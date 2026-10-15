from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.v3.strategy_families import evaluate
from app.v3.strategy_inputs import (
    STRATEGY_INPUT_POLICY_HASH,
    STRATEGY_INPUT_POLICY_VERSION,
    StrategyCandle,
    build_strategy_input,
)


def candle(tf: str, minute: int, close: float, *, open_: float | None = None) -> StrategyCandle:
    opened = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=minute)
    start = close if open_ is None else open_
    high = max(start, close) + 0.0010
    low = min(start, close) - 0.0010
    return StrategyCandle(tf, opened, start, high, low, close)


def series(tf: str, step: int, count: int, start: float, delta: float) -> list[StrategyCandle]:
    return [candle(tf, i * step, start + delta * i, open_=start + delta * max(i - 1, 0)) for i in range(count)]


def base_candles(direction: str = "bullish") -> list[StrategyCandle]:
    delta = 0.002 if direction == "bullish" else -0.002
    candles: list[StrategyCandle] = []
    candles.extend(series("M1", 1, 35, 1.10, delta / 10))
    candles.extend(series("M5", 5, 35, 1.10, delta / 6))
    candles.extend(series("M15", 15, 35, 1.10, delta / 4))
    candles.extend(series("H1", 60, 35, 1.10, delta))
    candles.extend(series("H6", 360, 35, 1.10, delta * 2))
    candles.extend(series("H12", 720, 35, 1.10, delta * 3))
    return candles


def test_real_payload_policy_provenance_and_no_placeholder_neutral() -> None:
    payload = build_strategy_input(base_candles("bullish"), datetime(2026, 1, 20, tzinfo=UTC))

    assert payload["strategy_input_policy_version"] == STRATEGY_INPUT_POLICY_VERSION
    assert len(STRATEGY_INPUT_POLICY_HASH) == 64
    assert all(payload["contexts"][tf]["available"] for tf in ("M1", "M5", "M15", "H1", "H6", "H12"))
    assert all(v["provenance"] == "DIRECT_MT5" for v in payload["feature_provenance"].values())
    assert set(payload["states"].values()) != {"NEUTRAL"}
    assert payload["alignment"]["BULLISH_TIMEFRAME_COUNT"] >= 1


def test_completed_candle_safety_excludes_incomplete_higher_timeframe() -> None:
    candles = base_candles("bullish")
    future_h12 = candle("H12", 60 * 24, 1.30)
    payload = build_strategy_input([*candles, future_h12], datetime(2026, 1, 20, tzinfo=UTC))

    assert payload["contexts"]["H12"]["source_candle_timestamp"] != future_h12.timestamp.isoformat()
    assert payload["contexts"]["H12"]["available"]


def test_trend_buy_sell_and_false_from_real_payload() -> None:
    buy = build_strategy_input(base_candles("bullish"), datetime(2026, 1, 20, tzinfo=UTC))
    sell = build_strategy_input(base_candles("bearish"), datetime(2026, 1, 20, tzinfo=UTC))
    short_history = build_strategy_input(base_candles("bullish")[:3], datetime(2026, 1, 1, 1, tzinfo=UTC))

    assert evaluate(buy, "TREND").side == "BUY"
    assert evaluate(sell, "TREND").side == "SELL"
    assert not evaluate(short_history, "TREND").setup


def test_breakout_buy_sell_and_false_from_real_payload() -> None:
    buy = build_strategy_input(base_candles("bullish"), datetime(2026, 1, 20, tzinfo=UTC))
    sell = build_strategy_input(base_candles("bearish"), datetime(2026, 1, 20, tzinfo=UTC))
    false_payload = build_strategy_input(base_candles("bullish"), datetime(2026, 1, 2, tzinfo=UTC))
    buy["close"] = buy["prior_high"] + 0.0001
    sell["close"] = sell["prior_low"] - 0.0001

    assert evaluate(buy, "BREAKOUT").side == "BUY"
    assert evaluate(sell, "BREAKOUT").side == "SELL"
    assert not evaluate(false_payload, "BREAKOUT").setup


def test_pullback_buy_sell_and_false_from_real_payload() -> None:
    buy = build_strategy_input(base_candles("bullish"), datetime(2026, 1, 20, tzinfo=UTC))
    sell = build_strategy_input(base_candles("bearish"), datetime(2026, 1, 20, tzinfo=UTC))
    buy["m1_return"] = -0.001
    buy["continuation"] = True
    sell["m1_return"] = 0.001
    sell["continuation"] = True

    assert evaluate(buy, "PULLBACK").side == "BUY"
    assert evaluate(sell, "PULLBACK").side == "SELL"
    assert not evaluate(build_strategy_input(base_candles("bullish"), datetime(2026, 1, 20, tzinfo=UTC)), "PULLBACK").setup


def test_mean_reversion_buy_sell_and_false_from_real_payload() -> None:
    buy = build_strategy_input(base_candles("bullish"), datetime(2026, 1, 20, tzinfo=UTC))
    sell = build_strategy_input(base_candles("bearish"), datetime(2026, 1, 20, tzinfo=UTC))
    for payload, ret in ((buy, -0.001), (sell, 0.001)):
        payload["regime"] = "RANGE"
        payload["deviation"] = 10 * payload["normalized_atr"]
        payload["m1_return"] = ret

    assert evaluate(buy, "MEAN_REVERSION").side == "BUY"
    assert evaluate(sell, "MEAN_REVERSION").side == "SELL"
    assert not evaluate(build_strategy_input(base_candles("bullish"), datetime(2026, 1, 20, tzinfo=UTC)), "MEAN_REVERSION").setup


def test_volatility_buy_sell_and_false_from_real_payload() -> None:
    buy = build_strategy_input(base_candles("bullish"), datetime(2026, 1, 20, tzinfo=UTC))
    sell = build_strategy_input(base_candles("bearish"), datetime(2026, 1, 20, tzinfo=UTC))
    buy["range_expansion"] = True
    sell["range_expansion"] = True

    assert evaluate(buy, "VOLATILITY").side == "BUY"
    assert evaluate(sell, "VOLATILITY").side == "SELL"
    assert not evaluate(build_strategy_input(base_candles("bullish"), datetime(2026, 1, 20, tzinfo=UTC)), "VOLATILITY").setup
