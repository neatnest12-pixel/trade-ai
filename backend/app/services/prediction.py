import numpy as np
import pandas as pd

from app.analysis.features import build_features, classify_regime
from app.domain.schemas import PredictionResponse
from app.strategies.agents import AGENTS


def predict(frame:pd.DataFrame,symbol:str,timeframe:str)->tuple[PredictionResponse,dict[str,float]]:
    features=build_features(frame)
    if len(features)<210: raise ValueError("At least 210 completed candles are required")
    row=features.iloc[-1]
    if row[["atr_14","ema_20","ema_50","rsi_14"]].isna().any(): raise ValueError("Insufficient valid features")
    votes=[a.evaluate(row) for a in AGENTS]
    directional=[v for v in votes if v.direction.value!="NEUTRAL"]
    signed=[(1 if v.direction.value=="BUY" else -1)*v.confidence/100 for v in directional]
    consensus=float(np.mean(signed)) if signed else 0
    probability_up=float(np.clip(.5+.22*consensus+.08*np.tanh(float(row.return_5)*500),.05,.95))
    atr=float(row.atr_14); close=float(row.close)
    # Until a versioned model has out-of-sample calibration evidence, heuristic strength is not confidence.
    confidence=0.0
    reasons=[]
    reasons.append("uncalibrated heuristic; empirical confidence unavailable")
    if directional and abs(sum(1 if v.direction.value=="BUY" else -1 for v in directional))<=2: reasons.append("strategies conflict")
    if float(row.atr_pct)>.004: reasons.append("abnormal volatility")
    if float(row.atr_pct)<.0004: reasons.append("low volatility")
    decision="NO TRADE" if reasons else ("BUY" if probability_up>.55 else "SELL" if probability_up<.45 else "NO TRADE")
    if decision=="NO TRADE" and not reasons: reasons=["probability edge below decision threshold"]
    regime=classify_regime(row)
    response=PredictionResponse(symbol=symbol,timeframe=timeframe,candle_timestamp=row.timestamp,
      probability_up=probability_up,probability_down=1-probability_up,expected_high=close+atr*.65,
      expected_low=close-atr*.65,expected_close_return=(probability_up-.5)*atr/close,
      expected_range=atr*1.3,confidence=confidence,market_regime=regime,decision=decision,
      no_trade_reasons=reasons,strategy_votes=votes,confidence_label="LOW",
      evidence_status="INSUFFICIENT_EVIDENCE")
    snapshot={k:float(row[k]) for k in ["rsi_14","macd_hist","atr_14","adx_14","ema_spread","bb_position","return_5"]}
    return response,snapshot
