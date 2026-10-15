from decimal import Decimal

import pytest

from app.v1_1_child.contract import atr14


def candle(index: int, high: str, low: str, close: str) -> dict[str, str]:
    return {"timestamp": str(index), "high": high, "low": low, "close": close}


def test_golden_atr_uses_fourteen_true_ranges_from_fifteen_candles() -> None:
    candles = [candle(0, "101", "99", "100")]
    candles += [candle(i, "102", "100", "101") for i in range(1, 14)]
    candles.append(candle(14, "106", "104", "105"))
    # Thirteen normal ranges of 2, then a gap-up range of max(2, 5, 3) = 5.
    assert atr14(candles) == Decimal("31") / Decimal("14")


def test_latest_fifteen_window_and_gap_down() -> None:
    candles = [candle(0, "500", "499", "499.5")] + [candle(i, "101", "99", "100") for i in range(1, 15)]
    candles.append(candle(15, "96", "94", "95"))
    # The old first candle is outside the latest window; final gap-down TR is 6.
    assert atr14(candles) == Decimal("32") / Decimal("14")


def test_requires_fifteen_completed_candles() -> None:
    with pytest.raises(ValueError, match="INSUFFICIENT_ATR_HISTORY"):
        atr14([candle(i, "101", "99", "100") for i in range(14)])


def test_regression_contract_history_no_longer_raises_strict_zip_error() -> None:
    history = [candle(i, "101", "99", "100") for i in range(15)]
    assert atr14(history) == Decimal("2")
