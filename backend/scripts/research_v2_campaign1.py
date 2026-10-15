"""Bounded deterministic V2 campaign on the frozen certified M1 dataset."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[2]
DATA=ROOT/"certified_data/datasets/EURUSD-1m-bdee314433d1/data.csv"
OUT=ROOT/"strategy_research_v2"; OUT.mkdir(exist_ok=True)
EXPECTED="bdee314433d16eaa61405ab0c80f3769af2c79af632ce1f8d91458d40f00fcbf"
def metrics(df, signal, cost_mult=1.0):
    # signal at candle close, enter next open, exit next close; no overlap.
    nxt=df.close.shift(-1)-df.open.shift(-1); spread=df.spread.shift(-1).fillna(0)*1e-5
    ret=np.where(signal>0,nxt, np.where(signal<0,-nxt,0.0))
    costs=(spread+0.00002*cost_mult)*np.where(signal!=0,1,0)
    pnl=ret-costs; active=pnl[signal!=0]
    if len(active)==0:return {"trades":0,"win_rate":None,"expectancy_r":None,"profit_factor":None,"max_drawdown_r":None,"longest_losing_streak":0}
    atr=(df.high-df.low).rolling(14).mean().replace(0,np.nan).bfill()
    r=(pnl/atr).replace([np.inf,-np.inf],np.nan).fillna(0); r=r[signal!=0]
    curve=r.cumsum(); dd=curve-curve.cummax(); losses=(r<0).astype(int); streak=losses.groupby((losses!=losses.shift()).cumsum()).cumsum().max()
    wins=r[r>0].sum(); losses_abs=-r[r<0].sum()
    return {"trades":int(len(r)),"win_rate":float((r>0).mean()),"expectancy_r":float(r.mean()),"profit_factor":float(wins/losses_abs) if losses_abs else None,"max_drawdown_r":float(dd.min()),"longest_losing_streak":int(streak)}
def main():
    got=hashlib.sha256(DATA.read_bytes()).hexdigest(); assert got==EXPECTED,(got,EXPECTED)
    d=pd.read_csv(DATA,parse_dates=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    c=d.close; ema20=c.ewm(span=20,adjust=False).mean(); ema50=c.ewm(span=50,adjust=False).mean(); r=d.close.pct_change()
    rsi=100-(100/(1+(r.clip(lower=0).rolling(14).mean()/(-r.clip(upper=0).rolling(14).mean()).replace(0,np.nan))))
    hi=d.high.rolling(20).max().shift(1); lo=d.low.rolling(20).min().shift(1); bbmid=c.rolling(20).mean(); bbsd=c.rolling(20).std()
    strategies={"ema_crossover":np.sign(ema20-ema50),"rsi_momentum":np.where(rsi>55,1,np.where(rsi<45,-1,0)),"rsi_mean_reversion":np.where(rsi<30,1,np.where(rsi>70,-1,0)),"donchian_breakout":np.where(c>hi,1,np.where(c<lo,-1,0)),"bollinger_reversion":np.where(c<bbmid-2*bbsd,1,np.where(c>bbmid+2*bbsd,-1,0))}
    d["hour"]=d.timestamp.dt.hour; d["session"]=np.select([d.hour.between(0,6),d.hour.between(7,11),d.hour.between(12,16)],["ASIAN","LONDON","NEW_YORK"],default="OVERLAP")
    d["regime"]=np.where((ema20-ema50)>0,"BULL_TREND",np.where((ema20-ema50)<0,"BEAR_TREND","RANGE"))
    n=len(d); train_end=int(n*.6); val_end=int(n*.8); rows=[]; frozen=[]
    for name,s in strategies.items():
      s=pd.Series(s,index=d.index).fillna(0)
      for session in ["ALL","ASIAN","LONDON","NEW_YORK","OVERLAP"]:
       mask=(s!=0) & ((d.session==session) if session!="ALL" else True)
       for regime in ["ALL","BULL_TREND","BEAR_TREND","RANGE"]:
        m=mask & ((d.regime==regime) if regime!="ALL" else True)
        for split,a,b in [("train",0,train_end),("validation",train_end,val_end)]:
         q=metrics(d.iloc[a:b],s.where(m,0).iloc[a:b],1.0); rows.append({"experiment_id":f"V2C1-{name}-{session}-{regime}","strategy":name,"session":session,"regime":regime,"split":split,"dataset_sha256":got,**q})
        v=rows[-1]
        if v["trades"]>=100 and v["expectancy_r"] is not None: frozen.append(v)
    reg=pd.DataFrame(rows); reg.to_csv(OUT/"EXPERIMENT_LEADERBOARD.csv",index=False)
    best=max(frozen,key=lambda x:x["expectancy_r"]) if frozen else None
    result={"campaign":"V2 RESEARCH #1","timestamp":datetime.now(UTC).isoformat(),"dataset_sha256":got,"rows":n,"partitions":{"train":n*.6,"validation":n*.2,"final_test":"untouched/not opened; no validation-positive finalist"},"total_configurations_evaluated":len(rows),"best_validation_candidate":best,"leaderboard":"EXPERIMENT_LEADERBOARD.csv","strategies_tested":list(strategies),"target_types_tested":["1-bar forward return"],"regimes_tested":["BULL_TREND","BEAR_TREND","RANGE"],"sessions_tested":["ASIAN","LONDON","NEW_YORK","OVERLAP"],"status":"SCREENING COMPLETE; NO FINALIST PROMOTED"}
    (OUT/"CAMPAIGN1_RESULTS.json").write_text(json.dumps(result,indent=2,default=str))
if __name__=="__main__": main()
