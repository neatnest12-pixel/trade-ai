from __future__ import annotations


def test_startup_reconciliation_emits_progress_without_extra_scan(monkeypatch):
    collector = _load_collector_module()
    rows = [{'observation_id':str(i),'timestamp':f'row-{i:04d}'} for i in range(201)]
    monkeypatch.setattr(collector,'_iter_jsonl',lambda path:iter(rows))
    seen=[]
    result=collector.scan_observations(rebuild_index=False,heartbeat=seen.append)
    assert result['count']==201
    assert seen==[100,200]

import hashlib
import importlib.util
import json
from pathlib import Path


def _load_collector_module():
    path = Path("backend/scripts/v3_prospective_collector.py")
    spec = importlib.util.spec_from_file_location("v3_prospective_collector_test_module", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n", encoding="utf8")


def test_reconcile_counts_persisted_v34_outcomes_without_mutating_storage(tmp_path: Path) -> None:
    collector = _load_collector_module()
    obs = tmp_path / "obs.jsonl"
    outcomes = tmp_path / "outcomes.jsonl"
    collector.OBS = obs
    collector.OUTCOMES = outcomes
    collector.RUNTIME = tmp_path
    collector.INDEX = tmp_path / "index.sqlite3"
    v34 = {
        "observation_id": "v34-1",
        "timestamp": "2026-08-21T10:00:00+00:00",
        "symbol": "EURUSD",
        "collector_policy_version": collector.ACTIVE_COLLECTOR_POLICY_VERSION,
        "collector_policy_hash": collector.ACTIVE_COLLECTOR_POLICY_HASH,
    }
    legacy = {
        "observation_id": "legacy-1",
        "timestamp": "2026-08-20T10:00:00+00:00",
        "symbol": "EURUSD",
        "collector_policy_version": "legacy",
        "collector_policy_hash": "legacy",
    }
    _write_jsonl(obs, [legacy, v34])
    _write_jsonl(
        outcomes,
        [
            {"observation_id": "v34-1", "outcome_id": "a", "status": "RESOLVED"},
            {"observation_id": "v34-1", "outcome_id": "b", "status": "RESOLVED"},
            {"observation_id": "legacy-1", "outcome_id": "c", "status": "RESOLVED"},
            {"observation_id": "v34-1", "outcome_id": "p", "status": "PENDING"},
        ],
    )
    before = hashlib.sha256(outcomes.read_bytes()).hexdigest()

    state = collector.reconcile({})

    assert state["observations_count"] == 2
    assert state["resolved_outcomes"] == 0
    assert state["legacy_resolved_outcomes"] == 1
    assert state["pending"] == 0  # fixture has no eligible strategy signal
    assert state["resolved_outcomes_persisted"] == 0  # aliases now consistently describe certified outcomes
    assert state["pending_outcomes_persisted"] == 0
    assert state["all_v3_resolved_outcomes"] == 2
    assert state["v3_4_resolved_outcomes"] == 0
    assert hashlib.sha256(outcomes.read_bytes()).hexdigest() == before


def test_reconcile_fails_closed_on_malformed_outcome_records(tmp_path: Path) -> None:
    collector = _load_collector_module()
    obs = tmp_path / "obs.jsonl"
    outcomes = tmp_path / "outcomes.jsonl"
    collector.OBS = obs
    collector.OUTCOMES = outcomes
    collector.RUNTIME = tmp_path
    collector.INDEX = tmp_path / "index.sqlite3"
    _write_jsonl(
        obs,
        [
            {
                "observation_id": "v34-1",
                "timestamp": "2026-08-21T10:00:00+00:00",
                "symbol": "EURUSD",
                "collector_policy_version": collector.ACTIVE_COLLECTOR_POLICY_VERSION,
                "collector_policy_hash": collector.ACTIVE_COLLECTOR_POLICY_HASH,
            }
        ],
    )
    outcomes.write_text('{"observation_id":"v34-1","status":"RESOLVED"}\nnot-json\n[]\n', encoding="utf8")

    import pytest

    with pytest.raises(RuntimeError, match="MALFORMED_JSONL"):
        collector.reconcile({})
