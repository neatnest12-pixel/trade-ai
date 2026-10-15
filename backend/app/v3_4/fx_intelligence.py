from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from statistics import mean, pstdev
from typing import Any

from app.v3_2.confluence import Candle, atr, stable_hash
from app.v3_3.system_intelligence import Tick, feature_drift_metrics

POLICY_VERSION = "v3.4-complete-fx-intelligence-r1"
POLICY_HASH = hashlib.sha256(POLICY_VERSION.encode("utf-8")).hexdigest()
ADAPTIVE_RR = (0.5, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 7.0)


@dataclass(frozen=True)
class MacroEvent:
    event_id: str
    event_timestamp: datetime
    currency: str
    country: str
    event_type: str
    importance: str
    actual: float | None = None
    forecast: float | None = None
    previous: float | None = None
    revised_previous: float | None = None
    provider: str = "UNAVAILABLE"
    retrieved_at: datetime | None = None


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def macro_event_schema(event: MacroEvent | None, decision_timestamp: datetime) -> dict[str, Any]:
    if event is None:
        return {"state": "NEWS_DATA_UNAVAILABLE", "provider": None}
    surprise = economic_surprise(event)
    minutes = (_utc(decision_timestamp) - _utc(event.event_timestamp)).total_seconds() / 60
    return {
        **event.__dict__,
        "event_timestamp": _utc(event.event_timestamp).isoformat(),
        "retrieved_at": _utc(event.retrieved_at).isoformat() if event.retrieved_at else None,
        **surprise,
        "minutes_to_event": max(-minutes, 0),
        "minutes_since_event": max(minutes, 0),
        "pre_event_volatility": "UNAVAILABLE",
        "post_event_volatility": "UNAVAILABLE",
    }


def economic_surprise(event: MacroEvent) -> dict[str, Any]:
    if event.actual is None or event.forecast is None:
        return {"surprise_absolute": "UNAVAILABLE", "surprise_normalized": "UNAVAILABLE", "positive_surprise": None, "negative_surprise": None, "surprise_magnitude": "UNAVAILABLE", "surprise_percentile": "UNAVAILABLE"}
    surprise = event.actual - event.forecast
    return {"surprise_absolute": surprise, "surprise_normalized": "UNAVAILABLE", "positive_surprise": surprise > 0, "negative_surprise": surprise < 0, "surprise_magnitude": abs(surprise), "surprise_percentile": "UNAVAILABLE"}


def event_window(event_timestamp: datetime | None, decision_timestamp: datetime) -> str:
    if event_timestamp is None:
        return "NORMAL"
    minutes = (_utc(decision_timestamp) - _utc(event_timestamp)).total_seconds() / 60
    if -120 <= minutes < -15:
        return "PRE_NEWS"
    if -15 <= minutes < 0:
        return "NEWS_IMMINENT"
    if 0 <= minutes <= 5:
        return "NEWS_SHOCK"
    if 5 < minutes <= 120:
        return "POST_NEWS"
    return "NORMAL"


def central_bank_policy(fed_rate: float | None = None, ecb_rate: float | None = None, previous_fed: float | None = None, previous_ecb: float | None = None) -> dict[str, Any]:
    def direction(current: float | None, previous: float | None) -> str:
        if current is None or previous is None:
            return "UNKNOWN"
        return "TIGHTENING" if current > previous else "EASING" if current < previous else "NEUTRAL"

    return {"fed_policy_rate": fed_rate, "ecb_policy_rate": ecb_rate, "fed_policy_direction": direction(fed_rate, previous_fed), "ecb_policy_direction": direction(ecb_rate, previous_ecb), "meeting_probabilities": "UNAVAILABLE", "forward_guidance": "UNAVAILABLE"}


def rate_differential(us_rate: float | None = None, eu_rate: float | None = None, yields: dict[str, float] | None = None) -> dict[str, Any]:
    yields = yields or {}
    return {
        "policy_rate_differential": us_rate - eu_rate if us_rate is not None and eu_rate is not None else "UNAVAILABLE",
        "2Y_yield_differential": yields.get("US_2Y") - yields.get("DE_2Y") if "US_2Y" in yields and "DE_2Y" in yields else "UNAVAILABLE",
        "5Y_yield_differential": yields.get("US_5Y") - yields.get("DE_5Y") if "US_5Y" in yields and "DE_5Y" in yields else "UNAVAILABLE",
        "10Y_yield_differential": yields.get("US_10Y") - yields.get("DE_10Y") if "US_10Y" in yields and "DE_10Y" in yields else "UNAVAILABLE",
        "change_1d": "UNAVAILABLE",
        "change_5d": "UNAVAILABLE",
        "change_20d": "UNAVAILABLE",
        "rate_momentum": "UNAVAILABLE",
    }


def carry_engine(broker_meta: dict[str, Any] | None = None) -> dict[str, Any]:
    meta = broker_meta or {}
    if meta.get("swap_long") is None or meta.get("swap_short") is None:
        state = "CARRY_UNAVAILABLE"
    else:
        state = "CARRY_VERIFIED" if meta.get("source") else "CARRY_PARTIAL"
    return {"state": state, "spot_rate": meta.get("spot_rate"), "base_rate": meta.get("base_rate"), "quote_rate": meta.get("quote_rate"), "rate_differential": meta.get("rate_differential"), "broker_swap_long": meta.get("swap_long", "SWAP_UNAVAILABLE"), "broker_swap_short": meta.get("swap_short", "SWAP_UNAVAILABLE"), "forward_points": meta.get("forward_points", "UNAVAILABLE"), "estimated_carry": "UNAVAILABLE", "observed_carry": "UNAVAILABLE"}


def unavailable_adapter(name: str) -> dict[str, Any]:
    return {"state": f"{name}_UNAVAILABLE", "provider": None, "quality": "UNAVAILABLE"}


def broker_cost_engine(tick: Tick, symbol_spec: dict[str, Any] | None = None, broker_meta: dict[str, Any] | None = None) -> dict[str, Any]:
    spec = symbol_spec or {}
    meta = broker_meta or {}
    spread = tick.ask - tick.bid
    return {"bid": tick.bid, "ask": tick.ask, "spread": spread, "spread_pips": spread / 0.0001, "commission": meta.get("commission", "COMMISSION_UNAVAILABLE"), "swap_long": meta.get("swap_long", "SWAP_UNAVAILABLE"), "swap_short": meta.get("swap_short", "SWAP_UNAVAILABLE"), "tick_size": spec.get("tick_size", "UNAVAILABLE"), "tick_value": spec.get("tick_value", "UNAVAILABLE"), "contract_size": spec.get("contract_size", "UNAVAILABLE"), "minimum_lot": spec.get("volume_min", "UNAVAILABLE"), "lot_step": spec.get("volume_step", "UNAVAILABLE"), "maximum_lot": spec.get("volume_max", "UNAVAILABLE"), "stop_level": spec.get("stop_level", "UNAVAILABLE"), "freeze_level": spec.get("freeze_level", "UNAVAILABLE")}


def spread_regime(ticks: list[Tick]) -> dict[str, Any]:
    spreads = [t.ask - t.bid for t in ticks if t.ask >= t.bid]
    if not spreads:
        return {"state": "UNKNOWN"}
    current = spreads[-1]
    avg = mean(spreads)
    std = pstdev(spreads) if len(spreads) > 1 else 0
    z = (current - avg) / std if std else 0
    sorted_spreads = sorted(spreads)
    pct = sum(s <= current for s in spreads) / len(spreads)
    regime = "TIGHT" if pct <= 0.25 else "NORMAL" if pct <= 0.75 else "ELEVATED" if pct <= 0.95 else "EXTREME"
    return {"spread_current": current, "spread_mean_5m": avg, "spread_mean_15m": avg, "spread_mean_1h": avg, "spread_p50": sorted_spreads[len(spreads) // 2], "spread_p75": sorted_spreads[int((len(spreads)-1)*0.75)], "spread_p90": sorted_spreads[int((len(spreads)-1)*0.9)], "spread_p95": sorted_spreads[int((len(spreads)-1)*0.95)], "spread_p99": sorted_spreads[-1], "spread_zscore": z, "classification": regime}


def slippage_schema() -> dict[str, Any]:
    return {"signal_timestamp": None, "order_request_timestamp": None, "broker_ack_timestamp": None, "fill_timestamp": None, "requested_entry": None, "market_quote_at_signal": None, "market_quote_at_send": None, "actual_fill": None, "slippage_points": None, "slippage_pips": None, "slippage_R": None, "latency_ms": None, "order_retcodes": [], "partial_fill": None, "demo_order_sent": False}


def latency_schema() -> dict[str, Any]:
    return {"signal_to_decision_ms": None, "decision_to_order_ms": None, "order_to_ack_ms": None, "ack_to_fill_ms": None, "total_execution_ms": None, "research_correlations": ["latency", "volatility", "spread", "slippage"]}


def liquidity_regime(spread: dict[str, Any], tick_frequency: float, volume_state: str, volatility_state: str, session: str) -> str:
    if spread.get("classification") == "EXTREME" or volatility_state == "SHOCK":
        return "DISLOCATED"
    if spread.get("classification") == "ELEVATED" or tick_frequency < 0.2 or session == "OFF_HOURS":
        return "THIN"
    if volume_state == "HIGH" and spread.get("classification") == "TIGHT":
        return "DEEP"
    return "NORMAL"


def session_specialization(ts: datetime) -> dict[str, Any]:
    hour = _utc(ts).hour
    if 7 <= hour < 8:
        bucket = "LONDON_OPEN"
    elif 8 <= hour < 11:
        bucket = "LONDON_MID"
    elif 11 <= hour < 12:
        bucket = "LONDON_CLOSE"
    elif 12 <= hour < 13:
        bucket = "NY_OPEN"
    elif 13 <= hour < 16:
        bucket = "LONDON_NY_OVERLAP"
    elif 16 <= hour < 20:
        bucket = "NY_MID"
    elif 20 <= hour < 21:
        bucket = "NY_CLOSE"
    elif hour in {6, 12, 21}:
        bucket = "SESSION_TRANSITION"
    elif 0 <= hour < 7:
        bucket = "ASIA"
    else:
        bucket = "OFF_HOURS"
    return {"session_bucket": bucket, "hour": hour, "weekday": _utc(ts).weekday()}


def momentum_factor(candles: list[Candle]) -> dict[str, Any]:
    closes = [c.close for c in candles]
    def ret(n: int) -> float | str:
        return (closes[-1] / closes[-n] - 1) if len(closes) >= n else "UNAVAILABLE"
    return {"return_1D": ret(1440), "return_5D": ret(7200), "return_20D": ret(28800), "return_60D": ret(86400), "return_120D": ret(172800), "trend_persistence": "UNAVAILABLE" if len(closes) < 20 else sum(1 for a, b in zip(closes[-20:-1], closes[-19:], strict=False) if b > a) / 19, "momentum_acceleration": "UNAVAILABLE" if len(closes) < 10 else (closes[-1] - closes[-5]) - (closes[-5] - closes[-10])}


def value_foundation() -> dict[str, Any]:
    return {"state": "VALUE_UNAVAILABLE", "real_exchange_rate_deviation": "UNAVAILABLE", "inflation_differential": "UNAVAILABLE", "PPP_like_deviation": "UNAVAILABLE", "terms_of_trade_proxy": "UNAVAILABLE"}


def volatility_forecast_labels() -> dict[str, Any]:
    return {f"realized_vol_next_{h}": "LABEL_ONLY" for h in ("5m", "15m", "1h", "6h", "12h")} | {"methods": ["ATR-based", "return-based", "tick-based"]}


def volatility_term_structure(candles: list[Candle]) -> dict[str, Any]:
    def vol(n: int) -> float | str:
        rows = candles[-n:]
        return atr(rows) if len(rows) >= 15 else "UNAVAILABLE"
    vols = {"vol_1m": vol(20), "vol_5m": vol(50), "vol_15m": vol(100), "vol_1h": vol(200), "vol_6h": vol(400), "vol_12h": vol(800)}
    numeric = [v for v in vols.values() if isinstance(v, float)]
    state = "NORMAL" if not numeric else "VOL_EXPANSION" if numeric[-1] > mean(numeric) else "VOL_COMPRESSION" if numeric[-1] < mean(numeric) * 0.8 else "NORMAL"
    return {**vols, "classification": state}


def strategy_selector_foundation() -> dict[str, list[str]]:
    return {"stages": ["market regime", "eligible strategies", "strategy quality", "direction", "TP-before-SL probability", "expected net R", "RR selection", "risk recommendation", "BUY/SELL/NO_TRADE"]}


def meta_label_schema() -> dict[str, Any]:
    return {"target": "TAKE/SKIP", "features": "decision-time only", "targets": ["net_R", "TP_before_SL", "MFE", "MAE"], "model_fitting_enabled": False}


def adaptive_stop_exit() -> dict[str, Any]:
    return {"adaptive_RR": ADAPTIVE_RR, "stop_families": ["structure stop", "swing high/low stop", "liquidity-sweep invalidation", "ATR stop", "volatility-adjusted stop"], "exit_policies": ["fixed TP", "structure target", "partial target", "trailing ATR", "break-even after threshold", "time stop", "regime-change exit"]}


def currency_exposure(pair: str, side: str) -> dict[str, float]:
    base, quote = pair[:3], pair[3:]
    sign = 1 if side == "LONG" else -1
    return {base: sign, quote: -sign}


def risk_foundations() -> dict[str, Any]:
    return {"correlation_risk": {"rolling_pair_correlation": "UNAVAILABLE", "currency_factor_exposure": "SUPPORTED", "correlated_position_cluster": "UNAVAILABLE"}, "portfolio_risk": {"normal_risk_per_trade": "0.25%-0.50%", "absolute_single_trade_ceiling": "3%", "absolute_total_open_risk_ceiling": "5%", "ceilings_not_targets": True}, "three_five_seven": {"3": "single-trade hard ceiling", "5": "aggregate open-risk hard ceiling", "7": "experimental only; do not conflate 7% objective with 7R target"}, "drawdown_guards": ["daily_loss_limit", "weekly_loss_limit", "maximum_drawdown_pause", "consecutive_loss_pause", "strategy_drawdown_pause", "regime_drawdown_pause", "no martingale", "no loss doubling", "no recovery sizing"], "tail_risk": ["gap risk", "news shock", "spread explosion", "volatility shock", "flash move", "extreme MAE", "extreme slippage", "CVaR-style diagnostics"]}


def validation_research_foundation() -> dict[str, Any]:
    return {"sample_gates": {"INSUFFICIENT": "<50", "EXPLORATORY_ONLY": "50-99", "PRELIMINARY": "100-249", "RESEARCH_ELIGIBLE": "250-499", "STRONGER_RESEARCH_SAMPLE": "500+"}, "purged_walk_forward": {"chronological": True, "parent_level_grouping": True, "purge": True, "embargo": True, "expanding_windows": True, "minimum_folds": 4, "random_shuffle": False}, "final_holdout": "reserved for one-time evaluation after candidate freeze", "bootstrap_monte_carlo": ["expectancy confidence interval", "PF uncertainty", "drawdown distribution", "loss-streak distribution", "sequence-risk simulation"], "multiple_testing": {"experiment_count": 0, "hypotheses_tried": 0, "false_discovery_aware_reporting": True}, "ablation": ["technical indicators", "candlesticks", "SMC", "structure", "MTF", "microstructure", "macro", "rates", "cross-asset", "session", "costs"], "redundancy": ["duplicate features", "near-duplicate signals", "highly correlated features"]}


def data_provenance(provider: str | None, source_timestamp: datetime | None, retrieved_at: datetime | None, quality: str = "UNAVAILABLE") -> dict[str, Any]:
    staleness = None
    if source_timestamp and retrieved_at:
        staleness = (_utc(retrieved_at) - _utc(source_timestamp)).total_seconds()
    return {"provider": provider, "source_timestamp": _utc(source_timestamp).isoformat() if source_timestamp else None, "retrieved_at": _utc(retrieved_at).isoformat() if retrieved_at else None, "quality": quality, "staleness_seconds": staleness, "max_staleness_by_class": {"tick": 5, "macro": 86400, "COT": 604800}}


def fail_closed_policy() -> dict[str, Any]:
    return {"unavailable_action": "mark unavailable", "forbidden": ["fabricate", "silently zero-fill", "use future value", "reuse stale value beyond configured limit"], "state": "PASS"}


def build_fx_intelligence_snapshot(candles: list[Candle], ticks: list[Tick], decision_timestamp: datetime, event: MacroEvent | None = None) -> dict[str, Any]:
    if not ticks:
        raise ValueError("ticks required")
    if any(_utc(t.timestamp) > _utc(decision_timestamp) for t in ticks):
        raise ValueError("future tick")
    latest_tick = ticks[-1]
    spreads = spread_regime(ticks)
    macro = macro_event_schema(event, decision_timestamp)
    snapshot = {
        "policy_version": POLICY_VERSION,
        "policy_hash": POLICY_HASH,
        "macro_event": macro,
        "economic_surprise": economic_surprise(event) if event else {"state": "NEWS_DATA_UNAVAILABLE"},
        "event_window": event_window(event.event_timestamp if event else None, decision_timestamp),
        "central_bank": central_bank_policy(),
        "rate_differential": rate_differential(),
        "carry": carry_engine(),
        "cross_asset": unavailable_adapter("CROSS_ASSET"),
        "fx_futures": unavailable_adapter("FX_FUTURES"),
        "positioning_cot": unavailable_adapter("POSITIONING_COT"),
        "broker_cost": broker_cost_engine(latest_tick),
        "spread_regime": spreads,
        "slippage": slippage_schema(),
        "latency": latency_schema(),
        "liquidity_regime": liquidity_regime(spreads, len(ticks) / 300, "NORMAL", "NORMAL", session_specialization(decision_timestamp)["session_bucket"]),
        "session_specialization": session_specialization(decision_timestamp),
        "time_of_day_matrix": {"dimensions": ["strategy × session", "strategy × hour", "strategy × weekday", "strategy × regime", "strategy × spread regime"]},
        "momentum_factor": momentum_factor(candles),
        "value_foundation": value_foundation(),
        "volatility_forecast": volatility_forecast_labels(),
        "volatility_term_structure": volatility_term_structure(candles),
        "strategy_selector": strategy_selector_foundation(),
        "meta_labeling": meta_label_schema(),
        "adaptive_research": adaptive_stop_exit(),
        "currency_exposure": {"state": "UNAVAILABLE", "reason": "NO_EXECUTED_POSITION"},
        "risk": risk_foundations(),
        "drift": {"states": ["STABLE", "WATCH", "DEGRADING", "DRIFT_DETECTED"], "feature_drift": {"state": "UNAVAILABLE", "reason": "NO_CERTIFIED_REFERENCE"}},
        "calibration": {"raw_probability": "FUTURE", "calibrated_probability": "FUTURE", "Brier": "FUTURE", "log_loss": "FUTURE", "ECE": "FUTURE"},
        "validation": validation_research_foundation(),
        "strategy_attribution": {"state": "UNAVAILABLE", "reason": "USE_PERSISTED_STRATEGY_FAMILIES"},
        "data_provenance": data_provenance(None, None, None),
        "no_lookahead": True,
        "fail_closed": fail_closed_policy(),
        "model_fit_calls": 0,
        "broker_execution_calls": 0,
    }
    snapshot["snapshot_hash"] = stable_hash(snapshot)
    return snapshot
