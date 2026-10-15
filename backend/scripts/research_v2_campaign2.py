from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[2]
DATA=ROOT/"certified_data/datasets/EURUSD-1m-bdee314433d1/data.csv"
OUT=ROOT/"campaign_v2_02"; OUT.mkdir(exist_ok=True)
EXPECTED="bdee314433d16eaa61405ab0c80f3769af2c79af632ce1f8d91458d40f00fcbf"

def evaluate(d, sig, atr, sl, tp, hold, cost_mult=1.0):
    close=d.close.to_numpy(); high=d.high.to_numpy(); low=d.low.to_numpy(); spread=d.spread.to_numpy()*1e-5
    sig=np.asarray(sig); atr=np.asarray(atr); out=[]
    for i in np.flatnonzero(sig):
        if i+hold>=len(d) or not np.isfinite(atr[i]) or atr[i]<=0: continue
        direction=sig[i]; entry=close[i]; target=entry+direction*tp*atr[i]; stop=entry-direction*sl*atr[i]; result=None; exit_px=close[min(i+hold,len(d)-1)]
        for j in range(i+1,min(i+hold+1,len(d))):
            if direction>0 and low[j]<=stop and high[j]>=target: result=-sl*atr[i]; break
            if direction<0 and high[j]>=stop and low[j]<=target: result=-sl*atr[i]; break
            if direction>0 and high[j]>=target: result=tp*atr[i]; break
            if direction<0 and low[j]<=target: result=tp*atr[i]; break
            if direction>0 and low[j]<=stop: result=-sl*atr[i]; break
            if direction<0 and high[j]>=stop: result=-sl*atr[i]; break
        if result is None: result=direction*(exit_px-entry)
        out.append(result-(spread[i]+0.00002*cost_mult))
    if not out:return {"trades":0,"win_rate":None,"expectancy_r":None,"profit_factor":None,"max_drawdown_r":None,"longest_losing_streak":0}
    r=np.asarray(out)/np.nanmedian(atr[np.flatnonzero(sig)[:len(out)]])
    curve=np.cumsum(r); dd=curve-np.maximum.accumulate(curve); streak=0
    wins=r[r>0].sum(); loss=-r[r<0].sum()
    return {"trades":int(len(r)),"win_rate":float((r>0).mean()),"expectancy_r":float(r.mean()),"profit_factor":float(wins/loss) if loss else None,"max_drawdown_r":float(dd.min()),"longest_losing_streak":int(streak)}

def main():
    digest=hashlib.sha256(DATA.read_bytes()).hexdigest(); assert digest==EXPECTED
    d=pd.read_csv(DATA,parse_dates=["timestamp"]).sort_values("timestamp").reset_index(drop=True); c=d.close
    atrs={p:(d.high-d.low).rolling(p).mean().shift(1) for p in (7,14,21,28)}
    ema20=c.ewm(span=20,adjust=False).mean(); ema50=c.ewm(span=50,adjust=False).mean(); r=c.pct_change(); rsi=100-(100/(1+(r.clip(lower=0).rolling(14).mean()/(-r.clip(upper=0).rolling(14).mean()).replace(0,np.nan))))
    hi=d.high.rolling(30).max().shift(1); lo=d.low.rolling(30).min().shift(1); mid=c.rolling(20).mean(); sd=c.rolling(20).std()
    strategies={"trend_pullback":np.where((ema20>ema50)&(rsi<50),1,np.where((ema20<ema50)&(rsi>50),-1,0)),"range_mean_reversion":np.where((c<mid-2*sd)&(rsi<35),1,np.where((c>mid+2*sd)&(rsi>65),-1,0)),"donchian_breakout":np.where(c>hi,1,np.where(c<lo,-1,0))}
    rows=[]; n=len(d); split=int(n*.6); val_end=int(n*.8)
    for name,s in strategies.items():
      for ap,atr in atrs.items():
       for sl,tp,hold in [(0.75,1.5,10),(1.0,1.5,20),(1.0,2.0,30),(1.5,2.0,45)]:
        for stage,a,b in [("train",0,split),("validation",split,val_end)]:
         q=evaluate(d.iloc[a:b],s[a:b],atr[a:b],sl,tp,hold); rows.append({"experiment_id":f"V2C2-{name}-atr{ap}-sl{sl}-tp{tp}-h{hold}","strategy":name,"atr_period":ap,"sl":sl,"tp":tp,"max_hold":hold,"stage":stage,"dataset_sha256":digest,**q})
    reg=pd.DataFrame(rows); reg.to_csv(OUT/"EXPERIMENT_REGISTRY.csv",index=False); val=reg[reg.stage=="validation"]; finalists=val[(val.trades>=100)&(val.expectancy_r>0)&(val.profit_factor>1)]
    best=finalists.sort_values(["expectancy_r","profit_factor"],ascending=False).head(5).to_dict("records")
    result={"campaign":"V2 RESEARCH #2","timestamp":datetime.now(UTC).isoformat(),"dataset_sha256":digest,"rows":n,"experiments_completed":len(reg),"configurations_evaluated":len(reg),"triple_barrier_configurations":len(reg)//2,"atr_target_configurations":len(reg)//2,"best_validation_candidates":best,"final_test_accessed":False,"status":"NO VALIDATION-POSITIVE FINALIST" if not best else "FINALISTS FROZEN"}
    (OUT/"CAMPAIGN2_RESULTS.json").write_text(json.dumps(result,indent=2,default=str))
if __name__=="__main__": main()
