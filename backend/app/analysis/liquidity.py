import numpy as np
import pandas as pd


def add_liquidity(df: pd.DataFrame, tolerance_atr: float=.15) -> pd.DataFrame:
    x=df.copy(); atr=x.get("atr_14",(x.high-x.low).rolling(14).mean())
    prior_high=x.high.shift().rolling(20).max(); prior_low=x.low.shift().rolling(20).min(); tol=atr*tolerance_atr
    x["equal_highs"]=(x.high-prior_high).abs()<=tol; x["equal_lows"]=(x.low-prior_low).abs()<=tol
    x["sweep_high"]=(x.high>prior_high)&(x.close<prior_high); x["sweep_low"]=(x.low<prior_low)&(x.close>prior_low)
    x["fvg_bullish"]=x.low>x.high.shift(2); x["fvg_bearish"]=x.high<x.low.shift(2)
    body=(x.close-x.open).abs(); x["displacement"]=body>1.5*atr
    # A candidate is exposed on the confirming displacement candle. Its origin price is prior-bar data.
    x["bullish_order_block_candidate"]=x.displacement&(x.close>x.open)&(x.close.shift(1)<x.open.shift(1))
    x["bearish_order_block_candidate"]=x.displacement&(x.close<x.open)&(x.close.shift(1)>x.open.shift(1))
    x["bullish_order_block_price"]=x.low.shift(1).where(x.bullish_order_block_candidate)
    x["bearish_order_block_price"]=x.high.shift(1).where(x.bearish_order_block_candidate)
    hi=x.high.rolling(50).max(); lo=x.low.rolling(50).min(); x["premium_discount"]=(x.close-lo)/(hi-lo).replace(0,np.nan)
    return x
