import json
from pathlib import Path

from backend.scripts.paper_report import EXPECTED_CONFIG_HASH, EXPECTED_SNAPSHOT, NOT_ENOUGH_DATA, analyze_records, read_jsonl


def prediction(ts, signal="BUY", **extra):
    return {"record_type": "prediction", "v1_snapshot_id": EXPECTED_SNAPSHOT, "v1_config_hash": EXPECTED_CONFIG_HASH, "symbol": "EURUSD", "timeframe": "1m", "candle_timestamp": ts, "signal": signal, **extra}


def test_empty_journal_is_not_enough_data():
    report = analyze_records([])
    assert report["unique_official_prediction_keys"] == 0
    assert report["expectancy"] == NOT_ENOUGH_DATA


def test_legacy_and_duplicate_are_excluded_from_official_counts():
    first = prediction("2026-01-01T00:00:00+00:00")
    report = analyze_records([{"record_type": "prediction", "signal": "SELL"}, first, dict(first)])
    assert report["legacy_pre_freeze_records"] == 1
    assert report["unique_official_prediction_keys"] == 1
    assert report["duplicate_restart_records"] == 1


def test_snapshot_or_config_mismatch_is_not_official():
    assert analyze_records([prediction("x", v1_snapshot_id="other")])["unique_official_prediction_keys"] == 0
    assert analyze_records([prediction("x", v1_config_hash="other")])["unique_official_prediction_keys"] == 0


def test_evaluation_metrics_and_unresolved_count():
    first, second = prediction("a"), prediction("b", "SELL")
    evaluation = {**first, "record_type": "evaluation", "result_r": 2.0}
    report = analyze_records([first, second, evaluation])
    assert report["completed_trades"] == 1 and report["unresolved"] == 1
    assert report["wins"] == 1 and report["expectancy"] == 2.0


def test_malformed_json_is_reported_without_mutation(tmp_path: Path):
    journal = tmp_path / "journal.jsonl"
    journal.write_text(json.dumps(prediction("x")) + "\n{bad}\n", encoding="utf-8")
    records, errors = read_jsonl(journal)
    assert len(records) == 1 and errors
