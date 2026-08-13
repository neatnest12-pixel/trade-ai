import numpy as np
import pandas as pd


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy(); c, h, l = x.close, x.high, x.low
    for n in (9, 20, 50, 100, 200): x[f"ema_{n}"] = c.ewm(span=n, adjust=False, min_periods=n).mean()
    for n in (20, 50, 200): x[f"sma_{n}"] = c.rolling(n, min_periods=n).mean()
    delta = c.diff(); gain = delta.clip(lower=0); loss = -delta.clip(upper=0)
    rs = gain.ewm(alpha=1/14, adjust=False, min_periods=14).mean() / loss.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    x["rsi_14"] = 100 - 100 / (1 + rs)
    fast, slow = c.ewm(span=12, adjust=False).mean(), c.ewm(span=26, adjust=False).mean()
    x["macd"] = fast - slow; x["macd_signal"] = x.macd.ewm(span=9, adjust=False).mean(); x["macd_hist"] = x.macd - x.macd_signal
    low14, high14 = l.rolling(14).min(), h.rolling(14).max()
    x["stoch_k"] = 100 * (c-low14)/(high14-low14).replace(0, np.nan); x["stoch_d"] = x.stoch_k.rolling(3).mean()
    prev = c.shift(); tr = pd.concat([h-l, (h-prev).abs(), (l-prev).abs()], axis=1).max(axis=1)
    x["atr_14"] = tr.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    up, down = h.diff(), -l.diff(); plus = up.where((up > down) & (up > 0), 0); minus = down.where((down > up) & (down > 0), 0)
    plus_di = 100 * plus.ewm(alpha=1/14, adjust=False).mean()/x.atr_14; minus_di = 100 * minus.ewm(alpha=1/14, adjust=False).mean()/x.atr_14
    x["plus_di"] = plus_di; x["minus_di"] = minus_di
    x["adx_14"] = (100*(plus_di-minus_di).abs()/(plus_di+minus_di).replace(0,np.nan)).ewm(alpha=1/14,adjust=False).mean()
    mid = c.rolling(20).mean(); std = c.rolling(20).std(ddof=0)
    x["bb_mid"] = mid; x["bb_upper"] = mid+2*std; x["bb_lower"] = mid-2*std; x["roc_10"] = c.pct_change(10)*100
    if "real_volume" in x and x.real_volume.notna().any():
        vol = x.real_volume.fillna(0); typical = (h+l+c)/3; x["vwap"] = (typical*vol).cumsum()/vol.cumsum().replace(0,np.nan)
    return x
