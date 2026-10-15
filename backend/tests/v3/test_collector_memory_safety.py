from __future__ import annotations

import hashlib
import json
import os
import tracemalloc
from pathlib import Path

import pytest

from backend.scripts import v3_prospective_collector as collector


def _configure(tmp_path: Path) -> Path:
    observation_path = tmp_path / "observations.jsonl"
    collector.OBS = observation_path
    collector.OUTCOMES = tmp_path / "outcomes.jsonl"
    collector.INDEX = tmp_path / "index.sqlite3"
    collector.LOCK = tmp_path / "collector.lock"
    return observation_path


def _row(number: int) -> dict[str, object]:
    return {
        "observation_id": f"id-{number}",
        "symbol": "EURUSD",
        "timestamp": f"2026-09-01T00:00:00+00:00#{number:08d}",
        "time_normalization_version": collector.TIME_NORMALIZATION_VERSION,
        "collector_policy_version": collector.ACTIVE_COLLECTOR_POLICY_VERSION,
        "collector_policy_hash": collector.ACTIVE_COLLECTOR_POLICY_HASH,
        "setup_states": {"TREND": number % 2 == 0},
        "padding": "x" * 2048,
    }


def _write(path: Path, count: int) -> None:
    with path.open("w", encoding="utf8", newline="\n") as handle:
        for number in range(count):
            handle.write(json.dumps(_row(number)) + "\n")


def test_large_jsonl_scan_is_memory_bounded_and_preserves_bytes(tmp_path: Path) -> None:
    path = _configure(tmp_path)
    _write(path, 12_000)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    tracemalloc.start()
    summary = collector.scan_observations()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert summary["count"] == 12_000
    assert summary["last_row"]["observation_id"] == "id-11999"
    assert peak < 12 * 1024 * 1024
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_malformed_or_partial_final_line_fails_closed(tmp_path: Path) -> None:
    path = _configure(tmp_path)
    path.write_bytes((json.dumps(_row(0)) + "\n{\"observation_id\":").encode())
    with pytest.raises(RuntimeError, match="PARTIAL_FINAL_JSONL_LINE"):
        collector.scan_observations()


def test_duplicate_identity_fails_closed(tmp_path: Path) -> None:
    path = _configure(tmp_path)
    row = _row(0)
    path.write_text(json.dumps(row) + "\n" + json.dumps({**row, "observation_id": "other"}) + "\n", encoding="utf8")
    with pytest.raises(RuntimeError, match="DUPLICATE_OBSERVATION"):
        collector.scan_observations()


def test_durable_append_updates_file_before_index_and_prevents_restart_duplicate(tmp_path: Path) -> None:
    path = _configure(tmp_path)
    path.touch()
    collector.scan_observations()
    row = _row(1)
    with collector._connect_index() as connection:
        collector._durable_append(row, connection)
        assert collector._index_has_id(connection, "id-1")
    before = path.read_bytes()
    collector.scan_observations()
    assert path.read_bytes() == before


def test_existing_lock_requires_external_ownership_audit(tmp_path: Path) -> None:
    _configure(tmp_path)
    collector.LOCK.write_text(json.dumps({"collector_id": "old", "pid": 99999999}), encoding="utf8")
    with pytest.raises(RuntimeError, match="OWNERSHIP_AUDIT"):
        collector.acquire_lock("new")
    assert json.loads(collector.LOCK.read_text())["collector_id"] == "old"
    collector.LOCK.write_text(json.dumps({"collector_id": "live", "pid": os.getpid()}), encoding="utf8")
    with pytest.raises(RuntimeError, match="OWNERSHIP_AUDIT"):
        collector.acquire_lock("other")


def test_policy_and_execution_invariants_unchanged() -> None:
    source = Path("backend/scripts/v3_prospective_collector.py").read_text(encoding="utf8")
    assert collector.ACTIVE_COLLECTOR_POLICY_VERSION == "v3.4-complete-fx-intelligence-r1"
    assert collector.ACTIVE_COLLECTOR_POLICY_HASH == "c056fac9dca4cf331dc94c52ea3e46dc7e6257e9fd0009506fbf808ce795ee58"
    assert "fit_model(" not in source
    assert "order_send(" not in source
