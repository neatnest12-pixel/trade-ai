from __future__ import annotations

import json

import pytest

from app.v3_5 import state_io
from app.v3_5.worker_lifecycle import finalize_worker


def owner(pid: int = 7123, worker_id: str = "worker-a") -> dict[str, object]:
    return {"pid": pid, "worker_id": worker_id, "worker_identity": "test-worker"}


def test_terminal_state_is_published_after_protected_resources_close(tmp_path):
    lock = tmp_path / "worker.lock"
    lock.write_text(json.dumps(owner()))
    sequence: list[str] = []

    def publish(errors):
        sequence.append("publish")
        assert sequence[0] == "close-db"
        assert errors == []

    errors = finalize_worker(owner=owner(), lock_path=lock,
        cleanup=[("close-db", lambda: sequence.append("close-db")),
                 ("shutdown-provider", lambda: sequence.append("shutdown-provider"))],
        publish_terminal=publish, record_event=lambda event: sequence.append("event"))

    assert errors == []
    assert sequence == ["close-db", "shutdown-provider", "publish"]
    assert not lock.exists()


@pytest.mark.parametrize(("status", "reason"), [("STOPPED", "NORMAL_STOP"),
                                                   ("INTERRUPTED", "OPERATOR_INTERRUPT")])
def test_normal_and_interrupted_shutdown_publish_caller_selected_terminal_state(tmp_path, status, reason):
    lock = tmp_path / "worker.lock"
    lock.write_text(json.dumps(owner()))
    published = []

    def publish(errors):
        published.append({"status": status, "reason": reason, "cleanup_errors": errors})

    finalize_worker(owner=owner(), lock_path=lock, cleanup=[], publish_terminal=publish,
                    record_event=lambda _event: None)
    assert published == [{"status": status, "reason": reason, "cleanup_errors": []}]
    assert not lock.exists()


def test_cleanup_error_is_reported_without_replacing_original_reason(tmp_path):
    lock = tmp_path / "worker.lock"
    lock.write_text(json.dumps(owner()))
    events = []

    def bad_close():
        raise OSError("simulated close failure")

    def publish(errors):
        assert errors == [{"operation": "close-db", "error": "OSError:simulated close failure"}]
        events.append({"status": "INTERRUPTED", "reason": "OPERATOR_INTERRUPT", "cleanup_errors": errors})

    errors = finalize_worker(owner=owner(), lock_path=lock, cleanup=[("close-db", bad_close)],
        publish_terminal=publish, record_event=events.append)
    assert errors[0]["operation"] == "close-db"
    assert events[0]["reason"] == "OPERATOR_INTERRUPT"
    assert not lock.exists()


def test_failed_terminal_write_preserves_temp_and_retains_lock(tmp_path, monkeypatch):
    state = tmp_path / "worker_state.json"
    lock = tmp_path / "worker.lock"
    lock.write_text(json.dumps(owner()))
    state.write_text(json.dumps({"status": "RUNNING", **owner()}))
    original_replace = state_io.os.replace

    def fail_replace(source, target):
        if str(target) == str(state):
            raise PermissionError("simulated final state replacement failure")
        return original_replace(source, target)

    monkeypatch.setattr(state_io.os, "replace", fail_replace)
    events = []

    def publish(_errors):
        state_io.write_json_atomic(state, {"status": "STOPPED", **owner()}, preserve_failed_temp=True)

    result = finalize_worker(owner=owner(), lock_path=lock, cleanup=[], publish_terminal=publish,
        record_event=events.append)
    temps = list(tmp_path.glob(".worker_state.json.*.tmp"))
    assert len(temps) == 1
    assert json.loads(temps[0].read_text())["status"] == "STOPPED"
    assert json.loads(state.read_text())["status"] == "RUNNING"
    assert lock.exists()
    assert any(event["event"] == "WORKER_TERMINAL_STATE_WRITE_FAILED" for event in events)
    assert result[-1]["operation"] == "publish_terminal_state"


def test_lock_release_failure_is_visible_and_does_not_replace_terminal_result(tmp_path, monkeypatch):
    lock = tmp_path / "worker.lock"
    lock.write_text(json.dumps(owner()))
    published = []
    events = []
    original_unlink = type(lock).unlink

    def fail_unlink(path, *args, **kwargs):
        if path == lock:
            raise PermissionError("simulated lock release failure")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(type(lock), "unlink", fail_unlink)
    result = finalize_worker(owner=owner(), lock_path=lock, cleanup=[],
        publish_terminal=lambda errors: published.append(list(errors)), record_event=events.append)
    assert published == [[], [{"operation": "release_owned_lock",
                               "error": "PermissionError:simulated lock release failure"}]]
    assert lock.exists()
    assert result[-1]["operation"] == "release_owned_lock"
    assert events[0]["event"] == "WORKER_LOCK_RELEASE_FAILED"


def test_owner_mismatch_never_releases_another_workers_lock(tmp_path):
    lock = tmp_path / "worker.lock"
    lock.write_text(json.dumps(owner(pid=9999, worker_id="replacement")))
    events = []
    result = finalize_worker(owner=owner(), lock_path=lock, cleanup=[], publish_terminal=lambda _e: None,
        record_event=events.append)
    assert json.loads(lock.read_text())["worker_id"] == "replacement"
    assert result[-1]["operation"] == "release_owned_lock"
    assert events[0]["event"] == "WORKER_LOCK_RELEASE_FAILED"
    assert events[-1]["event"] == "WORKER_CLEANUP_ERROR_STATE_UPDATE_FAILED"


def test_repeated_shutdown_is_idempotent_after_owned_lock_was_released(tmp_path):
    lock = tmp_path / "worker.lock"
    lock.write_text(json.dumps(owner()))
    publish_count = 0

    def publish(_errors):
        nonlocal publish_count
        publish_count += 1

    kwargs = {"owner": owner(), "lock_path": lock, "cleanup": [], "publish_terminal": publish,
              "record_event": lambda _event: None}
    assert finalize_worker(**kwargs) == []
    assert finalize_worker(**kwargs) == []
    assert publish_count == 2
    assert not lock.exists()


def test_stale_temp_cleanup_requires_dead_pid_and_is_not_part_of_shutdown(tmp_path):
    lock = tmp_path / "worker.lock"
    lock.write_text(json.dumps(owner()))
    temp = tmp_path / ".worker_state.json.7123.failed.tmp"
    temp.write_text('{"status":"STOPPED"}')
    finalize_worker(owner=owner(), lock_path=lock, cleanup=[], publish_terminal=lambda _e: None,
                    record_event=lambda _event: None)
    assert temp.exists()
    state_io.cleanup_stale_temps(tmp_path / "worker_state.json", lambda pid: False)
    assert not temp.exists()


@pytest.mark.parametrize("worker", ["collector", "resolver"])
def test_worker_startup_preserves_orphaned_temp_evidence(tmp_path, monkeypatch, worker):
    from datetime import UTC, datetime

    from app.v3_5 import live_adapter, outcome_resolver

    state = tmp_path / "worker_state.json"
    lock = tmp_path / "worker.lock"
    state_temp = tmp_path / ".worker_state.json.4321.failed.tmp"
    lock_temp = tmp_path / ".worker.lock.4321.failed.tmp"
    state_temp.write_text('{"status":"STOPPED","worker_id":"old"}')
    lock_temp.write_text('{"pid":4321,"worker_id":"old"}')
    monkeypatch.setattr(live_adapter, "STATE_PATH", state)
    monkeypatch.setattr(live_adapter, "LOCK_PATH", lock)
    monkeypatch.setattr(outcome_resolver, "STATE_PATH", state)
    monkeypatch.setattr(outcome_resolver, "LOCK_PATH", lock)
    monkeypatch.setattr(live_adapter, "_pid_exists", lambda _pid: False)
    monkeypatch.setattr(outcome_resolver, "_pid_exists", lambda _pid: False)

    try:
        if worker == "collector":
            live_adapter._acquire_collector_lock()
        else:
            outcome_resolver._acquire_lock(datetime.now(UTC))
        assert state_temp.exists() and lock_temp.exists()
    finally:
        lock.unlink(missing_ok=True)


@pytest.mark.parametrize(("interrupt", "expected_status"), [(False, "STOPPED"),
                                                             (True, "INTERRUPTED")])
def test_resolver_publishes_terminal_state_only_after_mt5_shutdown(
        tmp_path, monkeypatch, interrupt, expected_status):
    import sys
    import types
    from datetime import UTC, datetime

    from app import mt5_connection
    from app.v3_5 import outcome_resolver as resolver

    monkeypatch.setattr(resolver, "ROOT", tmp_path)
    monkeypatch.setattr(resolver, "STORE", tmp_path / "source.sqlite3")
    monkeypatch.setattr(resolver, "OUTCOME_DB", tmp_path / "outcomes.sqlite3")
    monkeypatch.setattr(resolver, "TICK_CACHE_DB", tmp_path / "ticks.sqlite3")
    monkeypatch.setattr(resolver, "STATE_PATH", tmp_path / "resolver_state.json")
    monkeypatch.setattr(resolver, "LOCK_PATH", tmp_path / "resolver.lock")
    monkeypatch.setattr(resolver, "REPORT_PATH", tmp_path / "report.json")
    resolver.STATE_PATH.unlink(missing_ok=True)
    resolver.LOCK_PATH.unlink(missing_ok=True)
    resolver.STORE.touch()
    events = []
    fake_mt5 = types.ModuleType("MetaTrader5")
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake_mt5)
    monkeypatch.setattr(mt5_connection, "initialize_checked", lambda _mt5: {"server": "ICMarketsSC-Demo"})
    monkeypatch.setattr(mt5_connection, "shutdown", lambda _mt5: events.append("mt5-shutdown"))
    monkeypatch.setattr(resolver, "_broker_offset_seconds", lambda: 10800)
    now = datetime.now(UTC)
    monkeypatch.setattr(resolver, "_fresh_tick", lambda _mt5: (types.SimpleNamespace(bid=1.1, ask=1.1001), now, now))
    monkeypatch.setattr(resolver, "verify_collector_health", lambda *_a, **_k: {
        "status": "PASS", "pid": 1, "heartbeat_age_seconds": 0.1})
    monkeypatch.setattr(resolver, "clock_evidence", lambda: {"clock_safe": True})
    monkeypatch.setattr(resolver, "_verify_cycle_storage", lambda *_a, **_k: {"status": "PASS"})
    monkeypatch.setattr(resolver, "training_admission_report", lambda *_a, **_k: {})

    def cycle(*_args, **_kwargs):
        if interrupt:
            raise KeyboardInterrupt
        return {"processed": 0, "pending": 0}

    monkeypatch.setattr(resolver, "resolver_cycle", cycle)
    original_state = resolver._state

    def track_state(owner, **kwargs):
        state = original_state(owner, **kwargs)
        if state["status"] in {"STOPPED", "INTERRUPTED"}:
            events.append("terminal-state")
        return state

    monkeypatch.setattr(resolver, "_state", track_state)
    resolver.run_resolver(max_cycles_for_test=1)

    state = json.loads(resolver.STATE_PATH.read_text())
    assert state["status"] == expected_status
    assert events.index("mt5-shutdown") < events.index("terminal-state")
    assert not resolver.LOCK_PATH.exists()
