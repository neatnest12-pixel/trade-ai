from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.v3_2.confluence import Candle
from app.v3_3.system_intelligence import (
    POLICY_HASH,
    POLICY_VERSION,
    Tick,
    build_system_snapshot,
    commission_swap_provenance,
    drift_monitoring,
    execution_realism,
    feature_drift_metrics,
    opening_range,
    rich_outcome_labels,
    session_vwap_proxy,
    tick_microstructure,
    volume_proxy,
)

BASE = datetime(2026, 8, 20, 8, 0, tzinfo=UTC)


def _candles(count: int = 90) -> list[Candle]:
    price = 1.10
    rows = []
    for idx in range(count):
        direction = 1 if idx % 9 < 6 else -1
        open_price = price
        close = open_price + direction * 0.0002
        rows.append(Candle(BASE + timedelta(minutes=idx), open_price, max(open_price, close) + 0.00025, min(open_price, close) - 0.00025, close, 100 + idx, 2))
        price = close
    return rows


def _ticks(decision: datetime, count: int = 80) -> list[Tick]:
    return [Tick(decision - timedelta(seconds=count - idx), 1.1010 + idx * 0.000001, 1.1012 + idx * 0.000001) for idx in range(count)]


def test_execution_realism_and_commission_swap_unavailable() -> None:
    tick = Tick(BASE, 1.1, 1.1002)
    economics = execution_realism(1.1001, tick, {"tick_size": 0.00001, "volume_min": 0.01})
    assert economics["spread_pips"] == pytest.approx(2.0)
    assert economics["actual_slippage"] == "UNAVAILABLE"
    provenance = commission_swap_provenance()
    assert provenance["commission_status"] == "COMMISSION_UNAVAILABLE"
    assert provenance["swap_long"] == "SWAP_UNAVAILABLE"


def test_tick_microstructure_volume_vwap_and_opening_range() -> None:
    candles = _candles()
    decision = candles[-1].timestamp + timedelta(minutes=1)
    micro = tick_microstructure(_ticks(decision), decision)
    assert micro["tick_count_1m"] > 0
    assert "tick_imbalance" in micro
    assert volume_proxy(candles)["volume_label"] == "BROKER_DERIVED_PROXY"
    assert session_vwap_proxy(candles, decision)["state"] == "BROKER_PROXY_VWAP"
    ranges = opening_range(candles, decision)
    assert {"ASIAN_RANGE", "LONDON_OPENING_RANGE", "NEW_YORK_OPENING_RANGE"} <= set(ranges)


def test_additional_patterns_divergence_regime_and_snapshot() -> None:
    candles = _candles()
    decision = candles[-1].timestamp + timedelta(minutes=1)
    snapshot = build_system_snapshot(
        {"M1": candles, "M5": candles, "M15": candles, "H1": candles, "H6": candles, "H12": candles},
        _ticks(decision),
        decision,
        strategy_signals=[{"strategy_id": "TREND_MOMENTUM", "strategy_family": "TREND", "setup": True, "direction": "BUY", "reason_codes": ["EMA"]}],
    )
    assert snapshot["policy_version"] == POLICY_VERSION
    assert snapshot["policy_hash"] == POLICY_HASH
    assert snapshot["additional_structure_patterns"]["patterns"]
    assert {"RSI_DIVERGENCE", "MACD_DIVERGENCE", "MOMENTUM_DIVERGENCE"} <= set(snapshot["divergence"])
    assert snapshot["regime"]["regime_id"] in {
        "TRENDING_UP",
        "TRENDING_DOWN",
        "RANGING",
        "VOLATILITY_EXPANSION",
        "VOLATILITY_COMPRESSION",
        "BREAKOUT_TRANSITION",
        "MEAN_REVERSION_REGIME",
        "NEWS_SHOCK",
        "POST_NEWS_NORMALIZATION",
        "THIN_LIQUIDITY",
        "MIXED_REGIME",
        "UNKNOWN",
    }
    assert snapshot["strategy_regime_matrix"][0]["regime_id"] == snapshot["regime"]["regime_id"]
    assert snapshot["uncertainty_foundation"]["calibrated_probability"] == "UNTRAINED"
    assert "SKIP_TRADE" in snapshot["future_training_targets"].values()
    assert snapshot["news_provider_interface"]["state"] == "NEWS_DATA_UNAVAILABLE"
    assert snapshot["broker_execution_calls"] == 0


def test_rich_outcomes_mfe_mae_drift_and_quality() -> None:
    path = [(BASE + timedelta(minutes=i), value) for i, value in enumerate([0.1, -0.3, 0.5, 1.2, 0.8])]
    labels = rich_outcome_labels(path)
    assert labels["MFE_R"] == 1.2
    assert labels["MAE_R"] == -0.3
    assert labels["best_rr_reached"] == 1
    assert drift_monitoring([0.1, -0.2, 0.3] * 10)["state"] in {"STABLE", "WATCH", "DEGRADING"}
    assert feature_drift_metrics([1, 2, 3], [1, 2, 2])["state"] in {"STABLE", "DRIFT_DETECTED"}


def test_no_lookahead_rejects_future_tick() -> None:
    candles = _candles()
    decision = candles[-1].timestamp + timedelta(minutes=1)
    with pytest.raises(ValueError, match="future tick"):
        build_system_snapshot({"M1": candles}, [Tick(decision + timedelta(seconds=1), 1.1, 1.1002)], decision)


def test_order_send_absent_from_v3_3_code() -> None:
    root = Path(__file__).resolve().parents[3]
    files = list((root / "backend" / "app" / "v3_3").glob("*.py")) + list((root / "backend" / "scripts").glob("v3_3*.py"))
    joined = "\n".join(path.read_text(encoding="utf-8") for path in files)
    assert "order_send" not in joined
