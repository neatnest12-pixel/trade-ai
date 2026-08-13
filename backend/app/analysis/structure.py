import numpy as np
import pandas as pd


def add_structure(df: pd.DataFrame, window: int = 2) -> pd.DataFrame:
    """Add causally available structure features.

    A pivot at t-window is only confirmed and exposed at t after ``window`` right-hand bars close.
    The feature is deliberately recorded on the confirmation bar, never backfilled onto the pivot.
    """
    if window < 1:
        raise ValueError("Swing confirmation window must be positive")
    x=df.copy(); size=2*window+1
    rolling_high=x.high.rolling(size).max(); rolling_low=x.low.rolling(size).min()
    pivot_high=x.high.shift(window); pivot_low=x.low.shift(window)
    x["swing_high"] = pivot_high.eq(rolling_high).fillna(False)
    x["swing_low"] = pivot_low.eq(rolling_low).fillna(False)
    x["confirmed_swing_high_price"] = pivot_high.where(x.swing_high)
    x["confirmed_swing_low_price"] = pivot_low.where(x.swing_low)
    highs=x.confirmed_swing_high_price.ffill(); lows=x.confirmed_swing_low_price.ffill()
    prev_high=highs.where(x.swing_high).shift().ffill(); prev_low=lows.where(x.swing_low).shift().ffill()
    x["higher_high"]=x.swing_high&(pivot_high>prev_high); x["lower_high"]=x.swing_high&(pivot_high<prev_high)
    x["higher_low"]=x.swing_low&(pivot_low>prev_low); x["lower_low"]=x.swing_low&(pivot_low<prev_low)
    x["bos_bullish"]=(x.close>highs.shift())&(x.close.shift()<=highs.shift())
    x["bos_bearish"]=(x.close<lows.shift())&(x.close.shift()>=lows.shift())
    trend=np.select([(highs>highs.shift())&(lows>lows.shift()),(highs<highs.shift())&(lows<lows.shift())],["bullish","bearish"],default="range")
    x["structure"]=trend
    x["choch_bullish"]=x.bos_bullish&(pd.Series(trend,index=x.index).shift()=="bearish")
    x["choch_bearish"]=x.bos_bearish&(pd.Series(trend,index=x.index).shift()=="bullish")
    x["support"]=lows; x["resistance"]=highs
    x["failed_breakout_high"]=(x.high>highs.shift())&(x.close<highs.shift())
    x["failed_breakout_low"]=(x.low<lows.shift())&(x.close>lows.shift())
    return x
