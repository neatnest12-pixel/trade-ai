from __future__ import annotations

import json
import sys
import types
from datetime import UTC, datetime, timedelta

import pytest

from app.v3_5 import live_adapter as live
from app.v3_5.provenance import COHORT_VERSION, POLICY_HASH, connect, digest


def test_setup_classification_preserves_frozen_outputs_without_aggregation():
    assert live._decision_direction({"a": {"setup": False, "side": "NONE"}}) == "NO_SETUP"
    assert live._decision_direction({"a": {"setup": True, "side": "BUY"}}) == "BUY_SETUP"
    assert live._decision_direction({"a": {"setup": True, "side": "SELL"}}) == "SELL_SETUP"
    assert live._decision_direction({"a": {"setup": True, "side": "BUY"},
                                     "b": {"setup": True, "side": "SELL"}}) == "EXCLUDED"
    assert live._decision_direction({"a": {"setup": True, "side": "NONE"}}) == "EXCLUDED"


def test_continuous_identity_is_deterministic_and_namespaces_cohort_policy():
    opened = datetime(2026, 9, 30, 5, 8, tzinfo=UTC)
    identity = live._decision_identity(opened, continuous=True)
    assert identity == f"{COHORT_VERSION}|{POLICY_HASH}|EURUSD|M1|{opened.isoformat()}"
    assert live._decision_identity(opened, continuous=True) == identity
    assert live._decision_identity(opened, continuous=False) == f"eurusd-m1:{opened.isoformat()}"


def test_fresh_tick_wait_keeps_five_second_limit_and_rejects_future(monkeypatch):
    now = datetime(2026, 9, 30, 5, 10, tzinfo=UTC)
    monkeypatch.setattr(live, "_utc_now", lambda: now)

    class Tick:
        time = 0
        time_msc = int((now.timestamp() + 10800 - 4.9) * 1000)
        bid = 1.1
        ask = 1.1001

    class Source:
        def symbol_info_tick(self, symbol):
            return Tick()

    tick, acquired, normalized = live._fresh_tick(Source())
    assert tick is not None and acquired == now
    assert 4.89 <= (acquired - normalized).total_seconds() <= 4.91

    Tick.time_msc = int((now.timestamp() + 10800 + 0.1) * 1000)
    with pytest.raises(live.PreflightError, match="FUTURE_TICK"):
        live._fresh_tick(Source())


def test_system_preflight_fails_closed_on_mt5_disconnect(monkeypatch):

    class Disconnected:
        @staticmethod
        def terminal_info():
            return types.SimpleNamespace(connected=False)

        @staticmethod
        def account_info():
            raise AssertionError("disconnected MT5 must fail before account or market reads")

    with pytest.raises(live.PreflightError, match="MT5_DISCONNECTED_AT_STARTUP"):
        live._system_preflight(Disconnected())


def test_store_connection_uses_bounded_sqlite_cache_and_full_durability(tmp_path):
    db = connect(tmp_path / "bounded.sqlite3")
    assert db.execute("PRAGMA cache_size").fetchone()[0] == -4096
    assert db.execute("PRAGMA temp_store").fetchone()[0] == 1
    assert db.execute("PRAGMA synchronous").fetchone()[0] == 2
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert db.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    db.close()


def test_ema_lineage_cites_full_bounded_provider_window():
    evidence = {tf: [{"evidence_id": f"{tf}-{i}"} for i in range(80)] for tf in live.TIMEFRAMES}
    for tf in live.TIMEFRAMES:
        assert len(live._ancestors(evidence, f"trend_state.{tf}")) == 80
    continuation = live._ancestors(evidence, "continuation")
    assert len(continuation) == 320
    assert {item["evidence_id"] for item in continuation} == {
        f"{tf}-{i}" for tf in ("M1", "H1", "H6", "H12") for i in range(80)
    }
    assert live._required_lineage_counts("trend_state.M1") == {"M1": 80}
    assert live._required_lineage_counts("regime") == {"M1": 80, "M5": 80, "M15": 80, "H1": 80}


def test_incomplete_lineage_fails_closed_before_observation():
    incomplete = {"trend_state.M1": [{"kind": "bar", "timeframe": "M1",
                                     "provenance_status": "CERTIFIED", "evidence_id": str(i)} for i in range(22)]}
    with pytest.raises(live.PreflightError, match="INCOMPLETE_FEATURE_LINEAGE"):
        live._assert_feature_ancestor_manifest(incomplete)


def test_lock_refuses_live_owner_and_recovers_only_proven_stale_owner(tmp_path, monkeypatch):
    lock = tmp_path / "collector.lock"
    state = tmp_path / "collector_state.json"
    monkeypatch.setattr(live, "LOCK_PATH", lock)
    monkeypatch.setattr(live, "STATE_PATH", state)
    monkeypatch.setattr(live, "_pid_exists", lambda pid: True)
    lock.write_text(json.dumps({"pid": 987654, "worker_id": "live",
        "acquired_at_utc": (datetime.now(UTC) - timedelta(minutes=5)).isoformat(),
        "heartbeat_at_utc": (datetime.now(UTC) - timedelta(minutes=5)).isoformat()}))
    with pytest.raises(live.PreflightError, match="LOCK_OWNED"):
        live._acquire_collector_lock()
    assert lock.exists()

    monkeypatch.setattr(live, "_pid_exists", lambda pid: False)
    owner = live._acquire_collector_lock()
    assert owner["worker_id"] != "live"
    assert json.loads(lock.read_text())["worker_id"] == owner["worker_id"]


def test_heartbeat_writes_truthful_atomic_state(tmp_path, monkeypatch):
    monkeypatch.setattr(live.os, "getpid", lambda: 123)
    lock = tmp_path / "collector.lock"
    state_path = tmp_path / "collector_state.json"
    monkeypatch.setattr(live, "LOCK_PATH", lock)
    monkeypatch.setattr(live, "STATE_PATH", state_path)
    owner = {"pid": 123, "worker_id": "owner", "acquired_at_utc": datetime.now(UTC).isoformat(),
             "heartbeat_at_utc": datetime.now(UTC).isoformat()}
    lock.write_text(json.dumps(owner))
    live._write_heartbeat(owner, collector_id="v3.5-eurusd-continuous-r1", observations_count=2,
                           data_training_eligible_count=2, fit_model=0, order_send=0)
    state = json.loads(state_path.read_text())
    assert state["status"] == "RUNNING"
    assert state["pid"] == 123
    assert state["continuous_collection"] is True
    assert state["observations_count"] == 2
    assert state["fit_model"] == state["order_send"] == 0
    assert json.loads(lock.read_text())["worker_id"] == "owner"


@pytest.mark.parametrize("clock_pause", [False, True, "slow_audit_gap"])
def test_continuous_worker_records_three_distinct_cycles_and_safety_flags(tmp_path, monkeypatch, clock_pause):
    db_path = tmp_path / "cohort.sqlite3"
    db = connect(db_path)
    monkeypatch.setattr(live, "LOCK_PATH", tmp_path / "collector.lock")
    statuses = []
    owner = {"pid": 321, "worker_id": "test-owner", "acquired_at_utc": datetime.now(UTC).isoformat(),
             "heartbeat_at_utc": datetime.now(UTC).isoformat()}
    base = datetime(2026, 9, 30, 5, 10, tzinfo=UTC)
    opens = iter([base + timedelta(minutes=i) for i in ((1, 11, 12, 13) if clock_pause else (1, 2, 3))])
    anchors = []
    lifecycle = []
    clock_calls = [0]
    def clock(**kwargs):
        clock_calls[0] += 1
        if clock_pause is True and clock_calls[0] in (2, 3):
            raise live.ClockCertificationError("WINDOWS_TIME_STATUS_QUERY_FAILED", {"failed_subcheck":"WINDOWS_TIME_STATUS_QUERY_FAILED"})
        return {"clock_safe":True}
    monkeypatch.setattr(live, "ROOT", tmp_path)
    monkeypatch.setattr(live, "_lifecycle_state", lambda owner, **fields:lifecycle.append(fields))
    monkeypatch.setattr(live, "clock_evidence", clock)
    monkeypatch.setattr(live.time, "sleep", lambda seconds:None)
    monkeypatch.setattr(live, "_acquire_collector_lock", lambda: dict(owner))
    monkeypatch.setattr(live, "_write_state", lambda status, **fields: statuses.append((status, fields)))
    monkeypatch.setattr(live, "_write_heartbeat", lambda owner, **health: None)
    monkeypatch.setattr(live, "_install_sqlite_heartbeat", lambda db, owner: [])
    monkeypatch.setattr(live, "initialize_checked", lambda mt5: {"server": "ICMarketsSC-Demo"})
    monkeypatch.setattr(live, "shutdown", lambda mt5: {"status": "PASS"})
    monkeypatch.setattr(live, "_system_preflight", lambda mt5: {
        "latest_completed_m1_open_utc": base.isoformat(), "required_feature_count": 17})
    monkeypatch.setattr(live, "connect", lambda path: db)
    monkeypatch.setattr(live, "_database_size", lambda: 1024)
    monkeypatch.setattr(live, "_process_memory", lambda: {
        "working_set_bytes": 4096, "peak_working_set_bytes": 8192})
    latest = iter([base, base + timedelta(minutes=10)])
    monkeypatch.setattr(live, "_latest_completed_m1", lambda mt5:next(latest))
    def wait(mt5, after, **kw):
        anchors.append(after)
        opened=next(opens)
        if clock_pause=="slow_audit_gap" and len(anchors)==1:
            raise live.PreflightError("NON_CONSECUTIVE_COMPLETED_M1")
        return opened, {"bar_close_utc":(opened+timedelta(minutes=1)).isoformat()}
    monkeypatch.setattr(live,"_wait_completed_m1",wait)

    counter = {"i": 0}

    def collect(mt5, store, opened, info, cycle_number, *, continuous, heartbeat_callback):
        assert callable(heartbeat_callback)
        heartbeat_callback()
        counter["i"] += 1
        i = counter["i"]
        decision_id = live._decision_identity(opened, continuous=True)
        observation_id = f"observation-{i}"
        timestamp = (opened + timedelta(minutes=1, seconds=1)).isoformat()
        payload = {"direction": "NO_SETUP", "data_training_eligible": True}
        store.execute("INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            observation_id, decision_id, COHORT_VERSION, "policy", POLICY_HASH, "schema", "collector", timestamp,
            digest(payload), json.dumps(payload), 1, "[]", timestamp))
        store.commit()
        return {"observation_id": observation_id, "decision_id": decision_id, "idempotent_replay": False,
            "data_training_eligible": True, "direction": "NO_SETUP", "snapshot_sha256": digest(payload),
            "provider_latency_seconds": 0.01, "provenance_write_seconds": 0.02}

    monkeypatch.setattr(live, "_collect_cycle", collect)
    monkeypatch.setattr(live, "_verify_cycle_storage", lambda db, observation_id: {
        "provenance_ref_count": 18, "no_lookahead": True})
    fake_mt5 = types.ModuleType("MetaTrader5")
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake_mt5)

    result = live.run_continuous_collection(max_cycles_for_test=3)
    assert result == {"status": "STOPPED", "validation_cycles_observed": 3, "observations_count": 3}
    assert len({live._decision_identity(base + timedelta(minutes=i), continuous=True) for i in (1, 2, 3)}) == 3
    check = __import__("sqlite3").connect(db_path)
    assert check.execute("SELECT count(*) FROM observations").fetchone()[0] == 3
    assert check.execute("SELECT count(*) FROM outcomes").fetchone()[0] == 0
    if clock_pause:
        assert anchors[1]==base+timedelta(minutes=10)
        assert all("|"+(base+timedelta(minutes=i)).isoformat() not in row[0] for row in check.execute("select decision_id from observations") for i in range(1,11))
        assert any(x.get("status")=="CLOCK_UNCERTIFIED_TRANSIENT" for x in lifecycle)
        assert any(x.get("missed_bar_backfill") is False for x in lifecycle)
    assert [status for status, _ in statuses] == ["STARTING", "STOPPED"]
    assert __import__("os").environ["FIT_MODEL"] == "0"
    assert __import__("os").environ["ORDER_SEND"] == "0"
    check.close()
