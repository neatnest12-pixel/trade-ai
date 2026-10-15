from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from app.v3_2.confluence import (
    Candle,
    TickQuote,
    bos,
    breakout_buildup,
    build_confluence_vector,
    choch,
    completed_candles,
    detect_candlestick_patterns,
    fair_value_gaps,
    fibonacci,
    indicators,
    liquidity,
    liquidity_sweeps,
    market_structure,
    mtf_alignment,
    order_blocks,
    session_context,
    supply_demand_zones,
    support_resistance,
)

POLICY_VERSION = "v3.1-full-strategy-research-r1"
POLICY_HASH = hashlib.sha256(POLICY_VERSION.encode("utf-8")).hexdigest()
STRATEGY_VERSION = "v3.1-strategy-registry-r1"
DEPLOYMENT_STATUS = "RESEARCH_ONLY"
RR_MATRIX = (1.0, 1.5, 2.0, 3.0, 5.0)


@dataclass(frozen=True)
class StrategyDefinition:
    strategy_id: str
    strategy_family: str
    strategy_version: str
    supported_timeframes: tuple[str, ...]
    required_features: tuple[str, ...]
    stop_policy: str
    target_policy: str
    max_hold: str
    cost_requirements: tuple[str, ...]
    sample_count: int = 0
    deployment_status: str = DEPLOYMENT_STATUS


@dataclass(frozen=True)
class StrategySignal:
    strategy_id: str
    strategy_family: str
    direction: str
    setup: bool
    setup_score: float
    timeframe: str
    regime: str
    reason_codes: tuple[str, ...]
    max_hold: str
    deployment_status: str = DEPLOYMENT_STATUS


class StrategyRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, StrategyDefinition] = {}
        self._evaluators: dict[str, Callable[[dict[str, Any]], StrategySignal]] = {}

    def register(self, definition: StrategyDefinition, evaluator: Callable[[dict[str, Any]], StrategySignal]) -> None:
        if definition.strategy_id in self._definitions:
            raise ValueError(f"duplicate strategy_id: {definition.strategy_id}")
        self._definitions[definition.strategy_id] = definition
        self._evaluators[definition.strategy_id] = evaluator

    def definitions(self) -> list[StrategyDefinition]:
        return list(self._definitions.values())

    def evaluate(self, context: dict[str, Any]) -> list[StrategySignal]:
        return [self._evaluators[strategy_id](context) for strategy_id in self._definitions]


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _latest(candles_by_tf: dict[str, list[Candle]], timeframe: str) -> list[Candle]:
    return candles_by_tf.get(timeframe, [])


def _direction_from_signal(score: float) -> str:
    if score > 0.05:
        return "BUY"
    if score < -0.05:
        return "SELL"
    return "NEUTRAL"


def _setup(score: float, threshold: float = 0.4) -> bool:
    return abs(score) >= threshold


def _signal(strategy_id: str, family: str, direction: str, setup: bool, score: float, timeframe: str, reasons: list[str], max_hold: str, regime: str = "UNCLASSIFIED") -> StrategySignal:
    return StrategySignal(strategy_id, family, direction, setup, round(score, 6), timeframe, regime, tuple(reasons), max_hold)


def time_horizon_policies() -> dict[str, dict[str, Any]]:
    return {
        "SCALPING": {"holding_horizon": "seconds-minutes", "max_hold": "30 minutes", "target_horizon": "short", "timeframe_context": ("M1", "M5"), "cost_sensitivity": "VERY_HIGH"},
        "DAY_TRADING": {"holding_horizon": "intraday", "max_hold": "1 day", "target_horizon": "intraday", "timeframe_context": ("M5", "M15", "H1"), "cost_sensitivity": "HIGH"},
        "SWING_TRADING": {"holding_horizon": "days", "max_hold": "5 days", "target_horizon": "multi-day", "timeframe_context": ("H1", "H6", "H12"), "cost_sensitivity": "MEDIUM"},
        "POSITION_TRADING": {"holding_horizon": "weeks", "max_hold": "30 days", "target_horizon": "long", "timeframe_context": ("H6", "H12"), "cost_sensitivity": "LOWER_BUT_SWAP_DEPENDENT"},
    }


def _trend_eval(context: dict[str, Any]) -> StrategySignal:
    ind = context["indicators"]["M15"]
    align = context["mtf_alignment"]
    ema_slope = ind.get("EMA_slope") or 0.0
    macd = ind.get("MACD") or 0.0
    score = (1 if ema_slope > 0 else -1 if ema_slope < 0 else 0) * 0.25
    score += (1 if macd > 0 else -1 if macd < 0 else 0) * 0.25
    score += 0.3 if "BULLISH" in align["alignment_state"] else -0.3 if "BEARISH" in align["alignment_state"] else 0
    reasons = ["EMA_SLOPE", "MACD", "MTF_ALIGNMENT"]
    return _signal("TREND_MOMENTUM", "TREND", _direction_from_signal(score), _setup(score), score, "M15", reasons, "1 day", align["alignment_state"])


def _breakout_eval(context: dict[str, Any]) -> StrategySignal:
    breakouts = context["breakout_buildup"]
    fvg = context["FVG"]
    score = 0.0
    direction = "NEUTRAL"
    reasons: list[str] = []
    if breakouts:
        direction = "BUY" if breakouts[-1]["break_direction"] == "BULLISH" else "SELL"
        score += 0.6 if direction == "BUY" else -0.6
        reasons.append("BREAKOUT_WITH_BUILDUP")
    if fvg:
        score += 0.15 if fvg[-1]["type"] == "BULLISH_FVG" else -0.15
        reasons.append("FVG_IMBALANCE")
    return _signal("BREAKOUT_RESEARCH", "BREAKOUT", direction if direction != "NEUTRAL" else _direction_from_signal(score), bool(breakouts), score, "M5", reasons or ["NO_BREAKOUT"], "6 hours")


def _mean_reversion_eval(context: dict[str, Any]) -> StrategySignal:
    ind = context["indicators"]["M5"]
    rsi = ind.get("RSI")
    stoch = ind.get("Stochastic")
    score = 0.0
    reasons: list[str] = []
    if rsi is not None and rsi < 30:
        score += 0.5
        reasons.append("RSI_OVERSOLD")
    if rsi is not None and rsi > 70:
        score -= 0.5
        reasons.append("RSI_OVERBOUGHT")
    if stoch is not None and stoch < 20:
        score += 0.25
        reasons.append("STOCHASTIC_OVERSOLD")
    if stoch is not None and stoch > 80:
        score -= 0.25
        reasons.append("STOCHASTIC_OVERBOUGHT")
    return _signal("MEAN_REVERSION_RANGE", "MEAN_REVERSION", _direction_from_signal(score), _setup(score, 0.5), score, "M5", reasons or ["NO_RANGE_EXTREME"], "4 hours")


def _pullback_eval(context: dict[str, Any]) -> StrategySignal:
    fib = context["fibonacci"]
    align = context["mtf_alignment"]
    nearest = fib.get("nearest_retracement")
    zone = nearest in (0.382, 0.5, 0.618)
    bullish = "BULLISH" in align["alignment_state"]
    bearish = "BEARISH" in align["alignment_state"]
    score = 0.0
    if zone and bullish:
        score = 0.55
    elif zone and bearish:
        score = -0.55
    return _signal("PULLBACK_FIBONACCI", "PULLBACK", _direction_from_signal(score), zone and (bullish or bearish), score, "M15", ["FIBONACCI_RETRACEMENT", "HTF_ALIGNMENT"] if zone else ["NO_DETERMINISTIC_PULLBACK"], "1 day")


def _harmonic_eval(context: dict[str, Any]) -> StrategySignal:
    harmonics = context["harmonics"]
    if not harmonics:
        return _signal("HARMONIC_RESEARCH", "HARMONICS", "NEUTRAL", False, 0.0, "H1", ["NO_HARMONIC_PATTERN", "HIGH_OVERFIT_RISK"], "5 days")
    latest = harmonics[-1]
    score = 0.45 if latest["direction"] == "BUY" else -0.45
    return _signal("HARMONIC_RESEARCH", "HARMONICS", latest["direction"], True, score, "H1", [latest["pattern_type"], "HIGH_OVERFIT_RISK"], "5 days")


def _structure_eval(context: dict[str, Any]) -> StrategySignal:
    structure = context["market_structure"]["state"]
    breaks = context["BOS"]
    changes = context["CHOCH"]
    score = 0.0
    if structure == "BULLISH_STRUCTURE":
        score += 0.35
    if structure == "BEARISH_STRUCTURE":
        score -= 0.35
    if breaks:
        score += 0.25 if breaks[-1]["type"] == "BULLISH_BOS" else -0.25
    if changes:
        score += 0.2 if changes[-1]["type"] == "BULLISH_CHOCH" else -0.2
    return _signal("MARKET_STRUCTURE_RESEARCH", "MARKET_STRUCTURE", _direction_from_signal(score), _setup(score), score, "M15", ["SWINGS", "BOS", "CHOCH"], "1 day", structure)


def _liquidity_eval(context: dict[str, Any]) -> StrategySignal:
    sweeps = context["liquidity_sweeps"]
    if not sweeps:
        return _signal("LIQUIDITY_SWEEP_RESEARCH", "LIQUIDITY", "NEUTRAL", False, 0.0, "M5", ["NO_LIQUIDITY_SWEEP"], "6 hours")
    latest = sweeps[-1]
    direction = "SELL" if latest["type"] == "HIGH_SWEEP" else "BUY"
    score = -0.65 if direction == "SELL" else 0.65
    return _signal("LIQUIDITY_SWEEP_RESEARCH", "LIQUIDITY", direction, True, score, "M5", [latest["type"], "STOP_RUN_PATTERN"], "6 hours")


def _price_action_eval(context: dict[str, Any]) -> StrategySignal:
    patterns = context["candlestick_patterns"]["M1"]
    directional = [p for p in patterns if p["candidate_direction"] in {"BULLISH", "BEARISH"}]
    if not directional:
        return _signal("PRICE_ACTION_RESEARCH", "PRICE_ACTION", "NEUTRAL", False, 0.0, "M1", ["NO_DIRECTIONAL_PATTERN"], "2 hours")
    latest = directional[-1]
    score = 0.4 if latest["candidate_direction"] == "BULLISH" else -0.4
    return _signal("PRICE_ACTION_RESEARCH", "PRICE_ACTION", _direction_from_signal(score), True, score, "M1", [latest["pattern_id"]], "2 hours")


def _volatility_eval(context: dict[str, Any]) -> StrategySignal:
    percentile = context["indicators"]["M1"].get("volatility_percentile")
    if percentile is None:
        return _signal("VOLATILITY_RESEARCH", "VOLATILITY", "NEUTRAL", False, 0.0, "M1", ["VOLATILITY_PERCENTILE_UNAVAILABLE"], "4 hours")
    if percentile >= 0.8:
        direction = context["mtf_alignment"]["micro_direction"]
        score = 0.5 if direction == "BULLISH" else -0.5 if direction == "BEARISH" else 0.0
        return _signal("VOLATILITY_RESEARCH", "VOLATILITY", _direction_from_signal(score), score != 0, score, "M1", ["ATR_EXPANSION", "HIGH_VOLATILITY_CONTINUATION"], "4 hours")
    if percentile <= 0.2:
        return _signal("VOLATILITY_RESEARCH", "VOLATILITY", "NEUTRAL", False, 0.0, "M1", ["ATR_CONTRACTION", "LOW_VOLATILITY_MEAN_REVERSION_RESEARCH"], "4 hours")
    return _signal("VOLATILITY_RESEARCH", "VOLATILITY", "NEUTRAL", False, 0.0, "M1", ["NORMAL_VOLATILITY"], "4 hours")


def _session_eval(context: dict[str, Any]) -> StrategySignal:
    session = context["session"]["session"]
    direction = context["mtf_alignment"]["micro_direction"]
    score = 0.25 if direction == "BULLISH" else -0.25 if direction == "BEARISH" else 0.0
    setup = session in {"LONDON", "NEW_YORK", "LONDON_NEW_YORK_OVERLAP"} and direction != "NEUTRAL"
    return _signal("SESSION_RESEARCH", "SESSION", _direction_from_signal(score), setup, score, "M5", [session, "FIXED_SESSION_BOUNDARY"], "1 day")


def _grid_eval(context: dict[str, Any]) -> StrategySignal:
    return _signal("GRID_RESEARCH", "GRID", "NEUTRAL", False, 0.0, "M5", ["FINITE_GRID_ONLY", "NO_MARTINGALE", "NO_INFINITE_AVERAGING", "HARD_MAX_EXPOSURE_REQUIRED", "RESEARCH_ONLY"], "research-defined")


def _arbitrage_eval(context: dict[str, Any]) -> StrategySignal:
    return _signal("ARBITRAGE_RESEARCH", "ARBITRAGE", "NEUTRAL", False, 0.0, "tick", ["ARBITRAGE_NOT_EVALUABLE", "MULTI_SYMBOL_SYNCHRONIZED_BID_ASK_UNAVAILABLE"], "immediate")


def _carry_eval(context: dict[str, Any]) -> StrategySignal:
    return _signal("CARRY_RESEARCH", "CARRY", "NEUTRAL", False, 0.0, "H12", ["CARRY_UNAVAILABLE", "SWAP_PROVENANCE_UNAVAILABLE"], "30 days")


def default_registry() -> StrategyRegistry:
    registry = StrategyRegistry()
    strategies = [
        ("TREND_MOMENTUM", "TREND", ("M5", "M15", "H1"), ("EMA", "MACD", "MTF_ALIGNMENT"), "ATR_OR_STRUCTURE", "RR_MATRIX", "1 day", ("spread",), _trend_eval),
        ("BREAKOUT_RESEARCH", "BREAKOUT", ("M5", "M15"), ("SUPPORT_RESISTANCE", "ATR", "FVG"), "STRUCTURE_STOP", "RR_MATRIX", "6 hours", ("spread",), _breakout_eval),
        ("MEAN_REVERSION_RANGE", "MEAN_REVERSION", ("M5", "M15"), ("RSI", "STOCHASTIC", "RANGE"), "RANGE_INVALIDATION", "MEAN_TARGET", "4 hours", ("spread",), _mean_reversion_eval),
        ("PULLBACK_FIBONACCI", "PULLBACK", ("M15", "H1"), ("FIBONACCI", "MTF_ALIGNMENT"), "STRUCTURE_STOP", "RR_MATRIX", "1 day", ("spread",), _pullback_eval),
        ("HARMONIC_RESEARCH", "HARMONICS", ("H1", "H6"), ("SWING_RATIOS",), "PATTERN_INVALIDATION", "COMPLETION_ZONE", "5 days", ("spread", "commission_sensitivity"), _harmonic_eval),
        ("MARKET_STRUCTURE_RESEARCH", "MARKET_STRUCTURE", ("M5", "M15", "H1"), ("SWINGS", "BOS", "CHOCH"), "STRUCTURE_STOP", "RR_MATRIX", "1 day", ("spread",), _structure_eval),
        ("LIQUIDITY_SWEEP_RESEARCH", "LIQUIDITY", ("M5", "M15"), ("LIQUIDITY_LEVELS", "SWEEP"), "SWEEP_EXTREME", "RR_MATRIX", "6 hours", ("spread",), _liquidity_eval),
        ("PRICE_ACTION_RESEARCH", "PRICE_ACTION", ("M1", "M5"), ("CANDLESTICK_PATTERNS",), "CANDLE_INVALIDATION", "RR_MATRIX", "2 hours", ("spread",), _price_action_eval),
        ("VOLATILITY_RESEARCH", "VOLATILITY", ("M1", "M5"), ("ATR", "VOLATILITY_PERCENTILE"), "ATR_STOP", "RR_MATRIX", "4 hours", ("spread",), _volatility_eval),
        ("SESSION_RESEARCH", "SESSION", ("M5", "M15"), ("SESSION", "SESSION_HIGH_LOW"), "SESSION_INVALIDATION", "RR_MATRIX", "1 day", ("spread",), _session_eval),
        ("GRID_RESEARCH", "GRID", ("M5",), ("RANGE", "VOLATILITY"), "HARD_DRAWDOWN_STOP", "FINITE_GRID", "research-defined", ("spread", "commission", "max_exposure"), _grid_eval),
        ("ARBITRAGE_RESEARCH", "ARBITRAGE", ("tick",), ("SYNCHRONIZED_MULTI_SYMBOL_QUOTES",), "SEQUENCING_RISK", "EXECUTION_SPREAD", "immediate", ("bid_ask", "latency", "commission", "slippage"), _arbitrage_eval),
        ("CARRY_RESEARCH", "CARRY", ("H6", "H12"), ("INTEREST_RATE_DIFFERENTIAL", "SWAP"), "MACRO_INVALIDATION", "CARRY_HORIZON", "30 days", ("swap", "commission"), _carry_eval),
    ]
    for strategy_id, family, tfs, features, stop, target, max_hold, costs, evaluator in strategies:
        registry.register(
            StrategyDefinition(strategy_id, family, STRATEGY_VERSION, tfs, features, stop, target, max_hold, costs),
            evaluator,
        )
    return registry


def harmonic_patterns(candles: list[Candle], tolerance: float = 0.12) -> list[dict[str, Any]]:
    points = market_structure(candles)["swing_points"][-5:]
    if len(points) < 5:
        return []
    prices = [p["price"] for p in points]
    legs = [abs(b - a) for a, b in zip(prices[:-1], prices[1:], strict=False)]
    if min(legs) <= 0:
        return []
    ratios = {"xab": legs[1] / legs[0], "abc": legs[2] / legs[1], "bcd": legs[3] / legs[2]}
    templates = {
        "GARTLEY": (0.618, 0.618, 1.272),
        "BUTTERFLY": (0.786, 0.786, 1.618),
        "BAT": (0.5, 0.5, 1.618),
        "CRAB": (0.382, 0.886, 2.24),
    }
    out = []
    for name, target in templates.items():
        errors = [abs(ratios[key] - target[idx]) / target[idx] for idx, key in enumerate(("xab", "abc", "bcd"))]
        if max(errors) <= tolerance:
            out.append({"pattern_type": name, "ratio_errors": errors, "completion_zone": prices[-1], "direction": "BUY" if prices[-1] < prices[-2] else "SELL", "risk_flag": "HIGH_OVERFIT_RISK"})
    return out


def news_context() -> dict[str, Any]:
    return {"state": "NEWS_DATA_UNAVAILABLE", "events": []}


def carry_context() -> dict[str, Any]:
    return {"state": "CARRY_UNAVAILABLE", "swap_provenance": "UNAVAILABLE"}


def cost_discipline(tick: TickQuote) -> dict[str, Any]:
    spread = tick.ask - tick.bid
    quality = "PASS" if spread > 0 else "FAIL"
    return {"bid": tick.bid, "ask": tick.ask, "spread": spread, "spread_provenance": tick.provenance, "commission": "COMMISSION_UNAVAILABLE", "quality": quality}


def shadow_outcomes(signals: list[StrategySignal], tick: TickQuote) -> list[dict[str, Any]]:
    rows = []
    spread = tick.ask - tick.bid
    for signal in signals:
        if not signal.setup or signal.direction not in {"BUY", "SELL"}:
            continue
        entry = tick.ask if signal.direction == "BUY" else tick.bid
        stop_distance = max(spread * 3, 1e-6)
        stop = entry - stop_distance if signal.direction == "BUY" else entry + stop_distance
        for rr in RR_MATRIX:
            target = entry + stop_distance * rr if signal.direction == "BUY" else entry - stop_distance * rr
            rows.append(
                {
                    "strategy_id": signal.strategy_id,
                    "direction": signal.direction,
                    "entry": entry,
                    "SL": stop,
                    "TP": target,
                    "RR": rr,
                    "max_hold": signal.max_hold,
                    "TP_SL_expiry": "UNRESOLVED",
                    "gross_R": None,
                    "spread_cost_R": spread / stop_distance,
                    "commission": "COMMISSION_UNAVAILABLE",
                    "net_R": None,
                }
            )
    return rows


def _build_completed_context(candles_by_tf: dict[str, list[Candle]], decision_timestamp: datetime) -> dict[str, list[Candle]]:
    return {
        "M1": completed_candles(candles_by_tf.get("M1", []), "M1", decision_timestamp),
        "M5": completed_candles(candles_by_tf.get("M5", []), "M5", decision_timestamp),
        "M15": completed_candles(candles_by_tf.get("M15", []), "M15", decision_timestamp),
        "H1": completed_candles(candles_by_tf.get("H1", []), "H1", decision_timestamp),
        "H6": completed_candles(candles_by_tf.get("H6", []), "H6", decision_timestamp),
        "H12": completed_candles(candles_by_tf.get("H12", []), "H12", decision_timestamp),
    }


def _range_state(candles: list[Candle]) -> dict[str, Any]:
    if not candles:
        return {"state": "UNKNOWN"}
    recent = candles[-30:]
    high = max(c.high for c in recent)
    low = min(c.low for c in recent)
    close = candles[-1].close
    midpoint = (high + low) / 2
    return {"range_high": high, "range_low": low, "midpoint": midpoint, "position": "UPPER_RANGE" if close > midpoint else "LOWER_RANGE"}


def build_research_snapshot(candles_by_tf: dict[str, list[Candle]], decision_timestamp: datetime, tick: TickQuote) -> dict[str, Any]:
    tick.validate(decision_timestamp)
    completed = _build_completed_context(candles_by_tf, decision_timestamp)
    missing = [tf for tf, rows in completed.items() if not rows]
    if missing:
        raise ValueError(f"missing completed candles: {missing}")
    m1 = completed["M1"]
    m5 = completed["M5"]
    confluence = build_confluence_vector(completed, decision_timestamp, tick)
    context = {
        "policy_version": POLICY_VERSION,
        "policy_hash": POLICY_HASH,
        "strategy_version": STRATEGY_VERSION,
        "deployment_status": DEPLOYMENT_STATUS,
        "decision_timestamp": _utc(decision_timestamp).isoformat(),
        "time_horizons": time_horizon_policies(),
        "candlestick_patterns": {tf: detect_candlestick_patterns(rows, tf) for tf, rows in completed.items()},
        "indicators": {tf: indicators(rows) for tf, rows in completed.items()},
        "mtf_alignment": mtf_alignment(completed),
        "market_structure": market_structure(m1),
        "BOS": bos(m1),
        "CHOCH": choch(m1),
        "support_resistance": support_resistance(m1, decision_timestamp),
        "liquidity": liquidity(m1),
        "liquidity_sweeps": liquidity_sweeps(m1, liquidity(m1)),
        "breakout_buildup": breakout_buildup(m1, support_resistance(m1, decision_timestamp)),
        "FVG": fair_value_gaps(m1),
        "order_blocks": order_blocks(m1, bos(m1)),
        "supply_demand": supply_demand_zones(m1),
        "fibonacci": fibonacci(m1),
        "harmonics": harmonic_patterns(completed["H1"]),
        "range_state": _range_state(m5),
        "session": session_context(decision_timestamp, m1),
        "news_context": news_context(),
        "carry_context": carry_context(),
        "cost_discipline": cost_discipline(tick),
        "confluence_reference": {
            "policy_version": confluence["policy_version"],
            "policy_hash": confluence["policy_hash"],
            "setup_count": len(confluence["confluence_setups"]),
        },
    }
    registry = default_registry()
    signals = registry.evaluate(context)
    return {
        **context,
        "strategy_registry": [asdict(item) for item in registry.definitions()],
        "strategy_signals": [asdict(item) for item in signals],
        "shadow_outcomes": shadow_outcomes(signals, tick),
        "sample_gates": {
            "INSUFFICIENT": "<100",
            "EARLY": "100-249",
            "PRELIMINARY": "250-499",
            "MEANINGFUL": "500-999",
            "STRONGER": "1000+",
        },
        "walk_forward_requirement": {
            "chronological": True,
            "folds": 4,
            "purge_embargo": True,
            "random_shuffle": False,
        },
    }


def sample_gate(sample_count: int) -> str:
    if sample_count < 100:
        return "INSUFFICIENT"
    if sample_count < 250:
        return "EARLY"
    if sample_count < 500:
        return "PRELIMINARY"
    if sample_count < 1000:
        return "MEANINGFUL"
    return "STRONGER"
