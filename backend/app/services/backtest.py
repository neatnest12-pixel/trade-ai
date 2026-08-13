import numpy as np
import pandas as pd

from app.analysis.features import build_features
from app.domain.schemas import BacktestRequest


def run_backtest(df:pd.DataFrame,req:BacktestRequest)->dict[str,object]:
    x=build_features(df).dropna(subset=["ema_20","ema_50","atr_14"]).reset_index(drop=True)
    returns=[]; equity=[req.initial_balance]; i=0
    while i < len(x)-req.max_holding-1:
        row=x.iloc[i]; side=1 if row.ema_20>row.ema_50 and row.adx_14>20 else -1 if row.ema_20<row.ema_50 and row.adx_14>20 else 0
        if not side:
            i+=1
            continue
        # Signal at close i; earliest fill is next candle open.
        entry=float(x.iloc[i+1].open)+side*(req.spread/2+req.slippage); risk=req.stop_atr*float(row.atr_14)
        stop=entry-side*risk; target=entry+side*req.target_atr*float(row.atr_14); exit_price=float(x.iloc[i+req.max_holding].close)
        exit_index=min(i+req.max_holding,len(x)-1)
        for j in range(i+1,min(i+1+req.max_holding,len(x))):
            bar=x.iloc[j]
            # Conservative ambiguity rule: stop wins if both touched in one bar.
            if (side==1 and bar.low<=stop) or (side==-1 and bar.high>=stop): exit_price=stop;exit_index=j;break
            if (side==1 and bar.high>=target) or (side==-1 and bar.low<=target): exit_price=target;exit_index=j;break
        r=side*(exit_price-entry)-req.commission; returns.append(r/risk); equity.append(equity[-1]*(1+.01*returns[-1]))
        i=exit_index+1  # one position at a time; no overlapping reuse of future bars
    a=np.array(returns); peaks=np.maximum.accumulate(equity); dd=(np.array(equity)-peaks)/peaks
    wins=a[a>0]; losses=a[a<0]
    return {"trades":len(a),"win_rate":float((a>0).mean()) if len(a) else 0,"average_r":float(a.mean()) if len(a) else 0,
      "profit_factor":float(wins.sum()/abs(losses.sum())) if len(losses) and losses.sum()!=0 else None,
      "maximum_drawdown":float(dd.min()),"ending_equity":float(equity[-1]),"equity_curve":equity,
      "notice":"Historical simulation with modeled costs; not real-money performance."}
