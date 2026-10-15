"""One-shot, non-persisting V1.1C prediction helper."""
from __future__ import annotations

import pandas as pd

from app.core.volatility import V1_1C_VOLATILITY_BOUNDS
from app.services.prediction import predict


def predict_v11c(frame: pd.DataFrame, symbol: str = "EURUSD", timeframe: str = "1m"):
    return predict(frame, symbol, timeframe,
                   min_atr_pct=V1_1C_VOLATILITY_BOUNDS.min_atr_pct,
                   max_atr_pct=V1_1C_VOLATILITY_BOUNDS.max_atr_pct)
