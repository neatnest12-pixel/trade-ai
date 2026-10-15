import numpy as np
import pandas as pd

from app.analysis.features import build_features, classify_regime
from app.core.volatility import DEFAULT_MAX_ATR_PCT, DEFAULT_MIN_ATR_PCT
from app.domain.schemas import PredictionResponse
from app.strategies.agents import AGENTS, VolatilityAgent


def predict(frame: pd.DataFrame, symbol: str, timeframe: str, *,
            min_atr_pct: float = DEFAULT_MIN_ATR_PCT,
            max_atr_pct: float = DEFAULT_MAX_ATR_PCT) -> tuple[PredictionResponse, dict[str, float]]:
    if not 0 < min_atr_pct <= max_atr_pct:
        raise ValueError("volatility bounds must be positive and ordered")
    features=build_features(frame)
    if len(features)<210: raise ValueError("At least 210 completed candles are required")
    row=features.iloc[-1]
    if row[["atr_14","ema_20","ema_50","rsi_14"]].isna().any(): raise ValueError("Insufficient valid features")
    votes=[a.evaluate(row, min_atr_pct=min_atr_pct, max_atr_pct=max_atr_pct)
           if isinstance(a, VolatilityAgent) else a.evaluate(row) for a in AGENTS]
    directional=[v for v in votes if v.direction.value!="NEUTRAL"]
    signed=[(1 if v.direction.value=="BUY" else -1)*v.confidence/100 for v in directional]
    consensus=float(np.mean(signed)) if signed else 0
    probability_up=float(np.clip(.5+.22*consensus+.08*np.tanh(float(row.return_5)*500),.05,.95))
    atr=float(row.atr_14); close=float(row.close)
    # This is a strategy-consensus strength, not a calibrated probability or a promise.
    confidence=float(np.clip(abs(consensus) * 100, 0, 85))
    reasons=[]
    alignment=abs(sum(1 if v.direction.value=="BUY" else -1 for v in directional))
    if not directional or alignment<=2: reasons.append("strategies conflict")
    if float(row.atr_pct) > max_atr_pct: reasons.append("abnormal volatility")
    if float(row.atr_pct) < min_atr_pct: reasons.append("low volatility")
    if confidence<35: reasons.append("confidence below paper-trade threshold")
    decision="NO_TRADE" if reasons else ("BUY" if probability_up>.55 else "SELL" if probability_up<.45 else "NO_TRADE")
    if decision=="NO_TRADE" and not reasons: reasons=["probability edge below decision threshold"]
    entry=close
    stop=entry-atr*1.25 if decision=="BUY" else entry+atr*1.25
    target=entry+atr*2.5 if decision=="BUY" else entry-atr*2.5
    regime=classify_regime(row, min_atr_pct=min_atr_pct, max_atr_pct=max_atr_pct)
    response=PredictionResponse(symbol=symbol,timeframe=timeframe,candle_timestamp=row.timestamp,
      probability_up=probability_up,probability_down=1-probability_up,expected_high=close+atr*.65,
      expected_low=close-atr*.65,expected_close_return=(probability_up-.5)*atr/close,
      expected_range=atr*1.3,entry_reference=entry,stop_loss=stop,target=target,risk_reward=2.0,
      confidence=confidence,market_regime=regime,decision=decision,no_trade_reasons=reasons,
      strategy_votes=votes,confidence_label="HEURISTIC",evidence_status="PAPER_UNVALIDATED")
    snapshot={k:float(row[k]) for k in ["rsi_14","macd_hist","atr_14","adx_14","ema_spread","bb_position","return_5"]}
    return response,snapshot
