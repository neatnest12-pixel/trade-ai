from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from statistics import mean

from app.v3.multitimeframe import TIMEFRAMES, alignment, hierarchical

STRATEGY_INPUT_POLICY_VERSION = "v3-strategy-input-r1"
STRATEGY_INPUT_POLICY_HASH = hashlib.sha256(
    STRATEGY_INPUT_POLICY_VERSION.encode()
).hexdigest()

EMA_FAST = 8
EMA_SLOW = 21
ATR_LOOKBACK = 14
STRUCTURE_LOOKBACK = 20


@dataclass(frozen=True)
class StrategyCandle:
    timeframe: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    provenance: str = "DIRECT_MT5"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _ema(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    alpha = 2 / (period + 1)
    ema = mean(values[:period])
    for value in values[period:]:
        ema = alpha * value + (1 - alpha) * ema
    return ema


def _atr(candles: list[StrategyCandle]) -> float | None:
    if len(candles) < ATR_LOOKBACK + 1:
        return None
    trs: list[float] = []
    for prev, cur in zip(candles[-ATR_LOOKBACK - 1 : -1], candles[-ATR_LOOKBACK:], strict=True):
        trs.append(max(cur.high - cur.low, abs(cur.high - prev.close), abs(cur.low - prev.close)))
    return mean(trs)


def _state(ema_fast: float | None, ema_slow: float | None, ema_slope: float | None) -> str:
    if ema_fast is None or ema_slow is None or ema_slope is None:
        return "UNAVAILABLE"
    if ema_fast > ema_slow and ema_slope > 0:
        return "BULLISH"
    if ema_fast < ema_slow and ema_slope < 0:
        return "BEARISH"
    return "NEUTRAL"


def _session(hour: int) -> str:
    if 0 <= hour < 7:
        return "ASIA"
    if 7 <= hour < 12:
        return "LONDON"
    if 12 <= hour < 16:
        return "OVERLAP"
    if 16 <= hour < 21:
        return "NEW_YORK"
    return "OFF_HOURS"


def _completed(candles: Iterable[StrategyCandle], decision: datetime, timeframe: str) -> list[StrategyCandle]:
    minutes = TIMEFRAMES[timeframe]
    cutoff = _utc(decision)
    return sorted(
        (
            c
            for c in candles
            if c.timeframe == timeframe
            and _utc(c.timestamp) + timedelta(minutes=minutes) <= cutoff
        ),
        key=lambda c: c.timestamp,
    )


def _context(candles: list[StrategyCandle], decision: datetime, timeframe: str) -> dict[str, object]:
    completed = _completed(candles, decision, timeframe)
    if not completed:
        return {
            "timeframe": timeframe,
            "available": False,
            "provenance": "UNAVAILABLE",
            "feature_timestamp": _utc(decision).isoformat(),
        }
    current = completed[-1]
    closes = [c.close for c in completed]
    ranges = [c.high - c.low for c in completed]
    atr = _atr(completed)
    ema_fast = _ema(closes, EMA_FAST)
    ema_slow = _ema(closes, EMA_SLOW)
    prior_fast = _ema(closes[:-1], EMA_FAST)
    ema_slope = None if ema_fast is None or prior_fast is None else ema_fast - prior_fast
    prior = completed[-STRUCTURE_LOOKBACK - 1 : -1]
    recent_high = max((c.high for c in prior), default=None)
    recent_low = min((c.low for c in prior), default=None)
    recent_mean = mean(closes[-STRUCTURE_LOOKBACK:]) if len(closes) >= STRUCTURE_LOOKBACK else None
    avg_range = mean(ranges[-STRUCTURE_LOOKBACK - 1 : -1]) if len(ranges) > STRUCTURE_LOOKBACK else None
    normalized_atr = None if atr is None or current.close == 0 else atr / current.close
    trend_state = _state(ema_fast, ema_slow, ema_slope)
    volatility_state = "UNAVAILABLE"
    if normalized_atr is not None:
        if normalized_atr < 0.00005:
            volatility_state = "LOW_VOL"
        elif normalized_atr > 0.004:
            volatility_state = "HIGH_VOL"
        else:
            volatility_state = "NORMAL_VOL"
    return {
        "timeframe": timeframe,
        "available": True,
        "source_candle_timestamp": _utc(current.timestamp).isoformat(),
        "feature_timestamp": _utc(decision).isoformat(),
        "provenance": current.provenance,
        "open": current.open,
        "high": current.high,
        "low": current.low,
        "close": current.close,
        "return_1": (closes[-1] - closes[-2]) / closes[-2] if len(closes) >= 2 and closes[-2] else None,
        "return_3": (closes[-1] - closes[-4]) / closes[-4] if len(closes) >= 4 and closes[-4] else None,
        "atr14": atr,
        "normalized_atr": normalized_atr,
        "momentum": closes[-1] - closes[-4] if len(closes) >= 4 else None,
        "ema_fast": ema_fast,
        "ema_slow": ema_slow,
        "ema_slope": ema_slope,
        "trend_state": trend_state,
        "volatility_state": volatility_state,
        "recent_high": recent_high,
        "recent_low": recent_low,
        "recent_high_distance": None if recent_high is None else recent_high - current.close,
        "recent_low_distance": None if recent_low is None else current.close - recent_low,
        "range_width": None if recent_high is None or recent_low is None else recent_high - recent_low,
        "range_expansion": bool(avg_range is not None and (current.high - current.low) > 1.5 * avg_range),
        "range_compression": bool(avg_range is not None and (current.high - current.low) < 0.65 * avg_range),
        "local_mean": recent_mean,
        "distance_from_local_mean": None if recent_mean is None else current.close - recent_mean,
    }


def _regime(contexts: dict[str, dict[str, object]]) -> str:
    states = [contexts[tf].get("trend_state") for tf in ("M5", "M15", "H1") if contexts[tf].get("available")]
    if states and all(s == "BULLISH" for s in states):
        return "TREND_UP"
    if states and all(s == "BEARISH" for s in states):
        return "TREND_DOWN"
    m1 = contexts["M1"]
    if m1.get("range_compression"):
        return "RANGE"
    if m1.get("volatility_state") == "HIGH_VOL":
        return "HIGH_VOL"
    if m1.get("volatility_state") == "LOW_VOL":
        return "LOW_VOL"
    return "RANGE"


def build_strategy_input(candles: Iterable[StrategyCandle], decision: datetime) -> dict[str, object]:
    decision = _utc(decision)
    all_candles = list(candles)
    contexts = {tf: _context(all_candles, decision, tf) for tf in TIMEFRAMES}
    states = {tf: str(contexts[tf].get("trend_state", "UNAVAILABLE")) for tf in TIMEFRAMES}
    m1 = contexts["M1"]
    normalized_atr = m1.get("normalized_atr")
    recent_high = m1.get("recent_high")
    recent_low = m1.get("recent_low")
    deviation = m1.get("distance_from_local_mean")
    regime = _regime(contexts)
    payload = {
        "strategy_input_policy_version": STRATEGY_INPUT_POLICY_VERSION,
        "strategy_input_policy_hash": STRATEGY_INPUT_POLICY_HASH,
        "decision_timestamp": decision.isoformat(),
        "states": states,
        "contexts": contexts,
        "alignment": alignment(states),
        "hierarchy": hierarchical(states),
        "session": {
            "utc_hour": decision.hour,
            "weekday": decision.weekday(),
            "session": _session(decision.hour),
        },
        "regime": "RANGE" if regime == "RANGE" else regime,
        "market_regime": regime,
        "normalized_atr": normalized_atr,
        "close": m1.get("close"),
        "prior_high": recent_high,
        "prior_low": recent_low,
        "m1_return": m1.get("return_1"),
        "continuation": _continuation(contexts, states),
        "deviation": deviation,
        "range_expansion": m1.get("range_expansion"),
        "range_compression": m1.get("range_compression"),
        "breakout_up_state": bool(recent_high is not None and m1.get("close") is not None and m1["close"] > recent_high),
        "breakout_down_state": bool(recent_low is not None and m1.get("close") is not None and m1["close"] < recent_low),
        "feature_provenance": _provenance(contexts),
    }
    return payload


def _continuation(contexts: dict[str, dict[str, object]], states: dict[str, str]) -> bool:
    m1_return = contexts["M1"].get("return_1")
    if m1_return is None:
        return False
    htf_bullish = states.get("H1") == states.get("H6") == states.get("H12") == "BULLISH"
    htf_bearish = states.get("H1") == states.get("H6") == states.get("H12") == "BEARISH"
    return bool((htf_bullish and m1_return < 0) or (htf_bearish and m1_return > 0))


def _provenance(contexts: dict[str, dict[str, object]]) -> dict[str, dict[str, object]]:
    return {
        tf: {
            "source_timeframe": tf,
            "source_candle_timestamp": ctx.get("source_candle_timestamp"),
            "feature_timestamp": ctx.get("feature_timestamp"),
            "availability": "AVAILABLE" if ctx.get("available") else "UNAVAILABLE",
            "provenance": ctx.get("provenance", "UNAVAILABLE"),
        }
        for tf, ctx in contexts.items()
    }


def strategy_input_manifest() -> dict[str, object]:
    return {
        "strategy_input_policy_version": STRATEGY_INPUT_POLICY_VERSION,
        "strategy_input_policy_hash": STRATEGY_INPUT_POLICY_HASH,
        "completed_candles_only": True,
        "lookbacks": {
            "ema_fast": EMA_FAST,
            "ema_slow": EMA_SLOW,
            "atr": ATR_LOOKBACK,
            "structure": STRUCTURE_LOOKBACK,
        },
    }


def serialize_candles(candles: Iterable[StrategyCandle]) -> list[dict[str, object]]:
    return [asdict(c) | {"timestamp": _utc(c.timestamp).isoformat()} for c in candles]
