from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from app.v3_5 import outcome_resolver as resolver
from app.v3_5.provenance import digest


def t(seconds: float) -> int:
    return int(datetime(2026, 9, 30, tzinfo=UTC).timestamp() * 1000 + seconds * 1000)


def setup(direction: str = "BUY_SETUP") -> dict:
    return {"setup_id":"setup-1","observation_id":"obs-1","decision_time_utc":datetime(2026,9,30,tzinfo=UTC).isoformat(),"direction":direction}


def resolve(direction: str, prices: list[tuple[float,float,float]], *, now_seconds: float = 30,
            stop_distance: float = .001) -> dict:
    ticks=[(t(sec),bid,ask,t(sec)+10_800_000) for sec,bid,ask in prices]
    return resolver.resolve_quotes(setup=setup(direction),ticks=ticks,stop_distance=stop_distance,
        stop_contract={"timeframe":"M15","period":14},now=datetime.fromtimestamp(t(now_seconds)/1000,UTC))


def test_buy_uses_ask_entry_and_bid_exit_and_first_target_hit():
    result=resolve("BUY_SETUP",[(1,1.1000,1.1002),(2,1.1013,1.1015),(3,1.0990,1.0992)])
    assert result["status"]=="WIN"
    assert result["entry"]["price"]==1.1002
    assert result["entry"]["bid"]==1.1000 and result["entry"]["ask"]==1.1002
    assert result["exit_price"]==1.1013
    assert result["gross_r"]==pytest.approx(1.1)
    assert result["exit_timestamp_ms"]>t(0)


def test_sell_uses_bid_entry_and_ask_exit():
    result=resolve("SELL_SETUP",[(1,1.1000,1.1002),(2,1.0987,1.0989)])
    assert result["status"]=="WIN"
    assert result["entry"]["price"]==1.1000
    assert result["exit_price"]==1.0989


def test_stop_loss_is_first_valid_event():
    result=resolve("BUY_SETUP",[(1,1.1000,1.1002),(2,1.0990,1.0992),(3,1.1020,1.1022)])
    assert result["status"]=="LOSS" and result["reason"]=="SL_HIT"


def test_timeout_mark_to_market_and_breakeven():
    decision=datetime(2026,9,30,tzinfo=UTC)
    ticks=[(t(1),1.1000,1.1002,t(1)+10_800_000)]
    for sec in range(301,21602,300):
        ticks.append((t(sec),1.1002,1.1004,t(sec)+10_800_000))
    result=resolver.resolve_quotes(setup=setup(),ticks=ticks,stop_distance=.001,stop_contract={},
        now=decision+timedelta(hours=6,minutes=2))
    assert result["status"]=="BREAKEVEN"
    assert result["reason"]=="TIMEOUT"
    assert result["gross_r"]==pytest.approx(0)


def test_timeout_can_preserve_spread_loss_without_calling_it_sl():
    decision=datetime(2026,9,30,tzinfo=UTC)
    ticks=[(t(1),1.1000,1.1002,t(1)+10_800_000)]
    for sec in range(301,21602,300):
        ticks.append((t(sec),1.1001,1.1003,t(sec)+10_800_000))
    result=resolver.resolve_quotes(setup=setup(),ticks=ticks,stop_distance=.001,stop_contract={},
        now=decision+timedelta(hours=6,minutes=2))
    assert result["status"]=="TIMEOUT"
    assert result["reason"]=="TIMEOUT"
    assert result["gross_r"]==pytest.approx(-.1)


def test_pending_entry_window_and_pending_horizon():
    d=datetime(2026,9,30,tzinfo=UTC)
    early=resolver.resolve_quotes(setup=setup(),ticks=[],stop_distance=.001,stop_contract={},now=d+timedelta(seconds=20))
    assert early["status"]=="PENDING"
    horizon=resolver.resolve_quotes(setup=setup(),ticks=[(t(1),1.1,1.1002,t(1)+10_800_000)],stop_distance=.001,
        stop_contract={},now=d+timedelta(hours=1))
    assert horizon["status"]=="PENDING"


def test_ambiguous_ohlc_is_never_guessed():
    result=resolver.aggregated_bar_resolution(direction="BUY_SETUP",high=1.102,low=1.099,stop=1.0995,target=1.101)
    assert result=={"status":"PENDING","reason":"AMBIGUOUS_INTRABAR_NO_TICK_CHRONOLOGY"}


def test_future_quotes_at_or_before_decision_are_ignored():
    result=resolve("BUY_SETUP",[(0,1.1012,1.1014),(1,1.1000,1.1002),(2,1.1013,1.1015)])
    assert result["status"]=="WIN"
    assert result["entry"]["timestamp_ms"]==t(1)
    assert result["exit_timestamp_ms"]==t(2)


def test_identical_millisecond_future_quotes_fail_closed():
    result=resolver.resolve_quotes(setup=setup(),ticks=[(t(1),1.1,1.1002),(t(1),1.1013,1.1015)],
        stop_distance=.001,stop_contract={},now=datetime.fromtimestamp(t(2)/1000,UTC))
    assert result["status"]=="INVALID"
    assert result["reason"]=="AMBIGUOUS_SAME_MILLISECOND_QUOTES"


def test_sidecar_append_only_fk_hash_and_duplicate_setup(tmp_path, monkeypatch):
    path=tmp_path/"outcomes.sqlite3"; db=resolver.outcome_store(path)
    setup_payload={"setup_id":"s1","observation_id":"o1","snapshot_sha256":"h","decision_time_utc":"d","direction":"BUY_SETUP","policy_version":"v","policy_hash":"p"}
    text=json.dumps(setup_payload,sort_keys=True,separators=(",",":"))
    db.execute("INSERT INTO source_observations VALUES(?,?,?,?,?)",("o1","h","d","BUY_SETUP","now"))
    db.execute("INSERT INTO setups VALUES(?,?,?,?,?,?,?,?,?)",("s1","o1","h","d","BUY_SETUP","v","p",text,"now"))
    payload={"outcome_id":"id","setup_id":"s1","decision_snapshot_modified":False,"no_lookahead_verified":True}
    encoded=json.dumps(payload,sort_keys=True,separators=(",",":"));h=hashlib.sha256(encoded.encode()).hexdigest()
    db.execute("INSERT INTO outcomes VALUES(?,?,?,?,?,?)",("id","s1","WIN",encoded,h,"now"));db.commit()
    with pytest.raises(sqlite3.IntegrityError):db.execute("UPDATE outcomes SET status='LOSS' WHERE outcome_id='id'")
    with pytest.raises(sqlite3.IntegrityError):db.execute("INSERT INTO outcomes VALUES(?,?,?,?,?,?)",("id2","s1","LOSS",encoded,h,"now"))
    with pytest.raises(sqlite3.IntegrityError):db.execute("DELETE FROM setups WHERE setup_id='s1'")
    db.close()


def test_raw_future_tick_window_is_durably_hashed_before_use(tmp_path):
    class FakeMT5:
        COPY_TICKS_ALL=0
        def copy_ticks_range(self,symbol,start,end,kind):
            return [{"time_msc":t(1)+10_800_000,"bid":1.1,"ask":1.1002}]
        def last_error(self):return None
    cache=resolver.cache_store(tmp_path/"cache.sqlite3");labels=resolver.outcome_store(tmp_path/"labels.sqlite3")
    source=resolver.ReadOnlyMT5Ticks(FakeMT5(),cache,labels)
    decision=datetime(2026,9,30,tzinfo=UTC); now=decision+timedelta(seconds=2)
    row=list(source.ticks(decision,now,now))
    assert len(row)==1 and row[0][0]==t(1)
    assert row[0][3]==t(1)+10_800_000
    evidence=labels.execute("SELECT payload,payload_sha256 FROM tick_evidence_windows").fetchone()
    assert hashlib.sha256(evidence[0].encode()).hexdigest()==evidence[1]
    cached=json.loads(evidence[0])[0]
    assert cached==[t(1),1.1,1.1002,t(1)+10_800_000]
    assert row[0][5]==evidence[1]
    cache.close();labels.close()


def test_scan_cursor_is_idempotent_and_non_directional_rows_are_ignored(tmp_path, monkeypatch):
    src=sqlite3.connect(tmp_path/"source.db")
    src.execute("CREATE TABLE observations(observation_id TEXT,decision_id TEXT,decision_time_utc TEXT,snapshot_sha256 TEXT,payload TEXT,eligible INTEGER)")
    labels=resolver.outcome_store(tmp_path/"labels.db")
    monkeypatch.setattr(resolver,"_verify_cycle_storage",lambda *_:{"ok":True})
    dt="2026-09-30T00:00:00+00:00"; decision="d1"
    snapshot={"decision_id":decision,"decision_time_utc":dt,"feature_provenance_refs":{},"feature_values":{},"decision_outputs":{},"training_eligible":True}
    snapshot["snapshot_sha256"]=digest(snapshot)
    row={"direction":"BUY_SETUP","training_eligible":True,"snapshot":snapshot}
    src.execute("INSERT INTO observations VALUES(?,?,?,?,?,1)",("o1",decision,dt,snapshot["snapshot_sha256"],json.dumps(row)))
    assert resolver.observe_setups(src,labels,datetime(2026,9,30,tzinfo=UTC))==1
    assert resolver.observe_setups(src,labels,datetime(2026,9,30,tzinfo=UTC))==0
    assert labels.execute("SELECT count(*) FROM setups").fetchone()[0]==1
    assert labels.execute("SELECT count(*) FROM progress WHERE status='PENDING'").fetchone()[0]==1
    assert labels.execute("PRAGMA foreign_key_check").fetchall()==[]
    assert src.execute("SELECT payload FROM observations").fetchone()[0]==json.dumps(row)
    labels.close();src.close()


def test_outcome_policy_is_single_reference_label_and_safety_disabled():
    assert resolver.RR_REFERENCE==1.0
    assert not resolver.SPLIT_PLAN["random_split"]
    assert resolver.SPLIT_PLAN["fit_model"]==0
    assert resolver.OUTCOME_DB!=resolver.STORE
    assert resolver.TICK_CACHE_DB!=resolver.OUTCOME_DB


def test_resolver_lock_rejects_second_live_owner(tmp_path,monkeypatch):
    lock=tmp_path/"resolver.lock";monkeypatch.setattr(resolver,"LOCK_PATH",lock)
    owner=resolver._acquire_lock(datetime.now(UTC))
    assert json.loads(lock.read_text())["worker_id"]==owner["worker_id"]
    with pytest.raises(RuntimeError,match="LOCK_HELD"):
        resolver._acquire_lock(datetime.now(UTC))


def test_resolver_heartbeat_is_independent_and_truthful(tmp_path,monkeypatch):
    monkeypatch.setattr(resolver,"STATE_PATH",tmp_path/"resolver_state.json")
    monkeypatch.setattr(resolver,"LOCK_PATH",tmp_path/"resolver.lock")
    monkeypatch.setattr(resolver,"OUTCOME_DB",tmp_path/"outcomes.sqlite3")
    monkeypatch.setattr(resolver,"TICK_CACHE_DB",tmp_path/"ticks.sqlite3")
    owner={"pid":123,"worker_id":"resolver-test","acquired_at_utc":datetime.now(UTC).isoformat()}
    resolver._write_json_atomic(resolver.LOCK_PATH,owner)
    state=resolver._state(owner,status="RUNNING",started="start",processed=3,resolved=1,pending=2,invalid=0,
        last_success="ok",last_error=None,cycle_duration=.1,memory={"working_set_bytes":10,"peak_working_set_bytes":20})
    lock=json.loads(resolver.LOCK_PATH.read_text())
    assert state["status"]=="RUNNING" and state["processed_count"]==3
    assert lock["pid"]==123 and lock["worker_id"]=="resolver-test"
    assert state["collector_id"]=="v3.5-eurusd-outcome-resolver-evidence-r2"


def test_process_memory_sampler_reports_working_and_peak_set():
    result=resolver._memory()
    assert result["working_set_bytes"] is not None and result["working_set_bytes"]>0
    assert result["peak_working_set_bytes"] is not None and result["peak_working_set_bytes"]>=result["working_set_bytes"]


def test_resolver_coexists_with_collector_without_writing_source(tmp_path,monkeypatch):
    runtime=tmp_path/"runtime"/"v35_provenance";runtime.mkdir(parents=True)
    monkeypatch.setattr(resolver,"ROOT",tmp_path)
    now=datetime.now(UTC).isoformat()
    state={"status":"RUNNING","pid":9001,"worker_id":"collector-owner","heartbeat_at_utc":now,
        "observations_count":1,"last_error":None}
    (runtime/"live_adapter_state.json").write_text(json.dumps(state))
    (runtime/"live_adapter.lock").write_text(json.dumps({"pid":9001,"worker_id":"collector-owner"}))
    src=sqlite3.connect(":memory:");src.execute("create table observations(observation_id text)");src.execute("insert into observations values('o1')");src.commit()
    before=src.total_changes
    monkeypatch.setattr(resolver,"_pid_exists",lambda pid:True)
    monkeypatch.setattr(resolver,"clock_evidence",lambda:{"clock_safe":True,"evidence_hash":"clock"})
    result=resolver.verify_collector_health(src)
    assert result["status"]=="PASS" and result["pid"]==9001
    assert src.total_changes==before
    assert not str(resolver.OUTCOME_DB).lower().startswith(str(tmp_path/"runtime"/"v34").lower())
    src.close()


def test_training_monitor_is_read_only_and_training_execution_stays_disabled(tmp_path):
    source=sqlite3.connect(":memory:");source.execute("create table observations(observation_id text,eligible integer,payload text)")
    labels=resolver.outcome_store(tmp_path/"monitor.sqlite3")
    before=(source.total_changes,labels.total_changes)
    report=resolver.training_admission_report(source,labels,datetime(2026,9,30,tzinfo=UTC))
    assert report["safe_to_train"] is False and report["fit_model"]==0 and report["order_send"]==0
    assert report["demo_execution"] is False and report["real_execution"] is False
    assert (source.total_changes,labels.total_changes)==before
    assert resolver.OUTCOME_DB!=resolver.STORE
    assert resolver.OUTCOME_DB.name!="v34_certified_outcomes.sqlite3"
    source.close();labels.close()

