from datetime import UTC, datetime
from decimal import Decimal

import pandas as pd
import pytest

from app.analysis.features import classify_regime
from app.core.volatility import DEFAULT_MAX_ATR_PCT, DEFAULT_MIN_ATR_PCT, V1_1C_VOLATILITY_BOUNDS
from app.strategies.agents import VolatilityAgent, result
from app.v1_1.contract import MAX_RISK_RUPEES, RISK_PERCENTAGE
from app.v1_1c.campaign import JOURNAL_PATH, LOCK_PATH, WORKER_STATE_PATH
from app.v1_1c.contract import CAMPAIGN_ID, CONFIG_HASH, SNAPSHOT_ID, freeze_trade
from app.v1_1c.journal import append_v11c


def row(atr_pct: float) -> pd.Series:
    return pd.Series({"atr_pct": atr_pct, "adx_14": 20, "ema_spread": 0, "bb_position": .5})


def test_legacy_volatility_boundaries_are_unchanged():
    assert (DEFAULT_MIN_ATR_PCT, DEFAULT_MAX_ATR_PCT) == (.0004, .004)
    assert classify_regime(row(.00039999)) == "low volatility"
    assert classify_regime(row(.0004)) != "low volatility"
    assert classify_regime(row(.00400001)) == "high volatility"
    assert VolatilityAgent().evaluate(row(.0004)).direction.value == "NEUTRAL"


def test_v11c_resolves_one_shared_volatility_configuration():
    bounds = V1_1C_VOLATILITY_BOUNDS
    assert (bounds.min_atr_pct, bounds.max_atr_pct) == (.00008, .004)
    assert classify_regime(row(.00007999), min_atr_pct=bounds.min_atr_pct, max_atr_pct=bounds.max_atr_pct) == "low volatility"
    assert classify_regime(row(.00008), min_atr_pct=bounds.min_atr_pct, max_atr_pct=bounds.max_atr_pct) != "low volatility"
    assert classify_regime(row(.00008001), min_atr_pct=bounds.min_atr_pct, max_atr_pct=bounds.max_atr_pct) != "low volatility"
    agent = VolatilityAgent()
    assert agent.evaluate(row(.00007999), min_atr_pct=bounds.min_atr_pct, max_atr_pct=bounds.max_atr_pct).direction.value == "SELL"
    assert agent.evaluate(row(.00008), min_atr_pct=bounds.min_atr_pct, max_atr_pct=bounds.max_atr_pct).direction.value == "NEUTRAL"


def test_v11c_identity_paths_and_unchanged_risk_engine():
    assert CAMPAIGN_ID == "NAVORA-QUANT-V1.1C-PAPER"
    assert SNAPSHOT_ID.startswith("v1.1c-paper-") and len(CONFIG_HASH) == 64
    assert str(JOURNAL_PATH).endswith("FORWARD_PAPER_JOURNAL_V1_1C.jsonl")
    assert str(WORKER_STATE_PATH).endswith("v1_1c_worker_state.json")
    assert str(LOCK_PATH).endswith("v1_1c_forward_paper.lock")
    trade = freeze_trade(prediction_id="x", symbol="EURUSD", timeframe="M1", prediction_timestamp="t", candle_timestamp="c", decision="BUY", entry_price=Decimal("1.1"), atr_14_price=Decimal(".001"), balance_before_trade=Decimal("2500"), confidence=Decimal("50"))
    assert (trade.risk_rupees, trade.risk_percentage, trade.planned_rr) == (MAX_RISK_RUPEES, RISK_PERCENTAGE, Decimal("2"))
    assert (trade.snapshot_id, trade.config_hash) == (SNAPSHOT_ID, CONFIG_HASH)


def test_invalid_bounds_are_rejected():
    from app.services.prediction import predict
    with pytest.raises(ValueError):
        predict(pd.DataFrame(), "EURUSD", "1m", min_atr_pct=.005, max_atr_pct=.004)


class FixedAgent:
    def __init__(self, value: float):
        self.value = value

    def evaluate(self, _row):
        return result("Fixed", self.value, ["test"], ["test"])


def test_predictor_uses_v11c_threshold_and_keeps_all_decision_paths(monkeypatch):
    import app.services.prediction as prediction_module

    base = {"timestamp": pd.Timestamp("2026-08-17T00:00:00Z"), "atr_14": .0001,
            "close": 1.1, "atr_pct": .00008, "ema_20": 1.1, "ema_50": 1.1,
            "rsi_14": 50., "macd_hist": 0., "adx_14": 20., "ema_spread": 0.,
            "bb_position": .5, "return_5": 0.}
    features = pd.DataFrame([base] * 210)
    monkeypatch.setattr(prediction_module, "build_features", lambda _frame: features)
    monkeypatch.setattr(prediction_module, "AGENTS", [FixedAgent(1)] * 9)
    buy, _ = prediction_module.predict(pd.DataFrame(), "EURUSD", "1m", min_atr_pct=.00008)
    assert buy.decision == "BUY" and "low volatility" not in buy.no_trade_reasons
    monkeypatch.setattr(prediction_module, "AGENTS", [FixedAgent(-1)] * 9)
    sell, _ = prediction_module.predict(pd.DataFrame(), "EURUSD", "1m", min_atr_pct=.00008)
    assert sell.decision == "SELL"
    monkeypatch.setattr(prediction_module, "AGENTS", [FixedAgent(0)] * 9)
    no_trade, _ = prediction_module.predict(pd.DataFrame(), "EURUSD", "1m", min_atr_pct=.00008)
    assert no_trade.decision == "NO_TRADE"
    assert "confidence below paper-trade threshold" in no_trade.no_trade_reasons
    assert buy.confidence == 85 and sell.confidence == 85


def test_predictor_passes_the_resolved_bounds_to_the_volatility_agent(monkeypatch):
    import app.services.prediction as prediction_module

    class SpyVolatilityAgent(VolatilityAgent):
        def evaluate(self, value, *, min_atr_pct, max_atr_pct):
            self.bounds = (min_atr_pct, max_atr_pct)
            return super().evaluate(value, min_atr_pct=min_atr_pct, max_atr_pct=max_atr_pct)

    base = {"timestamp": pd.Timestamp("2026-08-17T00:00:00Z"), "atr_14": .0001,
            "close": 1.1, "atr_pct": .00008, "ema_20": 1.1, "ema_50": 1.1,
            "rsi_14": 50., "macd_hist": 0., "adx_14": 20., "ema_spread": 0.,
            "bb_position": .5, "return_5": 0.}
    monkeypatch.setattr(prediction_module, "build_features", lambda _frame: pd.DataFrame([base] * 210))
    spy = SpyVolatilityAgent()
    monkeypatch.setattr(prediction_module, "AGENTS", [FixedAgent(1)] * 8 + [spy])
    prediction_module.predict(pd.DataFrame(), "EURUSD", "1m", min_atr_pct=.00008, max_atr_pct=.004)
    assert spy.bounds == (.00008, .004)


def test_v11c_journal_rejects_identity_mismatch_and_duplicate(tmp_path):
    record = {"prediction_hash": "x", "campaign_id": CAMPAIGN_ID, "snapshot_id": SNAPSHOT_ID,
              "config_hash": CONFIG_HASH, "symbol": "EURUSD", "timeframe": "M1",
              "prediction_timestamp": "t", "candle_timestamp": "c", "decision": "NO_TRADE",
              "entry_price": None, "stop_loss": None, "target_price": None,
              "risk_rupees": None, "position_size_simulated": None}
    path = tmp_path / "FORWARD_PAPER_JOURNAL_V1_1C.jsonl"
    append_v11c(path, record)
    with pytest.raises(ValueError, match="duplicate"):
        append_v11c(path, record)
    with pytest.raises(ValueError, match="identity"):
        append_v11c(tmp_path / "bad.jsonl", {**record, "config_hash": "wrong"})


def test_real_account_connection_is_blocked_by_the_read_only_adapter():
    from app.v1_1.mt5_adapter import MT5Adapter

    class RealAccountMT5:
        def initialize(self):
            return True

        def account_info(self):
            return type("Account", (), {"company": "Live Broker", "server": "Live-01"})()

    adapter = MT5Adapter(RealAccountMT5(), now=lambda: datetime.now(UTC))
    with pytest.raises(RuntimeError, match="broker mismatch"):
        adapter.connect()
