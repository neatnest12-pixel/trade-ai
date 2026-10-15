from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, time, timedelta
from statistics import mean
from typing import Any

POLICY_VERSION = "v3.2-confluence-engine-r1"
POLICY_HASH = hashlib.sha256(POLICY_VERSION.encode("utf-8")).hexdigest()
FEATURE_VERSION = "v3.2-confluence-features-r1"
COLLECTOR_VERSION = "v3.2-shadow-collector-r1"

TIMEFRAME_MINUTES = {
    "M1": 1,
    "M5": 5,
    "M15": 15,
    "H1": 60,
    "H6": 360,
    "H12": 720,
}
RR_MATRIX = (1.0, 1.5, 2.0, 3.0, 5.0)

PATTERN_IDS = (
    "DRAGONFLY_DOJI",
    "GRAVESTONE_DOJI",
    "DOJI",
    "HAMMER",
    "INVERTED_HAMMER",
    "HANGING_MAN",
    "SHOOTING_STAR",
    "BULLISH_ENGULFING",
    "BEARISH_ENGULFING",
    "BULLISH_HARAMI",
    "BEARISH_HARAMI",
    "MORNING_STAR",
    "EVENING_STAR",
    "PIERCING_LINE",
    "DARK_CLOUD_COVER",
    "THREE_WHITE_SOLDIERS",
    "THREE_BLACK_CROWS",
    "THREE_INSIDE_UP",
    "THREE_INSIDE_DOWN",
    "THREE_OUTSIDE_UP",
    "THREE_OUTSIDE_DOWN",
    "INSIDE_BAR",
    "OUTSIDE_BAR",
    "BULLISH_ABANDONED_BABY",
    "BEARISH_ABANDONED_BABY",
    "BULLISH_REJECTION",
    "BEARISH_REJECTION",
    "MOMENTUM_BULLISH_CANDLE",
    "MOMENTUM_BEARISH_CANDLE",
)


@dataclass(frozen=True)
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    tick_volume: float = 0
    spread: float | None = None
    real_volume: float = 0

    def validate(self) -> None:
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("invalid candle OHLC")
        if self.high < self.low:
            raise ValueError("invalid candle range")


@dataclass(frozen=True)
class TickQuote:
    timestamp: datetime
    bid: float
    ask: float
    provenance: str = "MT5_TICK"

    def validate(self, decision_timestamp: datetime) -> None:
        if self.bid <= 0 or self.ask <= 0 or self.ask < self.bid:
            raise ValueError("invalid bid/ask")
        if self.timestamp > decision_timestamp:
            raise ValueError("future tick in decision features")


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def completed_candles(candles: list[Candle], timeframe: str, decision_timestamp: datetime) -> list[Candle]:
    cutoff = _utc(decision_timestamp)
    delta = timedelta(minutes=TIMEFRAME_MINUTES[timeframe])
    out: list[Candle] = []
    for candle in sorted(candles, key=lambda c: c.timestamp):
        candle.validate()
        if _utc(candle.timestamp) + delta <= cutoff:
            out.append(candle)
    return out


def candle_metrics(candle: Candle, atr: float | None = None) -> dict[str, float]:
    rng = max(candle.high - candle.low, 0.0)
    body = abs(candle.close - candle.open)
    upper = candle.high - max(candle.open, candle.close)
    lower = min(candle.open, candle.close) - candle.low
    safe_range = rng or 1e-12
    return {
        "body_size": body,
        "upper_wick": upper,
        "lower_wick": lower,
        "range": rng,
        "body_to_range_ratio": body / safe_range,
        "upper_wick_ratio": upper / safe_range,
        "lower_wick_ratio": lower / safe_range,
        "ATR_normalized_range": rng / atr if atr and atr > 0 else 0.0,
    }


def true_ranges(candles: list[Candle]) -> list[float]:
    ranges: list[float] = []
    previous_close: float | None = None
    for candle in candles:
        if previous_close is None:
            ranges.append(candle.high - candle.low)
        else:
            ranges.append(max(candle.high - candle.low, abs(candle.high - previous_close), abs(candle.low - previous_close)))
        previous_close = candle.close
    return ranges


def atr(candles: list[Candle], period: int = 14) -> float | None:
    if len(candles) < period + 1:
        return None
    return mean(true_ranges(candles)[-period:])


def _direction(candle: Candle) -> str:
    if candle.close > candle.open:
        return "BULLISH"
    if candle.close < candle.open:
        return "BEARISH"
    return "NEUTRAL"


def _pattern(pattern_id: str, timeframe: str, candle: Candle, direction: str, atr_value: float | None) -> dict[str, Any]:
    return {
        "pattern_id": pattern_id,
        "pattern_version": POLICY_VERSION,
        "timeframe": timeframe,
        "timestamp": _utc(candle.timestamp).isoformat(),
        "open": candle.open,
        "high": candle.high,
        "low": candle.low,
        "close": candle.close,
        **candle_metrics(candle, atr_value),
        "candidate_direction": direction,
    }


def detect_candlestick_patterns(candles: list[Candle], timeframe: str) -> list[dict[str, Any]]:
    if not candles:
        return []
    atr_value = atr(candles)
    latest = candles[-1]
    previous = candles[-2] if len(candles) >= 2 else None
    third = candles[-3] if len(candles) >= 3 else None
    m = candle_metrics(latest, atr_value)
    body = m["body_size"]
    rng = m["range"] or 1e-12
    out: list[dict[str, Any]] = []

    def add(pid: str, direction: str = "NEUTRAL") -> None:
        out.append(_pattern(pid, timeframe, latest, direction, atr_value))

    if body / rng <= 0.1:
        add("DOJI")
        if m["lower_wick_ratio"] >= 0.6:
            add("DRAGONFLY_DOJI", "BULLISH")
        if m["upper_wick_ratio"] >= 0.6:
            add("GRAVESTONE_DOJI", "BEARISH")
    if m["lower_wick_ratio"] >= 0.55 and m["body_to_range_ratio"] <= 0.35:
        add("HAMMER", "BULLISH")
        add("HANGING_MAN", "BEARISH")
        add("BULLISH_REJECTION", "BULLISH")
    if m["upper_wick_ratio"] >= 0.55 and m["body_to_range_ratio"] <= 0.35:
        add("INVERTED_HAMMER", "BULLISH")
        add("SHOOTING_STAR", "BEARISH")
        add("BEARISH_REJECTION", "BEARISH")
    if body / rng >= 0.65:
        add("MOMENTUM_BULLISH_CANDLE" if latest.close > latest.open else "MOMENTUM_BEARISH_CANDLE", _direction(latest))
    if previous:
        latest_bull = latest.close > latest.open
        previous_bull = previous.close > previous.open
        latest_body_low, latest_body_high = sorted((latest.open, latest.close))
        prev_body_low, prev_body_high = sorted((previous.open, previous.close))
        if latest_bull and not previous_bull and latest_body_low <= prev_body_low and latest_body_high >= prev_body_high:
            add("BULLISH_ENGULFING", "BULLISH")
            add("THREE_OUTSIDE_UP", "BULLISH")
        if not latest_bull and previous_bull and latest_body_low <= prev_body_low and latest_body_high >= prev_body_high:
            add("BEARISH_ENGULFING", "BEARISH")
            add("THREE_OUTSIDE_DOWN", "BEARISH")
        if latest_body_low >= prev_body_low and latest_body_high <= prev_body_high:
            add("BULLISH_HARAMI" if latest_bull else "BEARISH_HARAMI", "BULLISH" if latest_bull else "BEARISH")
        if latest.high < previous.high and latest.low > previous.low:
            add("INSIDE_BAR")
        if latest.high > previous.high and latest.low < previous.low:
            add("OUTSIDE_BAR")
        if latest_bull and not previous_bull and latest.open < previous.close and latest.close > (previous.open + previous.close) / 2:
            add("PIERCING_LINE", "BULLISH")
        if not latest_bull and previous_bull and latest.open > previous.close and latest.close < (previous.open + previous.close) / 2:
            add("DARK_CLOUD_COVER", "BEARISH")
    if third and previous:
        dirs = [_direction(third), _direction(previous), _direction(latest)]
        if dirs == ["BEARISH", "NEUTRAL", "BULLISH"] or (third.close < third.open and latest.close > latest.open and latest.close > (third.open + third.close) / 2):
            add("MORNING_STAR", "BULLISH")
            add("THREE_INSIDE_UP", "BULLISH")
        if dirs == ["BULLISH", "NEUTRAL", "BEARISH"] or (third.close > third.open and latest.close < latest.open and latest.close < (third.open + third.close) / 2):
            add("EVENING_STAR", "BEARISH")
            add("THREE_INSIDE_DOWN", "BEARISH")
        if all(c.close > c.open for c in [third, previous, latest]) and latest.close > previous.close > third.close:
            add("THREE_WHITE_SOLDIERS", "BULLISH")
        if all(c.close < c.open for c in [third, previous, latest]) and latest.close < previous.close < third.close:
            add("THREE_BLACK_CROWS", "BEARISH")
        if previous.low > third.high and latest.open > previous.close:
            add("BULLISH_ABANDONED_BABY", "BULLISH")
        if previous.high < third.low and latest.open < previous.close:
            add("BEARISH_ABANDONED_BABY", "BEARISH")
    return out


def swing_points(candles: list[Candle], lookback: int = 2) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for idx in range(lookback, len(candles) - lookback):
        window = candles[idx - lookback : idx + lookback + 1]
        candle = candles[idx]
        if candle.high == max(c.high for c in window):
            points.append({"type": "SWING_HIGH", "timestamp": _utc(candle.timestamp).isoformat(), "price": candle.high})
        if candle.low == min(c.low for c in window):
            points.append({"type": "SWING_LOW", "timestamp": _utc(candle.timestamp).isoformat(), "price": candle.low})
    return points


def market_structure(candles: list[Candle]) -> dict[str, Any]:
    points = swing_points(candles)
    highs = [p for p in points if p["type"] == "SWING_HIGH"][-3:]
    lows = [p for p in points if p["type"] == "SWING_LOW"][-3:]
    labels: list[str] = []
    if len(highs) >= 2:
        labels.append("HH" if highs[-1]["price"] > highs[-2]["price"] else "LH")
    if len(lows) >= 2:
        labels.append("HL" if lows[-1]["price"] > lows[-2]["price"] else "LL")
    if "HH" in labels and "HL" in labels:
        state = "BULLISH_STRUCTURE"
    elif "LH" in labels and "LL" in labels:
        state = "BEARISH_STRUCTURE"
    elif labels:
        state = "TRANSITION"
    else:
        state = "UNKNOWN"
    return {"state": state, "labels": labels, "swing_points": points}


def bos(candles: list[Candle]) -> list[dict[str, Any]]:
    points = swing_points(candles)
    latest = candles[-1] if candles else None
    if not latest:
        return []
    out = []
    for point in points[-6:]:
        if point["type"] == "SWING_HIGH" and latest.close > point["price"]:
            out.append({"type": "BULLISH_BOS", "broken_structure_level": point["price"], "break_timestamp": _utc(latest.timestamp).isoformat(), "break_close": latest.close, "wick_penetration": latest.high > point["price"], "confirmed_close_break": True})
        if point["type"] == "SWING_LOW" and latest.close < point["price"]:
            out.append({"type": "BEARISH_BOS", "broken_structure_level": point["price"], "break_timestamp": _utc(latest.timestamp).isoformat(), "break_close": latest.close, "wick_penetration": latest.low < point["price"], "confirmed_close_break": True})
    return out


def choch(candles: list[Candle]) -> list[dict[str, Any]]:
    if len(candles) < 8:
        return []
    previous_state = market_structure(candles[:-1])["state"]
    breaks = bos(candles)
    out = []
    for item in breaks:
        bullish_change = previous_state == "BEARISH_STRUCTURE" and item["type"] == "BULLISH_BOS"
        bearish_change = previous_state == "BULLISH_STRUCTURE" and item["type"] == "BEARISH_BOS"
        if bullish_change or bearish_change:
            out.append({
                "type": "BULLISH_CHOCH" if bullish_change else "BEARISH_CHOCH",
                "previous_structure_state": previous_state,
                "broken_level": item["broken_structure_level"],
                "new_candidate_structure_state": "BULLISH_STRUCTURE" if bullish_change else "BEARISH_STRUCTURE",
                "confirmation_timestamp": item["break_timestamp"],
            })
    return out


def support_resistance(candles: list[Candle], decision_timestamp: datetime) -> list[dict[str, Any]]:
    levels: list[dict[str, Any]] = []
    latest = candles[-1]
    points = swing_points(candles)
    for point in points[-10:]:
        level_type = "recent swing resistance" if point["type"] == "SWING_HIGH" else "recent swing support"
        levels.append({"level_type": level_type, "level_price": point["price"], "level_created_at": point["timestamp"], "touch_count": sum(abs(c.close - point["price"]) <= (latest.high - latest.low) for c in candles), "distance_from_price": abs(latest.close - point["price"]), "distance_in_ATR": 0.0, "broken": latest.close > point["price"] if point["type"] == "SWING_HIGH" else latest.close < point["price"], "retested": abs(latest.close - point["price"]) <= max(latest.high - latest.low, 1e-12)})
    current_day = _utc(decision_timestamp).date()
    prior_day = [c for c in candles if _utc(c.timestamp).date() < current_day]
    if prior_day:
        levels.append({"level_type": "previous day high", "level_price": max(c.high for c in prior_day), "level_created_at": _utc(prior_day[-1].timestamp).isoformat(), "touch_count": 0, "distance_from_price": abs(latest.close - max(c.high for c in prior_day)), "distance_in_ATR": 0.0, "broken": latest.close > max(c.high for c in prior_day), "retested": False})
        levels.append({"level_type": "previous day low", "level_price": min(c.low for c in prior_day), "level_created_at": _utc(prior_day[-1].timestamp).isoformat(), "touch_count": 0, "distance_from_price": abs(latest.close - min(c.low for c in prior_day)), "distance_in_ATR": 0.0, "broken": latest.close < min(c.low for c in prior_day), "retested": False})
    return levels


def supply_demand_zones(candles: list[Candle]) -> list[dict[str, Any]]:
    zones = []
    atr_value = atr(candles) or 0.0
    for idx in range(1, len(candles)):
        previous, current = candles[idx - 1], candles[idx]
        displacement = abs(current.close - previous.close)
        if atr_value and displacement >= atr_value:
            zone_type = "DEMAND_ZONE" if current.close > previous.close else "SUPPLY_ZONE"
            zones.append({"zone_id": hashlib.sha256(f"{zone_type}:{current.timestamp}".encode()).hexdigest()[:16], "zone_type": zone_type, "proximal_price": max(previous.open, previous.close) if zone_type == "DEMAND_ZONE" else min(previous.open, previous.close), "distal_price": previous.low if zone_type == "DEMAND_ZONE" else previous.high, "created_at": _utc(current.timestamp).isoformat(), "impulse_strength": displacement, "ATR_normalized_impulse": displacement / atr_value, "touch_count": 0, "freshness": "FRESH"})
    return zones[-20:]


def retests(candles: list[Candle], levels: list[dict[str, Any]], zones: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not candles:
        return []
    latest = candles[-1]
    rng = max(latest.high - latest.low, 1e-12)
    out = []
    for level in levels[-10:]:
        if latest.low <= level["level_price"] <= latest.high:
            retest_type = "SUPPORT_RETEST" if "support" in level["level_type"] or "low" in level["level_type"] else "RESISTANCE_RETEST"
            out.append({"type": retest_type, "class": "CLEAN_RETEST" if abs(latest.close - level["level_price"]) <= rng * 0.35 else "DEEP_RETEST", "original_level": level["level_price"], "break_timestamp": level["level_created_at"], "retest_timestamp": _utc(latest.timestamp).isoformat(), "retest_depth": abs(latest.close - level["level_price"]), "ATR_normalized_depth": 0.0, "rejection_strength": abs(latest.close - latest.open) / rng})
    for zone in zones[-10:]:
        low, high = sorted((zone["proximal_price"], zone["distal_price"]))
        if latest.low <= high and latest.high >= low:
            out.append({"type": "DEMAND_RETEST" if zone["zone_type"] == "DEMAND_ZONE" else "SUPPLY_RETEST", "class": "CLEAN_RETEST", "original_level": zone["proximal_price"], "break_timestamp": zone["created_at"], "retest_timestamp": _utc(latest.timestamp).isoformat(), "retest_depth": abs(latest.close - zone["proximal_price"]), "ATR_normalized_depth": 0.0, "rejection_strength": abs(latest.close - latest.open) / rng})
    return out


def liquidity(candles: list[Candle]) -> list[dict[str, Any]]:
    points = swing_points(candles)
    out = []
    tolerance = (atr(candles) or 0.0) * 0.15
    highs = [p for p in points if p["type"] == "SWING_HIGH"]
    lows = [p for p in points if p["type"] == "SWING_LOW"]
    for point in highs[-8:]:
        out.append({"type": "SWING_HIGH_LIQUIDITY", "price": point["price"], "timestamp": point["timestamp"]})
    for point in lows[-8:]:
        out.append({"type": "SWING_LOW_LIQUIDITY", "price": point["price"], "timestamp": point["timestamp"]})
    if len(highs) >= 2 and abs(highs[-1]["price"] - highs[-2]["price"]) <= tolerance:
        out.append({"type": "EQUAL_HIGHS", "price": mean([highs[-1]["price"], highs[-2]["price"]]), "timestamp": highs[-1]["timestamp"]})
    if len(lows) >= 2 and abs(lows[-1]["price"] - lows[-2]["price"]) <= tolerance:
        out.append({"type": "EQUAL_LOWS", "price": mean([lows[-1]["price"], lows[-2]["price"]]), "timestamp": lows[-1]["timestamp"]})
    return out


def liquidity_sweeps(candles: list[Candle], liquidity_levels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not candles:
        return []
    latest = candles[-1]
    atr_value = atr(candles) or 1e-12
    out = []
    for level in liquidity_levels[-10:]:
        price = level["price"]
        if "HIGH" in level["type"] and latest.high > price and latest.close < price:
            out.append({"type": "HIGH_SWEEP", "liquidity_level": price, "sweep_extreme": latest.high, "sweep_distance": latest.high - price, "sweep_distance_ATR": (latest.high - price) / atr_value, "reclaim_price": latest.close, "sweep_timestamp": _utc(latest.timestamp).isoformat()})
        if "LOW" in level["type"] and latest.low < price and latest.close > price:
            out.append({"type": "LOW_SWEEP", "liquidity_level": price, "sweep_extreme": latest.low, "sweep_distance": price - latest.low, "sweep_distance_ATR": (price - latest.low) / atr_value, "reclaim_price": latest.close, "sweep_timestamp": _utc(latest.timestamp).isoformat()})
    return out


def breakout_buildup(candles: list[Candle], levels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(candles) < 8:
        return []
    latest = candles[-1]
    out = []
    for level in levels[-10:]:
        recent = candles[-6:]
        distances = [abs(c.close - level["level_price"]) for c in recent]
        if len(distances) >= 3 and distances[-1] < distances[0] and level["broken"]:
            out.append({"type": "BREAKOUT_WITH_BUILDUP", "level": level["level_price"], "number_of_tests": sum(d <= max(latest.high - latest.low, 1e-12) * 2 for d in distances), "compression_duration": len(recent), "ATR_contraction": distances[-1] / max(distances[0], 1e-12), "break_distance": abs(latest.close - level["level_price"]), "break_direction": "BULLISH" if latest.close > level["level_price"] else "BEARISH"})
    return out


def false_breakouts(candles: list[Candle], levels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not candles:
        return []
    latest = candles[-1]
    out = []
    for level in levels[-10:]:
        price = level["level_price"]
        if latest.high > price and latest.close < price:
            out.append({"type": "FALSE_BREAKOUT_HIGH", "trap": "BULL_TRAP", "false_break_level": price, "excursion": latest.high - price, "time_above_or_below": 1, "reclaim_timestamp": _utc(latest.timestamp).isoformat(), "confirmation": True})
        if latest.low < price and latest.close > price:
            out.append({"type": "FALSE_BREAKOUT_LOW", "trap": "BEAR_TRAP", "false_break_level": price, "excursion": price - latest.low, "time_above_or_below": 1, "reclaim_timestamp": _utc(latest.timestamp).isoformat(), "confirmation": True})
    return out


def fair_value_gaps(candles: list[Candle]) -> list[dict[str, Any]]:
    out = []
    atr_value = atr(candles) or 1e-12
    for first, _, third in zip(candles, candles[1:], candles[2:], strict=False):
        if third.low > first.high:
            out.append({"type": "BULLISH_FVG", "lower": first.high, "upper": third.low, "created_at": _utc(third.timestamp).isoformat(), "size": third.low - first.high, "ATR_normalized_size": (third.low - first.high) / atr_value, "state": "OPEN"})
        if third.high < first.low:
            out.append({"type": "BEARISH_FVG", "lower": third.high, "upper": first.low, "created_at": _utc(third.timestamp).isoformat(), "size": first.low - third.high, "ATR_normalized_size": (first.low - third.high) / atr_value, "state": "OPEN"})
    return out[-20:]


def order_blocks(candles: list[Candle], breaks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    if not breaks or len(candles) < 3:
        return out
    atr_value = atr(candles) or 1e-12
    for item in breaks[-5:]:
        bullish = item["type"] == "BULLISH_BOS"
        origins = [c for c in candles[:-1] if (c.close < c.open if bullish else c.close > c.open)]
        if origins:
            origin = origins[-1]
            out.append({"type": "BULLISH_ORDER_BLOCK_CANDIDATE" if bullish else "BEARISH_ORDER_BLOCK_CANDIDATE", "zone": {"proximal": max(origin.open, origin.close), "distal": origin.low if bullish else origin.high}, "origin_candle": _utc(origin.timestamp).isoformat(), "displacement": abs(candles[-1].close - origin.close), "ATR_normalized_displacement": abs(candles[-1].close - origin.close) / atr_value, "BOS_association": item["type"], "retest_state": "NO_RETEST", "definition": "RESEARCH_DEFINITION"})
    return out


def fibonacci(candles: list[Candle]) -> dict[str, Any]:
    points = swing_points(candles)
    if len(points) < 2:
        return {"state": "UNKNOWN", "nearest_retracement": None}
    anchor_a, anchor_b = points[-2], points[-1]
    high, low = max(anchor_a["price"], anchor_b["price"]), min(anchor_a["price"], anchor_b["price"])
    rng = max(high - low, 1e-12)
    levels = {ratio: high - rng * ratio for ratio in (0.236, 0.382, 0.5, 0.618, 0.786)}
    close = candles[-1].close
    nearest = min(levels.items(), key=lambda item: abs(item[1] - close))
    return {"anchors": [anchor_a, anchor_b], "levels": levels, "nearest_retracement": nearest[0], "nearest_price": nearest[1], "primary_research_zone": nearest[0] in (0.382, 0.5, 0.618)}


def indicators(candles: list[Candle]) -> dict[str, Any]:
    closes = [c.close for c in candles]
    if not closes:
        return {}
    ema_fast = _ema(closes, 12)
    ema_slow = _ema(closes, 26)
    sma_20 = mean(closes[-20:]) if len(closes) >= 20 else mean(closes)
    rsi_value = _rsi(closes)
    macd_value = ema_fast - ema_slow
    stoch = ((closes[-1] - min(closes[-14:])) / max(max(closes[-14:]) - min(closes[-14:]), 1e-12)) * 100 if len(closes) >= 14 else None
    return {"EMA": ema_fast, "SMA": sma_20, "EMA_slope": ema_fast - _ema(closes[:-1], 12) if len(closes) > 1 else 0.0, "RSI": rsi_value, "Stochastic": stoch, "MACD": macd_value, "ATR": atr(candles), "volatility_percentile": _volatility_percentile(candles), "ADX": None}


def _ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    alpha = 2 / (period + 1)
    value = values[0]
    for item in values[1:]:
        value = alpha * item + (1 - alpha) * value
    return value


def _rsi(values: list[float], period: int = 14) -> float | None:
    if len(values) < period + 1:
        return None
    diffs = [b - a for a, b in zip(values[-period - 1 : -1], values[-period:], strict=False)]
    gains = [max(diff, 0.0) for diff in diffs]
    losses = [abs(min(diff, 0.0)) for diff in diffs]
    avg_loss = mean(losses)
    if avg_loss == 0:
        return 100.0
    return 100 - (100 / (1 + mean(gains) / avg_loss))


def _volatility_percentile(candles: list[Candle]) -> float | None:
    ranges = true_ranges(candles)
    if len(ranges) < 20:
        return None
    current = ranges[-1]
    return sum(r <= current for r in ranges[-100:]) / len(ranges[-100:])


def timeframe_direction(candles: list[Candle]) -> str:
    if len(candles) < 5:
        return "UNKNOWN"
    close = candles[-1].close
    avg = mean(c.close for c in candles[-5:])
    if close > avg:
        return "BULLISH"
    if close < avg:
        return "BEARISH"
    return "NEUTRAL"


def mtf_alignment(candles_by_tf: dict[str, list[Candle]]) -> dict[str, Any]:
    directions = {tf: timeframe_direction(candles) for tf, candles in candles_by_tf.items()}
    bullish = sum(1 for value in directions.values() if value == "BULLISH")
    bearish = sum(1 for value in directions.values() if value == "BEARISH")
    known = bullish + bearish
    if known == 0:
        state = "MIXED"
    elif bullish == known:
        state = "FULL_BULLISH"
    elif bearish == known:
        state = "FULL_BEARISH"
    elif bullish > bearish:
        state = "MAJORITY_BULLISH"
    elif bearish > bullish:
        state = "MAJORITY_BEARISH"
    else:
        state = "MIXED"
    return {"directions": directions, "micro_direction": directions.get("M1", "UNKNOWN"), "intraday_direction": directions.get("M15", "UNKNOWN"), "higher_timeframe_direction": directions.get("H1", "UNKNOWN"), "alignment_count": max(bullish, bearish), "conflict_count": min(bullish, bearish), "alignment_state": state}


def session_context(decision_timestamp: datetime, candles: list[Candle]) -> dict[str, Any]:
    utc = _utc(decision_timestamp)
    hour = utc.hour
    if 0 <= hour < 7:
        session = "ASIA"
    elif 7 <= hour < 12:
        session = "LONDON"
    elif 12 <= hour < 16:
        session = "LONDON_NEW_YORK_OVERLAP"
    elif 16 <= hour < 21:
        session = "NEW_YORK"
    else:
        session = "OFF_HOURS"
    session_start = datetime.combine(utc.date(), time(hour=max(hour - 4, 0)), tzinfo=UTC)
    session_candles = [c for c in candles if _utc(c.timestamp) >= session_start]
    return {"session": session, "session_high": max((c.high for c in session_candles), default=None), "session_low": min((c.low for c in session_candles), default=None), "session_relative_position": candles[-1].close if candles else None}


def news_context() -> dict[str, Any]:
    return {"state": "NEWS_DATA_UNAVAILABLE", "provider": None}


def m5_liquidity_sweep_break(candles: list[Candle]) -> list[dict[str, Any]]:
    levels = liquidity(candles)
    sweeps = liquidity_sweeps(candles, levels)
    latest = candles[-1] if candles else None
    out = []
    if latest:
        for sweep in sweeps:
            if sweep["type"] == "HIGH_SWEEP" and latest.close < latest.open:
                out.append({"type": "M5_LIQUIDITY_SWEEP_BREAK", "direction": "SELL", "SL_reference": sweep["sweep_extreme"], "target_reference": "next validated structure low/support"})
            if sweep["type"] == "LOW_SWEEP" and latest.close > latest.open:
                out.append({"type": "M5_LIQUIDITY_SWEEP_BREAK", "direction": "BUY", "SL_reference": sweep["sweep_extreme"], "target_reference": "next structure high/resistance"})
    return out


def shadow_rr_matrix(setups: list[dict[str, Any]], bid: float, ask: float) -> list[dict[str, Any]]:
    spread = ask - bid
    matrix = []
    for setup in setups:
        direction = setup.get("direction", "UNKNOWN")
        entry = ask if direction == "BUY" else bid if direction == "SELL" else None
        if entry is None:
            continue
        stop_distance = max(spread * 3, 1e-6)
        for rr in RR_MATRIX:
            target = entry + stop_distance * rr if direction == "BUY" else entry - stop_distance * rr
            stop = entry - stop_distance if direction == "BUY" else entry + stop_distance
            matrix.append({"setup_id": setup["setup_id"], "direction": direction, "entry": entry, "SL": stop, "TP": target, "RR": rr, "entry_timestamp": setup["timestamp"], "SL_timestamp": None, "TP_timestamp": None, "max_favorable_excursion": None, "max_adverse_excursion": None, "gross_R": None, "spread_cost_R": spread / stop_distance, "commission_R": "COMMISSION_UNAVAILABLE", "net_R": None, "outcome": "UNRESOLVED"})
    return matrix


def candidate_setups(vector: dict[str, Any]) -> list[dict[str, Any]]:
    setups = []
    for sweep in vector["liquidity_sweeps"]:
        direction = "SELL" if sweep["type"] == "HIGH_SWEEP" else "BUY"
        setups.append({"setup_id": hashlib.sha256(f"{sweep['type']}:{sweep['sweep_timestamp']}".encode()).hexdigest()[:16], "type": "LIQUIDITY_SWEEP", "direction": direction, "timestamp": sweep["sweep_timestamp"]})
    for breakout in vector["breakout_buildup"]:
        direction = "BUY" if breakout["break_direction"] == "BULLISH" else "SELL"
        setups.append({"setup_id": hashlib.sha256(f"BREAKOUT:{breakout['level']}:{breakout['break_direction']}".encode()).hexdigest()[:16], "type": "BREAKOUT_WITH_BUILDUP", "direction": direction, "timestamp": vector["decision_timestamp"]})
    for false_break in vector["false_breakouts"]:
        direction = "SELL" if false_break["trap"] == "BULL_TRAP" else "BUY"
        setups.append({"setup_id": hashlib.sha256(f"FALSE:{false_break['false_break_level']}:{false_break['trap']}".encode()).hexdigest()[:16], "type": false_break["trap"], "direction": direction, "timestamp": false_break["reclaim_timestamp"]})
    return setups


def build_confluence_vector(candles_by_tf: dict[str, list[Candle]], decision_timestamp: datetime, tick: TickQuote) -> dict[str, Any]:
    tick.validate(decision_timestamp)
    completed = {tf: completed_candles(candles_by_tf.get(tf, []), tf, decision_timestamp) for tf in TIMEFRAME_MINUTES}
    missing = [tf for tf, candles in completed.items() if not candles]
    if missing:
        raise ValueError(f"missing completed candles: {missing}")
    m1 = completed["M1"]
    levels = support_resistance(m1, decision_timestamp)
    zones = supply_demand_zones(m1)
    liq = liquidity(m1)
    sweeps = liquidity_sweeps(m1, liq)
    breaks = bos(m1)
    vector: dict[str, Any] = {
        "policy_version": POLICY_VERSION,
        "policy_hash": POLICY_HASH,
        "feature_version": FEATURE_VERSION,
        "collector_version": COLLECTOR_VERSION,
        "decision_timestamp": _utc(decision_timestamp).isoformat(),
        "spread": tick.ask - tick.bid,
        "spread_provenance": tick.provenance,
        "commission": "COMMISSION_UNAVAILABLE",
        "candlestick_patterns": {tf: detect_candlestick_patterns(candles, tf) for tf, candles in completed.items()},
        "market_structure": market_structure(m1),
        "BOS": breaks,
        "CHOCH": choch(m1),
        "support_resistance": levels,
        "supply_demand": zones,
        "retests": retests(m1, levels, zones),
        "liquidity_state": liq,
        "liquidity_sweeps": sweeps,
        "M5_liquidity_sweep_break": m5_liquidity_sweep_break(completed["M5"]),
        "breakout_buildup": breakout_buildup(m1, levels),
        "false_breakouts": false_breakouts(m1, levels),
        "FVG": fair_value_gaps(m1),
        "order_block": order_blocks(m1, breaks),
        "fibonacci_context": fibonacci(m1),
        "indicator_context": {tf: indicators(candles) for tf, candles in completed.items()},
        "session": session_context(decision_timestamp, m1),
        "news_context": news_context(),
        "MTF_alignment": mtf_alignment(completed),
    }
    setups = candidate_setups(vector)
    vector["confluence_setups"] = setups
    vector["confluence_labels"] = [
        {"setup_id": setup["setup_id"], "label": "HIGH_CONFLUENCE" if len(setups) >= 3 else "MEDIUM_CONFLUENCE" if len(setups) == 2 else "LOW_CONFLUENCE"}
        for setup in setups
    ]
    vector["shadow_RR_matrix"] = shadow_rr_matrix(setups, tick.bid, tick.ask)
    return vector


def stable_hash(payload: dict[str, Any]) -> str:
    import json

    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def as_serializable_candle(candle: Candle) -> dict[str, Any]:
    payload = asdict(candle)
    payload["timestamp"] = _utc(candle.timestamp).isoformat()
    return payload
