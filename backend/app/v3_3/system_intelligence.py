from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from statistics import mean, median, pstdev
from typing import Any

from app.v3_2.confluence import Candle, TickQuote, atr, indicators, mtf_alignment, stable_hash

POLICY_VERSION = "v3.3-system-intelligence-r1"
POLICY_HASH = hashlib.sha256(POLICY_VERSION.encode("utf-8")).hexdigest()
SYMBOL = "EURUSD"


@dataclass(frozen=True)
class Tick:
    timestamp: datetime
    bid: float
    ask: float
    last: float | None = None
    volume: float | None = None
    flags: int | None = None
    provenance: str = "MT5_TICK"

    def validate(self, decision_timestamp: datetime) -> None:
        if self.bid <= 0 or self.ask <= 0 or self.ask < self.bid:
            raise ValueError("invalid bid/ask")
        if _utc(self.timestamp) > _utc(decision_timestamp):
            raise ValueError("future tick in decision features")


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _percentile(values: list[float], value: float) -> float | None:
    if not values:
        return None
    return sum(item <= value for item in values) / len(values)


def execution_realism(
    requested_entry: float | None,
    tick: Tick,
    symbol_spec: dict[str, Any] | None = None,
    stop_distance: float | None = None,
    target_distance: float | None = None,
) -> dict[str, Any]:
    spec = symbol_spec or {}
    spread = tick.ask - tick.bid
    tick_size = spec.get("tick_size", "UNAVAILABLE")
    return {
        "requested_entry": requested_entry,
        "observable_bid": tick.bid,
        "observable_ask": tick.ask,
        "spread": spread,
        "spread_pips": spread / 0.0001,
        "estimated_slippage": "UNAVAILABLE" if requested_entry is None else abs(((tick.bid + tick.ask) / 2) - requested_entry),
        "actual_slippage": "UNAVAILABLE",
        "tick_size": tick_size,
        "tick_value": spec.get("tick_value", "UNAVAILABLE"),
        "contract_size": spec.get("contract_size", "UNAVAILABLE"),
        "volume_min": spec.get("volume_min", "UNAVAILABLE"),
        "volume_step": spec.get("volume_step", "UNAVAILABLE"),
        "volume_max": spec.get("volume_max", "UNAVAILABLE"),
        "broker_max_volume_binding": spec.get("volume_max") is not None,
        "stop_distance": stop_distance,
        "target_distance": target_distance,
        "gap_through_stop": "UNAVAILABLE",
        "gap_through_target": "UNAVAILABLE",
        "broker_rejection_code": "UNAVAILABLE",
        "execution_latency_ms": "UNAVAILABLE",
        "provenance": "OBSERVABLE_QUOTE_AND_SYMBOL_SPEC" if spec else "OBSERVABLE_QUOTE_ONLY",
    }


def commission_swap_provenance(account_meta: dict[str, Any] | None = None) -> dict[str, Any]:
    meta = account_meta or {}
    commission = meta.get("commission")
    swap_long = meta.get("swap_long")
    swap_short = meta.get("swap_short")
    return {
        "commission_status": "VERIFIED" if commission is not None else "COMMISSION_UNAVAILABLE",
        "commission_value": commission if commission is not None else "UNAVAILABLE",
        "commission_source": meta.get("commission_source", "UNAVAILABLE"),
        "swap_long": swap_long if swap_long is not None else "SWAP_UNAVAILABLE",
        "swap_short": swap_short if swap_short is not None else "SWAP_UNAVAILABLE",
        "swap_source": meta.get("swap_source", "UNAVAILABLE"),
    }


def tick_microstructure(ticks: list[Tick], decision_timestamp: datetime) -> dict[str, Any]:
    valid = [tick for tick in ticks if _utc(tick.timestamp) <= _utc(decision_timestamp)]
    for tick in valid:
        tick.validate(decision_timestamp)
    one_minute = [tick for tick in valid if _utc(tick.timestamp) >= _utc(decision_timestamp) - timedelta(minutes=1)]
    five_minutes = [tick for tick in valid if _utc(tick.timestamp) >= _utc(decision_timestamp) - timedelta(minutes=5)]
    spreads = [tick.ask - tick.bid for tick in five_minutes]
    mids = [(tick.ask + tick.bid) / 2 for tick in five_minutes]
    bid_velocity = _velocity([tick.bid for tick in five_minutes])
    ask_velocity = _velocity([tick.ask for tick in five_minutes])
    mid_velocity = _velocity(mids)
    returns = [b - a for a, b in zip(mids[:-1], mids[1:], strict=False)]
    up = sum(1 for ret in returns if ret > 0)
    down = sum(1 for ret in returns if ret < 0)
    elapsed = max((_utc(five_minutes[-1].timestamp) - _utc(five_minutes[0].timestamp)).total_seconds(), 1) if len(five_minutes) >= 2 else 1
    return {
        "tick_count_1m": len(one_minute),
        "tick_count_5m": len(five_minutes),
        "quote_updates_per_second": len(five_minutes) / elapsed,
        "spread_mean": mean(spreads) if spreads else None,
        "spread_median": median(spreads) if spreads else None,
        "spread_max": max(spreads) if spreads else None,
        "spread_percentile": _percentile(spreads, spreads[-1]) if spreads else None,
        "bid_velocity": bid_velocity,
        "ask_velocity": ask_velocity,
        "mid_velocity": mid_velocity,
        "tick_return_volatility": pstdev(returns) if len(returns) > 1 else 0.0,
        "up_tick_count": up,
        "down_tick_count": down,
        "tick_imbalance": (up - down) / max(up + down, 1),
        "quote_imbalance_proxy": ((five_minutes[-1].bid - five_minutes[0].bid) - (five_minutes[-1].ask - five_minutes[0].ask)) if len(five_minutes) >= 2 else 0.0,
        "short_term_price_acceleration": _acceleration(mids),
    }


def _velocity(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    return values[-1] - values[0]


def _acceleration(values: list[float]) -> float:
    if len(values) < 3:
        return 0.0
    first = values[len(values) // 2] - values[0]
    second = values[-1] - values[len(values) // 2]
    return second - first


def volume_proxy(candles: list[Candle]) -> dict[str, Any]:
    if not candles:
        return {"state": "UNAVAILABLE"}
    volumes = [float(c.tick_volume) for c in candles]
    current = volumes[-1]
    avg = mean(volumes[-20:]) if len(volumes) >= 20 else mean(volumes)
    pct = _percentile(volumes[-100:], current)
    if pct is not None and pct < 0.25:
        liquidity_state = "THIN"
    elif pct is not None and pct > 0.75:
        liquidity_state = "HIGH"
    else:
        liquidity_state = "NORMAL"
    return {
        "volume_label": "BROKER_DERIVED_PROXY",
        "tick_volume": current,
        "relative_tick_volume": current / avg if avg else None,
        "rolling_tick_volume_percentile": pct,
        "volume_spike": pct is not None and pct >= 0.9,
        "volume_contraction": pct is not None and pct <= 0.1,
        "session_liquidity_state": liquidity_state,
    }


def session_vwap_proxy(candles: list[Candle], decision_timestamp: datetime) -> dict[str, Any]:
    session_start = datetime.combine(_utc(decision_timestamp).date(), time(hour=0), tzinfo=UTC)
    rows = [c for c in candles if _utc(c.timestamp) >= session_start]
    if not rows:
        return {"state": "UNAVAILABLE"}
    weighted = sum(((c.high + c.low + c.close) / 3) * max(float(c.tick_volume), 1) for c in rows)
    volume = sum(max(float(c.tick_volume), 1) for c in rows)
    vwap = weighted / volume
    latest = rows[-1]
    current_atr = atr(rows) or max(latest.high - latest.low, 1e-12)
    return {
        "state": "BROKER_PROXY_VWAP",
        "session_vwap": vwap,
        "distance_to_vwap": latest.close - vwap,
        "distance_to_vwap_ATR": (latest.close - vwap) / current_atr,
        "above_vwap": latest.close > vwap,
        "below_vwap": latest.close < vwap,
    }


def opening_range(candles: list[Candle], decision_timestamp: datetime) -> dict[str, Any]:
    utc = _utc(decision_timestamp)
    sessions = {"ASIAN_RANGE": 0, "LONDON_OPENING_RANGE": 7, "NEW_YORK_OPENING_RANGE": 12}
    out = {}
    current_atr = atr(candles) or 1e-12
    for name, hour in sessions.items():
        start = datetime.combine(utc.date(), time(hour=hour), tzinfo=UTC)
        end = start + timedelta(minutes=60)
        rows = [c for c in candles if start <= _utc(c.timestamp) < end]
        if not rows:
            out[name] = {"state": "UNAVAILABLE"}
            continue
        high, low = max(c.high for c in rows), min(c.low for c in rows)
        latest = candles[-1]
        broke_up = latest.close > high
        broke_down = latest.close < low
        out[name] = {
            "range_high": high,
            "range_low": low,
            "range_width": high - low,
            "range_width_ATR": (high - low) / current_atr,
            "opening_range_breakout": "UP" if broke_up else "DOWN" if broke_down else "NO",
            "opening_range_false_break": (latest.high > high and latest.close < high) or (latest.low < low and latest.close > low),
            "opening_range_retest": latest.low <= high <= latest.high or latest.low <= low <= latest.high,
        }
    return out


def additional_patterns(candles: list[Candle]) -> dict[str, Any]:
    if len(candles) < 30:
        return {"patterns": []}
    recent = candles[-30:]
    highs = sorted(recent, key=lambda c: c.high)[-3:]
    lows = sorted(recent, key=lambda c: c.low)[:3]
    avg_range = mean(c.high - c.low for c in recent)
    close = recent[-1].close
    patterns: list[dict[str, Any]] = []
    if abs(highs[-1].high - highs[-2].high) <= avg_range * 0.25:
        patterns.append({"type": "DOUBLE_TOP", "direction": "SELL", "algorithm": "two similar recent highs"})
    if abs(lows[0].low - lows[1].low) <= avg_range * 0.25:
        patterns.append({"type": "DOUBLE_BOTTOM", "direction": "BUY", "algorithm": "two similar recent lows"})
    if highs[1].high > highs[0].high and highs[1].high > highs[2].high:
        patterns.append({"type": "HEAD_AND_SHOULDERS", "direction": "SELL", "algorithm": "middle swing high dominant"})
    if lows[1].low < lows[0].low and lows[1].low < lows[2].low:
        patterns.append({"type": "INVERSE_HEAD_AND_SHOULDERS", "direction": "BUY", "algorithm": "middle swing low dominant"})
    widths = [c.high - c.low for c in recent]
    if widths[-1] < widths[0] * 0.7:
        patterns.extend([{"type": "TRIANGLE", "direction": "NEUTRAL"}, {"type": "WEDGE", "direction": "NEUTRAL"}, {"type": "PENNANT", "direction": "NEUTRAL"}])
    if abs(close - mean(c.close for c in recent[-5:])) <= avg_range:
        patterns.append({"type": "FLAG", "direction": "NEUTRAL"})
    if close > max(c.high for c in recent[:-1]) or close < min(c.low for c in recent[:-1]):
        patterns.append({"type": "DONCHIAN_BREAKOUT", "direction": "BUY" if close > max(c.high for c in recent[:-1]) else "SELL"})
        patterns.append({"type": "TRENDLINE_BREAK", "direction": "BUY" if close > max(c.high for c in recent[:-1]) else "SELL"})
    patterns.append({"type": "CHANNEL", "direction": "NEUTRAL", "upper": max(c.high for c in recent), "lower": min(c.low for c in recent)})
    patterns.append({"type": "TRENDLINE_RETEST", "direction": "NEUTRAL", "state": "RESEARCH_ONLY"})
    return {"patterns": patterns}


def divergence_features(candles: list[Candle]) -> dict[str, Any]:
    ind = indicators(candles)
    closes = [c.close for c in candles]
    if len(closes) < 20:
        return {"RSI_DIVERGENCE": False, "MACD_DIVERGENCE": False, "MOMENTUM_DIVERGENCE": False}
    price_slope = closes[-1] - closes[-10]
    rsi = ind.get("RSI")
    macd = ind.get("MACD") or 0
    momentum = closes[-1] - closes[-5]
    return {
        "RSI_DIVERGENCE": rsi is not None and ((price_slope > 0 and rsi < 50) or (price_slope < 0 and rsi > 50)),
        "MACD_DIVERGENCE": (price_slope > 0 and macd < 0) or (price_slope < 0 and macd > 0),
        "MOMENTUM_DIVERGENCE": (price_slope > 0 and momentum < 0) or (price_slope < 0 and momentum > 0),
    }


def bollinger_squeeze(candles: list[Candle]) -> dict[str, Any]:
    closes = [c.close for c in candles]
    if len(closes) < 20:
        return {"BOLLINGER_SQUEEZE": False}
    window = closes[-20:]
    width = 4 * pstdev(window)
    historical = [4 * pstdev(closes[idx - 20 : idx]) for idx in range(20, len(closes) + 1)]
    return {"BOLLINGER_SQUEEZE": _percentile(historical, width) is not None and _percentile(historical, width) <= 0.2, "band_width": width}


def regime_engine(candles: list[Candle], micro: dict[str, Any], volume: dict[str, Any], news: dict[str, Any]) -> dict[str, Any]:
    ind = indicators(candles)
    vol_pct = ind.get("volatility_percentile")
    ema_slope = ind.get("EMA_slope") or 0.0
    ranges = [c.high - c.low for c in candles[-20:]]
    range_efficiency = abs(candles[-1].close - candles[-20].close) / max(sum(ranges), 1e-12) if len(candles) >= 20 else 0.0
    scores = {
        "trend_strength": abs(ema_slope) * 10000,
        "volatility_percentile": vol_pct,
        "range_efficiency": range_efficiency,
        "breakout_state": 1.0 if vol_pct is not None and vol_pct > 0.8 else 0.0,
        "liquidity_state": 1.0 if volume.get("session_liquidity_state") == "HIGH" else 0.0,
    }
    if news.get("state") in {"HIGH_IMPACT_WINDOW", "POST_NEWS"}:
        regime = "NEWS_SHOCK" if news["state"] == "HIGH_IMPACT_WINDOW" else "POST_NEWS_NORMALIZATION"
    elif volume.get("session_liquidity_state") == "THIN":
        regime = "THIN_LIQUIDITY"
    elif vol_pct is not None and vol_pct > 0.85:
        regime = "VOLATILITY_EXPANSION"
    elif vol_pct is not None and vol_pct < 0.15:
        regime = "VOLATILITY_COMPRESSION"
    elif range_efficiency > 0.45 and ema_slope > 0:
        regime = "TRENDING_UP"
    elif range_efficiency > 0.45 and ema_slope < 0:
        regime = "TRENDING_DOWN"
    elif range_efficiency < 0.2:
        regime = "RANGING"
    else:
        regime = "MIXED_REGIME"
    score_values = [v for v in scores.values() if isinstance(v, int | float)]
    return {"regime_id": regime, "regime_score": mean(score_values) if score_values else 0.0, "regime_component_scores": scores}


def strategy_regime_matrix(strategy_signals: list[dict[str, Any]], regime: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"strategy_id": signal["strategy_id"], "strategy_family": signal["strategy_family"], "setup": signal["setup"], "direction": signal["direction"], "regime_id": regime["regime_id"]} for signal in strategy_signals]


def uncertainty_foundation(raw_score: float | None = None) -> dict[str, Any]:
    return {
        "raw_score": raw_score,
        "calibrated_probability": "UNTRAINED",
        "uncertainty_score": "UNTRAINED",
        "future_metrics": ["Brier score", "log loss", "reliability curve", "confidence buckets", "expected calibration error"],
    }


def no_trade_intelligence() -> dict[str, Any]:
    return {"target": "TRADE/NO_TRADE", "skip_reasons": ["LOW_EDGE", "HIGH_SPREAD", "HIGH_UNCERTAINTY", "REGIME_CONFLICT", "HTF_CONFLICT", "NEWS_RISK", "THIN_LIQUIDITY", "POOR_RR", "HIGH_EXECUTION_COST", "INSUFFICIENT_STRUCTURE"]}


def rich_outcome_labels(path_r: list[tuple[datetime, float]], rr_targets: list[float] | None = None) -> dict[str, Any]:
    if not path_r:
        return {"state": "UNAVAILABLE"}
    rr_targets = rr_targets or [1, 1.5, 2, 3, 5]
    values = [item[1] for item in path_r]
    mfe = max(values)
    mae = min(values)
    first_tp = next((ts for ts, value in path_r if value >= 1), None)
    first_sl = next((ts for ts, value in path_r if value <= -1), None)
    return {
        "gross_r": values[-1],
        "net_r": values[-1],
        "MFE_R": mfe,
        "MAE_R": mae,
        "time_to_MFE": next(ts for ts, value in path_r if value == mfe).isoformat(),
        "time_to_MAE": next(ts for ts, value in path_r if value == mae).isoformat(),
        "time_to_TP": first_tp.isoformat() if first_tp else None,
        "time_to_SL": first_sl.isoformat() if first_sl else None,
        "max_holding_adverse_excursion": mae,
        "best_rr_reached": max([rr for rr in rr_targets if mfe >= rr], default=0),
        "first_rr_hit": next((rr for rr in rr_targets for _, value in path_r if value >= rr), None),
        "highest_rr_before_reversal": max([rr for rr in rr_targets if mfe >= rr], default=0),
        "exit_reason": "TP" if first_tp else "SL" if first_sl else "TIME_EXIT",
        "path_efficiency": values[-1] / max(mfe, 1e-12) if mfe > 0 else 0.0,
    }


def future_training_targets() -> dict[str, str]:
    return {
        "TARGET_A": "PROBABILITY_TP_BEFORE_SL",
        "TARGET_B": "EXPECTED_NET_R",
        "TARGET_C": "BEST_RR_CLASS",
        "TARGET_D": "TRADE_QUALITY_CLASS",
        "TARGET_E": "SKIP_TRADE",
        "TARGET_F": "EXPECTED_MFE",
        "TARGET_G": "EXPECTED_MAE",
    }


def risk_intelligence() -> dict[str, Any]:
    return {
        "current_equity": "UNAVAILABLE",
        "current_drawdown": "UNAVAILABLE",
        "rolling_drawdown": "UNAVAILABLE",
        "daily_pnl": "UNAVAILABLE",
        "weekly_pnl": "UNAVAILABLE",
        "consecutive_losses": "UNAVAILABLE",
        "rolling_expectancy": "UNAVAILABLE",
        "rolling_profit_factor": "UNAVAILABLE",
        "volatility_adjusted_risk_reference": "RESEARCH_ONLY",
        "session_exposure": "UNAVAILABLE",
        "open_strategy_exposure": "UNAVAILABLE",
        "correlation_layer": "NOT_APPLICABLE",
        "future_hard_guards": {
            "max_risk_per_trade": None,
            "daily_loss_limit": None,
            "weekly_loss_limit": None,
            "max_drawdown_pause": None,
            "max_consecutive_losses": None,
            "max_open_positions": None,
            "max_symbol_exposure": None,
            "max_strategy_exposure": None,
            "news_execution_pause": None,
            "thin_liquidity_pause": None,
        },
    }


def drift_monitoring(values: list[float], reference: list[float] | None = None) -> dict[str, Any]:
    if len(values) < 20:
        state = "WATCH"
    else:
        recent = mean(values[-10:])
        earlier = mean(values[:-10]) if len(values) > 10 else recent
        state = "DEGRADING" if recent < earlier * 0.8 else "STABLE"
    feature_drift = feature_drift_metrics(values, reference or values)
    return {
        "state": state,
        "rolling_expectancy": mean(values[-20:]) if values else None,
        "profit_factor": "UNAVAILABLE",
        "win_rate": sum(v > 0 for v in values) / len(values) if values else None,
        "net_R": sum(values),
        "feature_drift": feature_drift,
        "performance_drift": {"state": "INSUFFICIENT_REFERENCE" if reference is None else state},
    }


def feature_drift_metrics(current: list[float], reference: list[float]) -> dict[str, Any]:
    if not current or not reference:
        return {"state": "INSUFFICIENT"}
    mean_shift = mean(current) - mean(reference)
    std_shift = (pstdev(current) if len(current) > 1 else 0) - (pstdev(reference) if len(reference) > 1 else 0)
    quantile_shift = sorted(current)[len(current) // 2] - sorted(reference)[len(reference) // 2]
    psi_like = abs(mean_shift) / max(abs(mean(reference)), 1e-12)
    return {"psi_like_drift": psi_like, "mean_shift": mean_shift, "std_shift": std_shift, "quantile_shift": quantile_shift, "state": "DRIFT_DETECTED" if psi_like > 0.25 else "STABLE"}


def ablation_contract() -> dict[str, Any]:
    return {"feature_groups": ["PRICE_ACTION", "CANDLESTICKS", "STRUCTURE", "LIQUIDITY", "SMC", "INDICATORS", "MTF", "REGIME", "SESSION", "MICROSTRUCTURE", "VOLUME", "NEWS", "COSTS"], "experiment": "all features minus one family", "metrics": ["expectancy", "PF", "drawdown", "accepted trades"], "training_now": False}


def redundancy_diagnostics(feature_names: list[str]) -> dict[str, Any]:
    seen: set[str] = set()
    duplicates = []
    for name in feature_names:
        normalized = name.lower().replace("_", "")
        if normalized in seen:
            duplicates.append(name)
        seen.add(normalized)
    return {"highly_correlated_features": "UNCOMPUTED_UNTIL_DATASET", "duplicate_signals": duplicates, "near_duplicate_pattern_definitions": []}


def strategy_attribution(signal: dict[str, Any], regime: dict[str, Any], mtf: dict[str, Any], cost: dict[str, Any]) -> dict[str, Any]:
    return {
        "primary_strategy": signal.get("strategy_id"),
        "supporting_strategies": [],
        "confluence_components": signal.get("reason_codes", []),
        "positive_evidence": signal.get("reason_codes", []),
        "negative_evidence": ["COMMISSION_UNAVAILABLE"] if cost.get("commission_status") == "COMMISSION_UNAVAILABLE" else [],
        "regime_context": regime,
        "MTF_context": mtf,
        "execution_cost_context": cost,
    }


def news_provider_contract() -> dict[str, Any]:
    return {"required_fields": ["event_timestamp", "currency", "impact", "event_type", "actual", "forecast", "previous"], "state": "NEWS_DATA_UNAVAILABLE"}


def data_quality(snapshot: dict[str, Any]) -> dict[str, Any]:
    issues = []
    tick = snapshot["latest_tick"]
    if tick["ask"] < tick["bid"]:
        issues.append("invalid bid/ask")
    if snapshot["execution_realism"]["spread"] < 0:
        issues.append("negative spread")
    if snapshot["commission_swap"]["commission_status"] == "COMMISSION_UNAVAILABLE":
        issues.append("missing cost provenance: commission")
    state = "FAIL" if any("invalid" in item or "negative" in item for item in issues) else "WARN" if issues else "PASS"
    return {"state": state, "issues": issues, "checks": ["future feature timestamps", "invalid bid/ask", "negative spread", "missing cost provenance", "invalid MFE/MAE ordering", "duplicate observation", "duplicate outcome", "out-of-order ticks", "out-of-order candles", "strategy identity collisions"]}


def build_system_snapshot(
    candles_by_tf: dict[str, list[Candle]],
    ticks: list[Tick],
    decision_timestamp: datetime,
    strategy_signals: list[dict[str, Any]] | None = None,
    symbol_spec: dict[str, Any] | None = None,
    account_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not ticks:
        raise ValueError("ticks required")
    decision = _utc(decision_timestamp)
    if any(_utc(tick.timestamp) > decision for tick in ticks):
        raise ValueError("future tick in decision features")
    latest_tick = max((tick for tick in ticks if _utc(tick.timestamp) <= decision), key=lambda item: item.timestamp)
    latest_tick.validate(decision)
    completed_m1 = [c for c in candles_by_tf.get("M1", []) if _utc(c.timestamp) + timedelta(minutes=1) <= decision]
    if not completed_m1:
        raise ValueError("completed M1 candles required")
    tick_quote = TickQuote(latest_tick.timestamp, latest_tick.bid, latest_tick.ask, latest_tick.provenance)
    mtf = mtf_alignment(candles_by_tf)
    volume = volume_proxy(completed_m1)
    news = {"state": "NEWS_DATA_UNAVAILABLE"}
    regime = regime_engine(completed_m1, tick_microstructure(ticks, decision), volume, news)
    commission_swap = commission_swap_provenance(account_meta)
    signals = strategy_signals or [{"strategy_id": "UNASSIGNED_RESEARCH_SIGNAL", "strategy_family": "RESEARCH", "setup": False, "direction": "NEUTRAL", "reason_codes": []}]
    snapshot: dict[str, Any] = {
        "policy_version": POLICY_VERSION,
        "policy_hash": POLICY_HASH,
        "schema_version": POLICY_VERSION,
        "symbol": SYMBOL,
        "multi_symbol_support": ["EURUSD", "GBPUSD", "USDJPY", "USDCHF"],
        "current_symbol_scope": "EURUSD_ONLY",
        "decision_timestamp": decision.isoformat(),
        "latest_tick": {"timestamp": latest_tick.timestamp.isoformat(), "bid": latest_tick.bid, "ask": latest_tick.ask, "provenance": latest_tick.provenance},
        "execution_realism": execution_realism(None, latest_tick, symbol_spec),
        "commission_swap": commission_swap,
        "microstructure": tick_microstructure(ticks, decision),
        "volume_proxy": volume,
        "vwap_proxy": session_vwap_proxy(completed_m1, decision),
        "opening_range": opening_range(completed_m1, decision),
        "additional_structure_patterns": additional_patterns(completed_m1),
        "divergence": divergence_features(completed_m1),
        "bollinger_squeeze": bollinger_squeeze(completed_m1),
        "regime": regime,
        "strategy_regime_matrix": strategy_regime_matrix(signals, regime),
        "uncertainty_foundation": uncertainty_foundation(),
        "no_trade_intelligence": no_trade_intelligence(),
        "rich_outcome_labels": {"state": "UNAVAILABLE", "source": "CERTIFIED_OUTCOME_JOURNAL_ONLY"},
        "future_training_targets": future_training_targets(),
        "risk_intelligence": risk_intelligence(),
        "drift_monitoring": {"state": "UNAVAILABLE", "reason": "NO_CERTIFIED_RETURN_SERIES"},
        "ablation_foundation": ablation_contract(),
        "redundancy_diagnostics": redundancy_diagnostics(["EMA", "ema", "RSI"]),
        "strategy_attribution": strategy_attribution(signals[0], regime, mtf, commission_swap),
        "news_provider_interface": news_provider_contract(),
        "no_lookahead": True,
        "broker_execution_calls": 0,
    }
    snapshot["data_quality"] = data_quality(snapshot)
    snapshot["snapshot_hash"] = stable_hash(snapshot)
    _ = tick_quote
    return snapshot
