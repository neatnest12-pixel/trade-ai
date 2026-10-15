from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.v3_2.confluence import Candle
from app.v3_3.system_intelligence import Tick
from app.v3_4.fx_intelligence import (
    ADAPTIVE_RR,
    POLICY_HASH,
    POLICY_VERSION,
    MacroEvent,
    build_fx_intelligence_snapshot,
    carry_engine,
    central_bank_policy,
    currency_exposure,
    economic_surprise,
    event_window,
    rate_differential,
    session_specialization,
    spread_regime,
)

BASE = datetime(2026, 8, 20, 8, 0, tzinfo=UTC)


def _candles() -> list[Candle]:
    return [Candle(BASE + timedelta(minutes=i), 1.1, 1.1005, 1.0995, 1.1 + (i % 5) * 0.0001, 100 + i, 2) for i in range(120)]


def _ticks(decision: datetime) -> list[Tick]:
    return [Tick(decision - timedelta(seconds=60 - i), 1.101 + i * 0.000001, 1.1012 + i * 0.000001) for i in range(60)]


def test_macro_schema_surprise_event_windows_and_rates() -> None:
    event = MacroEvent("e1", BASE + timedelta(minutes=10), "USD", "US", "US CPI", "HIGH", actual=3.1, forecast=3.0, previous=2.9)
    surprise = economic_surprise(event)
    assert surprise["surprise_absolute"] == pytest.approx(0.1)
    assert event_window(event.event_timestamp, BASE) == "NEWS_IMMINENT"
    policy = central_bank_policy(5.5, 4.0, 5.25, 4.0)
    assert policy["fed_policy_direction"] == "TIGHTENING"
    rates = rate_differential(5.5, 4.0, {"US_2Y": 4.5, "DE_2Y": 2.5})
    assert rates["policy_rate_differential"] == 1.5
    assert rates["2Y_yield_differential"] == 2.0


def test_carry_cross_asset_unavailable_and_broker_costs() -> None:
    assert carry_engine()["state"] == "CARRY_UNAVAILABLE"
    assert carry_engine({"swap_long": -1.2, "swap_short": 0.2})["state"] == "CARRY_PARTIAL"
    decision = BASE + timedelta(hours=2)
    snapshot = build_fx_intelligence_snapshot(_candles(), _ticks(decision), decision)
    assert snapshot["cross_asset"]["state"] == "CROSS_ASSET_UNAVAILABLE"
    assert snapshot["fx_futures"]["state"] == "FX_FUTURES_UNAVAILABLE"
    assert snapshot["positioning_cot"]["state"] == "POSITIONING_COT_UNAVAILABLE"
    assert snapshot["broker_cost"]["commission"] == "COMMISSION_UNAVAILABLE"
    assert snapshot["broker_cost"]["swap_long"] == "SWAP_UNAVAILABLE"


def test_spread_slippage_latency_liquidity_session_and_momentum() -> None:
    decision = BASE + timedelta(hours=2)
    snapshot = build_fx_intelligence_snapshot(_candles(), _ticks(decision), decision)
    assert spread_regime(_ticks(decision))["classification"] in {"TIGHT", "NORMAL", "ELEVATED", "EXTREME"}
    assert snapshot["slippage"]["demo_order_sent"] is False
    assert "signal_to_decision_ms" in snapshot["latency"]
    assert snapshot["liquidity_regime"] in {"DEEP", "NORMAL", "THIN", "DISLOCATED", "UNKNOWN"}
    assert session_specialization(decision)["session_bucket"]
    assert "return_1D" in snapshot["momentum_factor"]
    assert snapshot["value_foundation"]["state"] == "VALUE_UNAVAILABLE"


def test_volatility_strategy_meta_rr_risk_validation_and_provenance() -> None:
    decision = BASE + timedelta(hours=2)
    snapshot = build_fx_intelligence_snapshot(_candles(), _ticks(decision), decision)
    assert snapshot["volatility_forecast"]["realized_vol_next_5m"] == "LABEL_ONLY"
    assert "vol_1m" in snapshot["volatility_term_structure"]
    assert snapshot["strategy_selector"]["stages"][0] == "market regime"
    assert snapshot["meta_labeling"]["target"] == "TAKE/SKIP"
    assert 7.0 in ADAPTIVE_RR
    assert snapshot["adaptive_research"]["adaptive_RR"][-1] == 7.0
    assert currency_exposure("EURUSD", "LONG") == {"EUR": 1, "USD": -1}
    assert snapshot["risk"]["portfolio_risk"]["absolute_single_trade_ceiling"] == "3%"
    assert snapshot["risk"]["three_five_seven"]["7"].startswith("experimental")
    assert snapshot["validation"]["purged_walk_forward"]["minimum_folds"] == 4
    assert snapshot["data_provenance"]["quality"] == "UNAVAILABLE"
    assert snapshot["fail_closed"]["state"] == "PASS"


def test_no_lookahead_and_order_send_absence() -> None:
    decision = BASE + timedelta(hours=2)
    with pytest.raises(ValueError, match="future tick"):
        build_fx_intelligence_snapshot(_candles(), [Tick(decision + timedelta(seconds=1), 1.1, 1.1002)], decision)
    root = Path(__file__).resolve().parents[3]
    files = list((root / "backend" / "app" / "v3_4").glob("*.py")) + list((root / "backend" / "scripts").glob("v3_4*.py"))
    joined = "\n".join(path.read_text(encoding="utf-8") for path in files)
    assert "order_send" not in joined
    assert "fit_model" not in joined


def test_policy_and_snapshot_core() -> None:
    decision = BASE + timedelta(hours=2)
    snapshot = build_fx_intelligence_snapshot(_candles(), _ticks(decision), decision)
    assert POLICY_VERSION == "v3.4-complete-fx-intelligence-r1"
    assert len(POLICY_HASH) == 64
    assert snapshot["policy_hash"] == POLICY_HASH
    assert snapshot["no_lookahead"] is True
