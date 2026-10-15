import json
from pathlib import Path
from datetime import UTC, datetime, timedelta

from app.v3_4.mt5_time import normalize_mt5_epoch
from app.v3_5.provenance import digest, freeze_decision, make_tick_evidence, validate_clock


MANIFEST = (
    Path(__file__).resolve().parents[3]
    / "runtime"
    / "v35_provenance"
    / "frozen_decision_feature_manifest.json"
)


def test_frozen_decision_manifest_is_deterministic_and_separates_diagnostics():
    payload = MANIFEST.read_bytes()
    manifest = json.loads(payload)
    assert payload.endswith(b"\n")
    assert manifest["manifest_version"] == "v3.5-frozen-decision-feature-manifest-r1"
    assert manifest["frozen_decision_semantics_modified"] is False
    required = [f for f in manifest["features"] if f["status"] == "REQUIRED"]
    diagnostic = [f for f in manifest["features"] if f["status"] == "DIAGNOSTIC_ONLY"]
    assert len(required) == manifest["required_feature_count"]
    assert len(diagnostic) == manifest["diagnostic_only_count"]
    assert manifest["macro_dependencies"] == []
    assert manifest["cross_asset_dependencies"] == []
    assert manifest["target_instrument"] == manifest["traded_symbol"] == "EURUSD"
    assert manifest["xauusd_classification"] == "UNUSED"
    assert manifest["xauusd_required_for_eurusd_decision"] is False
    assert manifest["xauusd_required_for_v35_collection"] is False
    assert manifest["xauusd_required_for_future_training"] is False
    assert not manifest["cross_asset_dependency_audit"]["XAUUSD"]["consumed_by_frozen_eurusd_decision"]
    assert all(f["fail_closed_behavior"] for f in required)


def test_mt5_time_msc_is_preserved_and_fixed_offset_is_applied_once():
    decision = datetime(2026, 9, 30, 4, 0, 0, tzinfo=UTC)
    raw_msc = int((decision.timestamp() + 10800 - 0.25) * 1000)
    evidence = make_tick_evidence(
        raw_epoch_seconds=raw_msc / 1000,
        configured_offset_seconds=10800,
        decision_time_utc=decision,
        acquired_at_utc=decision,
        bid=1.1,
        ask=1.1001,
        symbol="EURUSD",
        broker_server="ICMarketsSC-Demo",
        request_id="fixture",
        clock_evidence={"clock_safe": True},
    )
    assert evidence["raw_value"]["raw_epoch_seconds"] == raw_msc / 1000
    assert evidence["normalized_timestamp"] == normalize_mt5_epoch(
        raw_msc / 1000, offset_seconds=10800, source="EURUSD"
    ).timestamp.isoformat()
    assert abs((datetime.fromisoformat(evidence["normalized_timestamp"]) - decision).total_seconds() + 0.25) < 0.001
    # Passing a UTC-normalized epoch as though it were broker time shifts it
    # three hours into the past and the fixed freshness contract rejects it.
    double_normalized = make_tick_evidence(
        raw_epoch_seconds=decision.timestamp() - 0.25,
        configured_offset_seconds=10800,
        decision_time_utc=decision,
        acquired_at_utc=decision,
        bid=1.1,
        ask=1.1001,
        symbol="EURUSD",
        broker_server="ICMarketsSC-Demo",
        request_id="fixture-double",
        clock_evidence={"clock_safe": True},
    )
    assert "STALE_TICK" in double_normalized["exclusion_reasons"]


def test_clock_guard_fails_outside_existing_five_second_envelope():
    sampled = datetime(2026, 9, 30, 4, 0, 0, tzinfo=UTC)
    assert validate_clock(sampled_utc=sampled, system_utc=sampled + timedelta(seconds=5))["clock_safe"]
    assert not validate_clock(sampled_utc=sampled, system_utc=sampled + timedelta(seconds=5.001))["clock_safe"]


def test_snapshot_rejects_provenance_bound_to_a_different_decision_time():
    decision = datetime(2026, 9, 30, 4, 0, 0, tzinfo=UTC)
    value = 1.25
    evidence = {
        "provenance_status": "CERTIFIED",
        "information_available_at_utc": decision.isoformat(),
        "decision_time_utc": (decision - timedelta(seconds=1)).isoformat(),
        "value_sha256": digest(value),
        "evidence_id": "evidence-id",
        "kind": "derived",
        "exclusion_reasons": [],
    }
    snapshot = freeze_decision(
        decision_id="decision", decision_time_utc=decision,
        feature_set_version="manifest-r1", feature_values={"x": value},
        feature_evidence={"x": evidence}, required_features=["x"],
        clock_evidence={"clock_safe": True},
    )
    assert not snapshot["training_eligible"]
    assert "PROVENANCE_DECISION_TIME_MISMATCH:x" in snapshot["eligibility_reasons"]

