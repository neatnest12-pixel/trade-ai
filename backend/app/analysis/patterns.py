import numpy as np
import pandas as pd


def detect_patterns(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy(); body=(x.close-x.open).abs(); span=(x.high-x.low).replace(0,np.nan)
    upper=x.high-x[["open","close"]].max(axis=1); lower=x[["open","close"]].min(axis=1)-x.low
    po, pc, ph, pl = x.open.shift(), x.close.shift(), x.high.shift(), x.low.shift()
    x["bullish_engulfing"]=(x.close>x.open)&(pc<po)&(x.open<=pc)&(x.close>=po)
    x["bearish_engulfing"]=(x.close<x.open)&(pc>po)&(x.open>=pc)&(x.close<=po)
    x["hammer"]=(lower>=2*body)&(upper<=body)&(body/span>=.1)
    x["shooting_star"]=(upper>=2*body)&(lower<=body)&(body/span>=.1)
    x["pin_bar"]=((upper>=2.5*body)|(lower>=2.5*body))&(body/span<=.35)
    x["inside_bar"]=(x.high<ph)&(x.low>pl); x["outside_bar"]=(x.high>ph)&(x.low<pl)
    x["doji"]=body/span<=.1
    avg=span.rolling(20).mean(); x["bullish_displacement"]=(x.close>x.open)&(body>=1.5*avg)&(upper/span<.2)
    x["bearish_displacement"]=(x.close<x.open)&(body>=1.5*avg)&(lower/span<.2)
    x["rejection"]=(upper+lower)/span>=.6
    return x

