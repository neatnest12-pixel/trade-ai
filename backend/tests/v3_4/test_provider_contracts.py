from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.v3_4.providers.contracts import (
    CommissionRecord,
    MacroEventRecord,
    Quality,
    central_bank_rate_record,
    cot_record,
    cross_asset_record,
    economic_surprise,
    execution_quality_contract,
    futures_record,
    unavailable,
    value_macro_contract,
    yield_record,
)
from app.v3_4.providers.provider_manifest import (
    DataDomain,
    ProviderQuality,
    fail_closed_check,
    provider_manifest,
    readiness_report,
)

NOW = datetime(2026, 8, 21, 6, 0, tzinfo=UTC)


def test_provider_manifest_priorities_and_quality() -> None:
    manifest = provider_manifest()
    domains = {item["data_domains"][0]: item for item in manifest}
    assert DataDomain.BROKER_COSTS.value in domains
    assert DataDomain.MACRO_CALENDAR.value in domains
    assert domains[DataDomain.BROKER_COSTS.value]["priority"] == "P0"
    assert domains[DataDomain.MACRO_CALENDAR.value]["priority"] == "P1"
    assert domains[DataDomain.FX_FUTURES.value]["priority"] == "P2"
    assert domains[DataDomain.BROKER_COSTS.value]["provenance_quality"] == ProviderQuality.VERIFIED.value


def test_commission_unavailable_and_normalized_record() -> None:
    missing = CommissionRecord("hash", "ICMarketsSC-Demo", "EURUSD", "1", NOW, 1.0, None, None, None, "MT5_HISTORY", NOW, Quality.UNAVAILABLE)
    assert missing.normalized()["state"] == "UNAVAILABLE"
    record = CommissionRecord("hash", "ICMarketsSC-Demo", "EURUSD", "2", NOW, 1.0, -3.5, "USD", -3.5, "BROKER_STATEMENT", NOW, Quality.VERIFIED)
    normalized = record.normalized()
    assert normalized["commission"] == -3.5
    assert normalized["quality"] == "VERIFIED"


def test_execution_slippage_latency_schema() -> None:
    schema = execution_quality_contract()
    assert schema["order_sent"] is False
    assert "latency_signal_to_send_ms" in schema
    assert "slippage_R" in schema


def test_macro_schema_and_economic_surprise() -> None:
    event = MacroEventRecord("e1", NOW, "US", "USD", "CPI", "HIGH", 3.1, 3.0, 2.9, None, "SCHEDULED", "TEST", NOW, Quality.HIGH)
    normalized = event.normalized()
    assert normalized["event_type"] == "CPI"
    assert economic_surprise(3.1, 3.0)["surprise_absolute"] == 0.10000000000000009
    assert economic_surprise(3.1, 3.0, sample_size=4, dispersion=0.2)["normalized_surprise"] == "UNAVAILABLE_INSUFFICIENT_HISTORY"


def test_rate_yield_cross_asset_futures_cot_and_value_contracts() -> None:
    assert central_bank_rate_record("FED", "USD", 5.5, NOW, 5.25, "TEST")["policy_state"] == "TIGHTENING"
    assert yield_record("US10Y", NOW, None, "NONE")["quality"] == "UNAVAILABLE"
    stale = cross_asset_record("DXY", NOW - timedelta(hours=1), 100.0, "TEST", decision_timestamp=NOW)
    assert stale["stale"] is True
    assert futures_record(NOW)["warning"] == "FUTURES_VOLUME != GLOBAL_SPOT_FX_VOLUME"
    assert cot_record(NOW - timedelta(days=7), NOW + timedelta(hours=1), NOW, "EUR")["available_at_decision"] is False
    assert value_macro_contract()["state"] == "VALUE_UNAVAILABLE"


def test_readiness_evaluator_distinguishes_engine_from_real_data() -> None:
    report = readiness_report()
    assert all(item["ENGINE_READY"] for item in report)
    macro = next(item for item in report if item["domain"] == DataDomain.MACRO_CALENDAR.value)
    broker = next(item for item in report if item["domain"] == DataDomain.BROKER_COSTS.value)
    assert macro["REAL_DATA_CONNECTED"] is False
    assert macro["P0/P1_BLOCKER"] is True
    assert broker["REAL_DATA_CONNECTED"] is True


def test_fail_closed_and_no_execution_keywords() -> None:
    assert fail_closed_check(unavailable("x")) is True
    root = Path(__file__).resolve().parents[3]
    files = list((root / "backend" / "app" / "v3_4" / "providers").glob("*.py"))
    joined = "\n".join(path.read_text(encoding="utf-8") for path in files)
    assert "order_send(" not in joined
    assert ".order_send" not in joined
    assert "fit_model(" not in joined
