from decimal import Decimal

from app.v1_1_child.contract import HASH as INR_HASH
from app.v1_1_usd.contract import (
    FROZEN_AT,
    HASH,
    REFERENCE_RISK_USD,
    VERSION,
    eligible,
    journal_fields,
)
from app.v1_1_usd.reporter import ending_balance_usd, pnl_usd
from app.v1_1_usd.risk_engine import size_usd

META = {"trade_tick_size": "0.00001", "trade_tick_value": "1", "volume_min": "0.01", "volume_step": "0.01", "volume_max": "200"}


def test_usd_policy_is_new_and_prospective() -> None:
    assert VERSION == "v1.1-paper-trade-usd-01" and HASH != INR_HASH and journal_fields()["reference_risk_usd"] == "0.30"
    row = {"type": "prediction", "v1_snapshot_id": "v1-paper-r4-0d891a2356e35464", "v1_config_hash": "0d891a2356e35464dbe3ba9f51c50fe240e40feff84fe4008d8b1564b55f9eac", "decision": "BUY", "candle_timestamp": FROZEN_AT}
    assert eligible([row]) == []


def test_usd_risk_rounding_and_minimum_lot_guard() -> None:
    ready = size_usd("1.1", "1.0999", "BUY", META, "USD")
    assert ready["status"] == "READY" and ready["actual_risk_usd"] <= REFERENCE_RISK_USD
    skipped = size_usd("1.1", "1.099", "BUY", {**META, "volume_min": "1"}, "USD")
    assert skipped["reason"] == "RISK_TOO_SMALL_FOR_MIN_LOT"


def test_usd_rr_amounts() -> None:
    assert [pnl_usd(Decimal(r)) for r in (2, 3, 5, 10)] == [Decimal(".60"), Decimal(".90"), Decimal("1.50"), Decimal("3.00")]
    assert ending_balance_usd(Decimal("2")) == Decimal("30.60")
