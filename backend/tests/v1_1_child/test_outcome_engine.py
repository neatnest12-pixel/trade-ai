from decimal import Decimal

import pytest

from app.v1_1_child.outcome_engine import resolve_rr_outcome


def contract(decision: str = "BUY", **changes: object) -> dict[str, object]:
    base: dict[str, object] = {
        "decision": decision, "entry_price": "100", "risk_distance": "10", "max_hold_m1": 60,
        "stop_price": "90" if decision == "BUY" else "110",
        "rr_2_target": "120" if decision == "BUY" else "80",
        "rr_3_target": "130" if decision == "BUY" else "70",
        "rr_5_target": "150" if decision == "BUY" else "50",
        "rr_10_target": "200" if decision == "BUY" else "0",
    }
    base.update(changes)
    return base


def candle(index: int, low: str = "95", high: str = "105", close: str = "100") -> dict[str, str]:
    return {"timestamp": f"2026-08-18T00:{index:02d}:00Z", "open": "100", "high": high, "low": low, "close": close}


@pytest.mark.parametrize(("profile", "target", "gross"), [("RR_2", "120", 2), ("RR_3", "130", 3), ("RR_5", "150", 5), ("RR_10", "200", 10)])
def test_buy_tp_and_rr_gross(profile: str, target: str, gross: int) -> None:
    result = resolve_rr_outcome(contract(), profile, [candle(1, high=target)])
    assert result["outcome"] == "TP_HIT"
    assert result["gross_r"] == Decimal(gross)


def test_buy_sl() -> None:
    result = resolve_rr_outcome(contract(), "RR_2", [candle(1, low="90")])
    assert result["outcome"] == "SL_HIT" and result["gross_r"] == Decimal("-1")


def test_sell_tp_and_sl() -> None:
    assert resolve_rr_outcome(contract("SELL"), "RR_2", [candle(1, low="80")])["outcome"] == "TP_HIT"
    assert resolve_rr_outcome(contract("SELL"), "RR_2", [candle(1, high="110")])["outcome"] == "SL_HIT"


@pytest.mark.parametrize("side,low,high", [("BUY", "90", "120"), ("SELL", "80", "110")])
def test_same_candle_is_stop_first(side: str, low: str, high: str) -> None:
    result = resolve_rr_outcome(contract(side), "RR_2", [candle(1, low=low, high=high)])
    assert result["outcome"] == "SL_HIT" and result["ambiguous_intrabar"] is True


@pytest.mark.parametrize(("side", "close", "expected"), [("BUY", "105", "0.5"), ("BUY", "95", "-0.5"), ("SELL", "95", "0.5"), ("SELL", "105", "-0.5")])
def test_expiry_gross_r(side: str, close: str, expected: str) -> None:
    result = resolve_rr_outcome(contract(side), "RR_2", [candle(i, close=close) for i in range(60)])
    assert result["outcome"] == "EXPIRED"
    assert result["holding_candles"] == 60 and result["exit_timestamp"] == candle(59)["timestamp"]
    assert result["gross_r"] == Decimal(expected)


def test_unresolved_when_less_than_sixty_candles() -> None:
    result = resolve_rr_outcome(contract(), "RR_2", [candle(i) for i in range(59)])
    assert result["outcome"] == "UNRESOLVED" and result["gross_r"] is None


def test_first_hit_and_no_lookahead() -> None:
    candles = [candle(i) for i in range(5)] + [candle(5, high="120")] + [{"invalid": "ignored"}]
    result = resolve_rr_outcome(contract(), "RR_2", candles)
    assert result["outcome"] == "TP_HIT" and result["holding_candles"] == 6


def test_expiry_uses_only_first_sixty_candles() -> None:
    candles = [candle(i) for i in range(60)] + [{"invalid": "ignored"}]
    assert resolve_rr_outcome(contract(), "RR_2", candles)["outcome"] == "EXPIRED"


@pytest.mark.parametrize("changes", [{"stop_price": "101"}, {"decision": "SELL", "stop_price": "99"}, {"risk_distance": "0"}, {"max_hold_m1": 0}])
def test_invalid_contract_levels_rejected(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        resolve_rr_outcome(contract(**changes), "RR_2", [])


def test_invalid_ohlc_and_order_rejected() -> None:
    with pytest.raises(ValueError):
        resolve_rr_outcome(contract(), "RR_2", [candle(1, low="110", high="100")])
    with pytest.raises(ValueError):
        resolve_rr_outcome(contract(), "RR_2", [candle(2), candle(1)])
