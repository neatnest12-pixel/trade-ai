from decimal import Decimal

import pytest

from app.v1_1_child.risk_engine import REFERENCE_RISK_INR, size_paper_position


@pytest.fixture
def eurusd_metadata() -> dict[str, str]:
    return {"point": "0.00001", "trade_tick_size": "0.00001", "trade_tick_value": "1",
            "trade_contract_size": "100000", "volume_min": "0.01", "volume_step": "0.01", "volume_max": "100"}


def size(metadata: dict[str, str], **changes: object) -> dict[str, object]:
    values: dict[str, object] = {"entry_price": "1.10000", "stop_price": "1.09998", "decision": "BUY",
                                 "symbol_metadata": metadata, "account_currency": "INR"}
    values.update(changes)
    return size_paper_position(**values)


def test_buy_and_sell_valid_risk(eurusd_metadata: dict[str, str]) -> None:
    buy = size(eurusd_metadata)
    sell = size(eurusd_metadata, entry_price="1.10000", stop_price="1.10002", decision="SELL")
    assert buy["status"] == sell["status"] == "READY"
    assert buy["actual_risk_inr"] <= REFERENCE_RISK_INR and sell["actual_risk_inr"] <= REFERENCE_RISK_INR


def test_exact_cap_raw_volume_and_inr_currency(eurusd_metadata: dict[str, str]) -> None:
    result = size(eurusd_metadata, entry_price="1.1", stop_price="1.09975")
    assert result["raw_volume"] == Decimal("1") and result["actual_risk_inr"] == REFERENCE_RISK_INR
    assert result["conversion_rate_used"] == Decimal("1")


def test_rounds_down_and_never_up(eurusd_metadata: dict[str, str]) -> None:
    result = size(eurusd_metadata, entry_price="1.1", stop_price="1.09900", symbol_metadata={**eurusd_metadata, "trade_tick_value": "6.756756756756756756756756757"})
    assert result["raw_volume"] == Decimal("0.03700000000000000000000000000")
    assert result["final_volume"] == Decimal("0.03") and result["actual_risk_inr"] < REFERENCE_RISK_INR


def test_min_lot_safe_and_unsafe(eurusd_metadata: dict[str, str]) -> None:
    metadata = {**eurusd_metadata, "trade_tick_value": "16.66666666666666666666666667"}
    safe = size(metadata, entry_price="1.1", stop_price="1.09900")
    unsafe = size(metadata, entry_price="1.1", stop_price="1.09700")
    assert safe["status"] == "READY" and safe["final_volume"] == Decimal("0.01")
    assert unsafe["status"] == "SKIPPED" and unsafe["reason"] == "RISK_TOO_SMALL_FOR_MIN_LOT"


def test_max_lot_cap_is_safe(eurusd_metadata: dict[str, str]) -> None:
    result = size(eurusd_metadata, entry_price="1.1", stop_price="1.099999", symbol_metadata={**eurusd_metadata, "volume_max": "2"})
    assert result["status"] == "READY" and result["final_volume"] == Decimal("2")


@pytest.mark.parametrize("field,value", [("trade_tick_size", "0"), ("trade_tick_value", "0"), ("volume_min", "0"), ("volume_step", "0")])
def test_invalid_metadata_fails_closed(eurusd_metadata: dict[str, str], field: str, value: str) -> None:
    result = size(eurusd_metadata, symbol_metadata={**eurusd_metadata, field: value})
    assert result["status"] == "SKIPPED"


@pytest.mark.parametrize(("decision", "stop"), [("BUY", "1.10001"), ("SELL", "1.09999")])
def test_invalid_entry_stop_fails_closed(eurusd_metadata: dict[str, str], decision: str, stop: str) -> None:
    assert size(eurusd_metadata, decision=decision, stop_price=stop)["reason"] == "INVALID_ENTRY_STOP"


def test_usd_requires_explicit_conversion(eurusd_metadata: dict[str, str]) -> None:
    missing = size(eurusd_metadata, account_currency="USD")
    converted = size(eurusd_metadata, account_currency="USD", inr_to_account_currency_rate="1")
    inverse = size(eurusd_metadata, account_currency="USD", account_currency_to_inr_rate="1")
    assert missing["reason"] == "RISK_FX_CONVERSION_UNAVAILABLE"
    assert converted["status"] == "READY" and converted["conversion_rate_used"] == Decimal("1")
    assert inverse["status"] == "READY" and inverse["conversion_rate_used"] == Decimal("1")


def test_tiny_and_large_stop_distances_remain_safe(eurusd_metadata: dict[str, str]) -> None:
    tiny = size(eurusd_metadata, entry_price="1.1", stop_price="1.099999")
    large = size(eurusd_metadata, entry_price="2", stop_price="1")
    assert tiny["actual_risk_inr"] <= REFERENCE_RISK_INR
    assert large["status"] == "SKIPPED" and large["actual_risk_inr"] > REFERENCE_RISK_INR


def test_zero_risk_distance_and_float_inputs_fail_safe(eurusd_metadata: dict[str, str]) -> None:
    zero = size(eurusd_metadata, stop_price="1.1")
    float_input = size(eurusd_metadata, entry_price=1.1, stop_price=1.09975)
    assert zero["reason"] == "INVALID_ENTRY_STOP"
    assert float_input["status"] == "READY" and float_input["actual_risk_inr"] <= REFERENCE_RISK_INR
