from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.v3_2.confluence import (
    PATTERN_IDS,
    POLICY_HASH,
    POLICY_VERSION,
    Candle,
    TickQuote,
    bos,
    breakout_buildup,
    build_confluence_vector,
    choch,
    completed_candles,
    detect_candlestick_patterns,
    fair_value_gaps,
    false_breakouts,
    fibonacci,
    indicators,
    liquidity,
    liquidity_sweeps,
    m5_liquidity_sweep_break,
    market_structure,
    order_blocks,
    retests,
    session_context,
    shadow_rr_matrix,
    supply_demand_zones,
    support_resistance,
    swing_points,
)

BASE = datetime(2026, 8, 20, 8, 0, tzinfo=UTC)


def _candles(count: int = 80, start: float = 1.10, step: float = 0.0002) -> list[Candle]:
    candles: list[Candle] = []
    price = start
    for idx in range(count):
        direction = 1 if idx % 6 in (0, 1, 2, 3) else -1
        open_price = price
        close = open_price + direction * step
        high = max(open_price, close) + step * 1.5
        low = min(open_price, close) - step * 1.5
        candles.append(
            Candle(
                timestamp=BASE + timedelta(minutes=idx),
                open=open_price,
                high=high,
                low=low,
                close=close,
                tick_volume=100 + idx,
                spread=2,
            )
        )
        price = close
    return candles


def _tf_candles(minutes: int, count: int = 80) -> list[Candle]:
    candles = _candles(count)
    return [
        Candle(
            timestamp=BASE + timedelta(minutes=idx * minutes),
            open=candle.open,
            high=candle.high,
            low=candle.low,
            close=candle.close,
            tick_volume=candle.tick_volume,
            spread=candle.spread,
        )
        for idx, candle in enumerate(candles)
    ]


def test_completed_candles_reject_future_and_forming() -> None:
    candles = _candles(3)
    decision = candles[-1].timestamp
    done = completed_candles(candles, "M1", decision)
    assert [c.timestamp for c in done] == [candles[0].timestamp, candles[1].timestamp]


def test_all_candlestick_pattern_ids_are_supported() -> None:
    assert len(PATTERN_IDS) == 29
    candles = [
        Candle(BASE, 1.1, 1.101, 1.099, 1.1001),
        Candle(BASE + timedelta(minutes=1), 1.1002, 1.1003, 1.099, 1.10025),
        Candle(BASE + timedelta(minutes=2), 1.1001, 1.102, 1.0998, 1.1018),
    ]
    patterns = detect_candlestick_patterns(candles, "M1")
    assert patterns
    assert {"pattern_id", "body_size", "upper_wick", "lower_wick", "candidate_direction"} <= set(patterns[0])


def test_structure_liquidity_retest_and_context_engines() -> None:
    candles = _candles(90)
    levels = support_resistance(candles, candles[-1].timestamp + timedelta(minutes=1))
    zones = supply_demand_zones(candles)
    liq = liquidity(candles)

    assert swing_points(candles)
    assert market_structure(candles)["state"] in {"BULLISH_STRUCTURE", "BEARISH_STRUCTURE", "RANGE_STRUCTURE", "TRANSITION", "UNKNOWN"}
    assert isinstance(bos(candles), list)
    assert isinstance(choch(candles), list)
    assert levels
    assert isinstance(zones, list)
    assert isinstance(retests(candles, levels, zones), list)
    assert isinstance(liq, list)
    assert isinstance(liquidity_sweeps(candles, liq), list)
    assert isinstance(m5_liquidity_sweep_break(candles), list)
    assert isinstance(breakout_buildup(candles, levels), list)
    assert isinstance(false_breakouts(candles, levels), list)
    assert isinstance(fair_value_gaps(candles), list)
    assert isinstance(order_blocks(candles, bos(candles)), list)
    assert fibonacci(candles)
    assert indicators(candles)["EMA"] > 0
    assert session_context(candles[-1].timestamp, candles)["session"]


def test_explicit_fvg_false_breakout_and_shadow_rr() -> None:
    candles = [
        Candle(BASE, 1.1000, 1.1010, 1.0995, 1.1008),
        Candle(BASE + timedelta(minutes=1), 1.1009, 1.1014, 1.1006, 1.1012),
        Candle(BASE + timedelta(minutes=2), 1.1020, 1.1030, 1.1018, 1.1028),
        Candle(BASE + timedelta(minutes=3), 1.1027, 1.1040, 1.1007, 1.1009),
    ]
    levels = [{"level_type": "recent swing resistance", "level_price": 1.1030, "level_created_at": BASE.isoformat(), "broken": False}]
    assert any(item["type"] == "BULLISH_FVG" for item in fair_value_gaps(candles))
    assert any(item["trap"] == "BULL_TRAP" for item in false_breakouts(candles, levels))
    setups = [{"setup_id": "s1", "direction": "BUY", "timestamp": BASE.isoformat()}]
    rr = shadow_rr_matrix(setups, bid=1.1000, ask=1.1002)
    assert {item["RR"] for item in rr} == {1.0, 1.5, 2.0, 3.0, 5.0}
    assert all(item["commission_R"] == "COMMISSION_UNAVAILABLE" for item in rr)


def test_confluence_vector_all_six_timeframes_no_lookahead_and_news_unavailable() -> None:
    candles_by_tf = {
        "M1": _tf_candles(1, 100),
        "M5": _tf_candles(5, 100),
        "M15": _tf_candles(15, 100),
        "H1": _tf_candles(60, 100),
        "H6": _tf_candles(360, 100),
        "H12": _tf_candles(720, 100),
    }
    decision = candles_by_tf["H12"][-1].timestamp + timedelta(hours=12)
    vector = build_confluence_vector(candles_by_tf, decision, TickQuote(decision - timedelta(seconds=1), 1.1, 1.1002))

    assert vector["policy_version"] == POLICY_VERSION
    assert vector["policy_hash"] == POLICY_HASH
    assert set(vector["candlestick_patterns"]) == {"M1", "M5", "M15", "H1", "H6", "H12"}
    assert vector["news_context"]["state"] == "NEWS_DATA_UNAVAILABLE"
    assert vector["MTF_alignment"]["alignment_state"] in {
        "FULL_BULLISH",
        "MAJORITY_BULLISH",
        "MIXED",
        "MAJORITY_BEARISH",
        "FULL_BEARISH",
    }
    assert vector["commission"] == "COMMISSION_UNAVAILABLE"


def test_confluence_vector_rejects_future_tick() -> None:
    candles = _candles(30)
    decision = candles[-1].timestamp + timedelta(minutes=1)
    candles_by_tf = {tf: candles for tf in ("M1", "M5", "M15", "H1", "H6", "H12")}
    with pytest.raises(ValueError, match="future tick"):
        build_confluence_vector(candles_by_tf, decision, TickQuote(decision + timedelta(seconds=1), 1.1, 1.1002))


def test_order_send_absent_from_v3_2_code() -> None:
    root = Path(__file__).resolve().parents[3]
    files = list((root / "backend" / "app" / "v3_2").glob("*.py")) + list((root / "backend" / "scripts").glob("v3_2*.py"))
    joined = "\n".join(path.read_text(encoding="utf-8") for path in files)
    assert "order_send" not in joined
