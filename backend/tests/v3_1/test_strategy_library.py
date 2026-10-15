from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.v3_1.strategy_library import (
    DEPLOYMENT_STATUS,
    POLICY_HASH,
    POLICY_VERSION,
    build_research_snapshot,
    default_registry,
    harmonic_patterns,
    sample_gate,
    time_horizon_policies,
)
from app.v3_2.confluence import Candle, TickQuote

BASE = datetime(2026, 8, 20, 0, 0, tzinfo=UTC)


def _candles(minutes: int, count: int = 90, start: float = 1.10) -> list[Candle]:
    price = start
    rows = []
    for idx in range(count):
        direction = 1 if idx % 8 < 5 else -1
        open_price = price
        close = open_price + direction * 0.0002
        rows.append(
            Candle(
                timestamp=BASE + timedelta(minutes=idx * minutes),
                open=open_price,
                high=max(open_price, close) + 0.0003,
                low=min(open_price, close) - 0.0003,
                close=close,
                tick_volume=100 + idx,
                spread=2,
            )
        )
        price = close
    return rows


def _snapshot() -> dict[str, object]:
    candles_by_tf = {
        "M1": _candles(1),
        "M5": _candles(5),
        "M15": _candles(15),
        "H1": _candles(60),
        "H6": _candles(360),
        "H12": _candles(720),
    }
    decision = candles_by_tf["H12"][-1].timestamp + timedelta(hours=12)
    return build_research_snapshot(candles_by_tf, decision, TickQuote(decision - timedelta(seconds=1), 1.101, 1.1012))


def test_policy_registry_and_time_horizons() -> None:
    registry = default_registry()
    definitions = registry.definitions()
    assert POLICY_VERSION == "v3.1-full-strategy-research-r1"
    assert len(POLICY_HASH) == 64
    assert len(definitions) >= 13
    assert all(item.deployment_status == DEPLOYMENT_STATUS for item in definitions)
    horizons = time_horizon_policies()
    assert {"SCALPING", "DAY_TRADING", "SWING_TRADING", "POSITION_TRADING"} <= set(horizons)
    assert horizons["SCALPING"]["cost_sensitivity"] == "VERY_HIGH"


def test_full_research_snapshot_contains_required_families_and_contexts() -> None:
    snapshot = _snapshot()
    families = {item["strategy_family"] for item in snapshot["strategy_registry"]}
    signal_families = {item["strategy_family"] for item in snapshot["strategy_signals"]}
    expected = {
        "TREND",
        "BREAKOUT",
        "MEAN_REVERSION",
        "PULLBACK",
        "HARMONICS",
        "MARKET_STRUCTURE",
        "LIQUIDITY",
        "PRICE_ACTION",
        "VOLATILITY",
        "SESSION",
        "GRID",
        "ARBITRAGE",
        "CARRY",
    }
    assert expected <= families
    assert expected <= signal_families
    assert snapshot["policy_version"] == POLICY_VERSION
    assert snapshot["policy_hash"] == POLICY_HASH
    assert snapshot["news_context"]["state"] == "NEWS_DATA_UNAVAILABLE"
    assert snapshot["carry_context"]["state"] == "CARRY_UNAVAILABLE"
    assert snapshot["cost_discipline"]["commission"] == "COMMISSION_UNAVAILABLE"


def test_technical_components_present() -> None:
    snapshot = _snapshot()
    assert snapshot["indicators"]["M1"]["EMA"] > 0
    assert "RSI" in snapshot["indicators"]["M5"]
    assert "Stochastic" in snapshot["indicators"]["M5"]
    assert "MACD" in snapshot["indicators"]["M15"]
    assert snapshot["fibonacci"]["nearest_retracement"] is not None
    assert isinstance(snapshot["BOS"], list)
    assert isinstance(snapshot["CHOCH"], list)
    assert isinstance(snapshot["support_resistance"], list)
    assert isinstance(snapshot["liquidity_sweeps"], list)
    assert isinstance(snapshot["FVG"], list)
    assert isinstance(snapshot["order_blocks"], list)
    assert snapshot["mtf_alignment"]["alignment_state"]


def test_grid_arbitrage_and_carry_are_research_or_unavailable_only() -> None:
    snapshot = _snapshot()
    signals = {item["strategy_family"]: item for item in snapshot["strategy_signals"]}
    assert signals["GRID"]["deployment_status"] == "RESEARCH_ONLY"
    assert "NO_MARTINGALE" in signals["GRID"]["reason_codes"]
    assert "ARBITRAGE_NOT_EVALUABLE" in signals["ARBITRAGE"]["reason_codes"]
    assert "CARRY_UNAVAILABLE" in signals["CARRY"]["reason_codes"]


def test_shadow_outcomes_are_research_only_and_rr_matrix() -> None:
    snapshot = _snapshot()
    outcomes = snapshot["shadow_outcomes"]
    assert all(item["commission"] == "COMMISSION_UNAVAILABLE" for item in outcomes)
    if outcomes:
        assert {item["RR"] for item in outcomes} <= {1.0, 1.5, 2.0, 3.0, 5.0}
        assert all(item["TP_SL_expiry"] == "UNRESOLVED" for item in outcomes)


def test_harmonic_research_flags_overfit_risk() -> None:
    candles = [
        Candle(BASE, 1.00, 1.00, 1.00, 1.00),
        Candle(BASE + timedelta(hours=1), 1.00, 1.10, 1.00, 1.10),
        Candle(BASE + timedelta(hours=2), 1.10, 1.10, 1.0382, 1.0382),
        Candle(BASE + timedelta(hours=3), 1.0382, 1.0764, 1.0382, 1.0764),
        Candle(BASE + timedelta(hours=4), 1.0764, 1.0764, 1.0278, 1.0278),
    ]
    patterns = harmonic_patterns(candles, tolerance=0.5)
    assert all(item["risk_flag"] == "HIGH_OVERFIT_RISK" for item in patterns)


def test_sample_gates_and_no_lookahead() -> None:
    assert sample_gate(10) == "INSUFFICIENT"
    assert sample_gate(100) == "EARLY"
    assert sample_gate(300) == "PRELIMINARY"
    assert sample_gate(700) == "MEANINGFUL"
    assert sample_gate(1200) == "STRONGER"
    candles_by_tf = {tf: _candles(1) for tf in ("M1", "M5", "M15", "H1", "H6", "H12")}
    decision = BASE + timedelta(hours=1)
    with pytest.raises(ValueError):
        build_research_snapshot(candles_by_tf, decision, TickQuote(decision + timedelta(seconds=1), 1.1, 1.1002))


def test_order_send_absent_from_v3_1_code() -> None:
    root = Path(__file__).resolve().parents[3]
    files = list((root / "backend" / "app" / "v3_1").glob("*.py")) + list((root / "backend" / "scripts").glob("v3_1*.py"))
    joined = "\n".join(path.read_text(encoding="utf-8") for path in files)
    assert "order_send" not in joined
