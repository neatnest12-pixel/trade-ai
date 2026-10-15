import numpy as np
import pandas as pd

from app.analysis.indicators import add_indicators
from app.analysis.liquidity import add_liquidity
from app.analysis.patterns import detect_patterns
from app.analysis.structure import add_structure
from app.core.volatility import DEFAULT_MAX_ATR_PCT, DEFAULT_MIN_ATR_PCT


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    x=add_liquidity(detect_patterns(add_structure(add_indicators(df))))
    x["return_1"]=x.close.pct_change(); x["return_5"]=x.close.pct_change(5)
    x["atr_pct"]=x.atr_14/x.close; x["ema_spread"]=(x.ema_20-x.ema_50)/x.close
    x["bb_position"]=(x.close-x.bb_lower)/(x.bb_upper-x.bb_lower).replace(0,np.nan)
    x["volume_z"]=(x.tick_volume-x.tick_volume.rolling(30).mean())/x.tick_volume.rolling(30).std().replace(0,np.nan)
    return x.replace([np.inf,-np.inf],np.nan)


def generate_targets(df: pd.DataFrame) -> pd.DataFrame:
    """All targets are shifted backward and must never enter feature columns."""
    x=df.copy(); nxt=x.shift(-1)
    x["target_up"]=(nxt.close>x.close).astype("Int64"); x["target_high"]=nxt.high
    x["target_low"]=nxt.low; x["target_range"]=nxt.high-nxt.low
    x["target_return"]=nxt.close/x.close-1
    if len(x): x.loc[x.index[-1],["target_up"]]=pd.NA
    return x


def classify_regime(row: pd.Series, *, min_atr_pct: float = DEFAULT_MIN_ATR_PCT,
                    max_atr_pct: float = DEFAULT_MAX_ATR_PCT) -> str:
    adx=float(row.get("adx_14",0) or 0); atrp=float(row.get("atr_pct",0) or 0); spread=float(row.get("ema_spread",0) or 0)
    if atrp > max_atr_pct: return "high volatility"
    if atrp < min_atr_pct: return "low volatility"
    if adx>=25 and spread>0: return "strong bullish trend"
    if adx>=25 and spread<0: return "strong bearish trend"
    if adx>=18 and spread>0: return "weak bullish trend"
    if adx>=18 and spread<0: return "weak bearish trend"
    if float(row.get("bb_position",.5) or .5) > .9 or float(row.get("bb_position",.5) or .5)<.1: return "possible breakout environment"
    return "range"
