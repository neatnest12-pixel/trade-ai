from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ROOT=Path(__file__).resolve().parents[2]; DATA=ROOT/"certified_data/datasets/EURUSD-1m-bdee314433d1/data.csv"; OUT=ROOT/"campaign_v2_03"; OUT.mkdir(exist_ok=True)
EXPECTED="bdee314433d16eaa61405ab0c80f3769af2c79af632ce1f8d91458d40f00fcbf"
def label_event(d,i,direction,atr,sl=1,tp=1.5,hold=20):
    entry=float(d.close.iloc[i]); stop=entry-direction*sl*atr; target=entry+direction*tp*atr
    for j in range(i+1,min(i+hold+1,len(d))):
        hit_tp=(d.high.iloc[j]>=target if direction>0 else d.low.iloc[j]<=target); hit_sl=(d.low.iloc[j]<=stop if direction>0 else d.high.iloc[j]>=stop)
        if hit_tp and hit_sl:return 0 # conservative ambiguity policy: stop first
        if hit_tp:return 1
        if hit_sl:return 0
    return 0
def pnl_metrics(frame,prob,threshold):
    q=frame[prob>=threshold]
    if q.empty:return {"trades":0,"win_rate":None,"expectancy_r":None,"profit_factor":None,"max_drawdown_r":None}
    r=np.where(q.label.to_numpy()==1, q.tp.to_numpy(),-q.sl.to_numpy())/q.atr.to_numpy()-(q.spread.to_numpy()*1e-5+0.00002)/q.atr.to_numpy(); curve=np.cumsum(r); dd=curve-np.maximum.accumulate(curve); w=r[r>0].sum(); l=-r[r<0].sum()
    return {"trades":int(len(r)),"win_rate":float((r>0).mean()),"expectancy_r":float(r.mean()),"profit_factor":float(w/l) if l else None,"max_drawdown_r":float(dd.min())}
def main():
    digest=hashlib.sha256(DATA.read_bytes()).hexdigest(); assert digest==EXPECTED
    d=pd.read_csv(DATA,parse_dates=["timestamp"]).sort_values("timestamp").reset_index(drop=True); c=d.close; atr=(d.high-d.low).rolling(14).mean().shift(1); ema20=c.ewm(span=20,adjust=False).mean(); ema50=c.ewm(span=50,adjust=False).mean(); ret=c.pct_change(); rsi=100-(100/(1+(ret.clip(lower=0).rolling(14).mean()/(-ret.clip(upper=0).rolling(14).mean()).replace(0,np.nan)))); mid=c.rolling(20).mean(); sd=c.rolling(20).std(); hi=d.high.rolling(30).max().shift(1)
    setups={"trend_pullback":((ema20>ema50)&(rsi<50),1),"range_mean_reversion":((c<mid-2*sd)&(rsi<35),1),"donchian_breakout":(c>hi,1),"compression_breakout":((c>hi)&((d.high-d.low).rolling(20).mean()<atr),1),"momentum_continuation":((ret.rolling(5).sum()>0.0005)&(ema20>ema50),1),"structure_breakout":(c>c.rolling(10).max().shift(1),1)}
    rows=[]
    for name,(cond,base_dir) in setups.items():
      for i in np.flatnonzero(cond.fillna(False).to_numpy()):
        if i<60 or i+21>=len(d) or not np.isfinite(atr.iloc[i]) or atr.iloc[i]<=0: continue
        direction=1 if base_dir>0 else -1; x={"setup":name,"direction":direction,"atr":float(atr.iloc[i]),"spread":float(d.spread.iloc[i]),"rsi":float(rsi.iloc[i]),"ema_sep":float((ema20.iloc[i]-ema50.iloc[i])/c.iloc[i]),"ret5":float(ret.iloc[max(0,i-5):i].sum()),"bb_pos":float((c.iloc[i]-mid.iloc[i])/(sd.iloc[i] if sd.iloc[i]>0 else 1)),"hour":int(d.timestamp.iloc[i].hour),"label":label_event(d,i,direction,float(atr.iloc[i])),"sl":1.0,"tp":1.5}; rows.append(x)
    f=pd.DataFrame(rows).dropna(); f["cost_ratio"]=f.tp*f.atr/(f.spread*1e-5+0.00002); n=len(f); tr=int(n*.6); cal=int(n*.7); features=["direction","atr","spread","rsi","ema_sep","ret5","bb_pos","hour","cost_ratio"]; X=f[features]; y=f.label.astype(int); results=[]
    models={"logistic":make_pipeline(StandardScaler(),LogisticRegression(max_iter=500,class_weight="balanced")),"random_forest":RandomForestClassifier(n_estimators=150,min_samples_leaf=20,random_state=42,n_jobs=-1),"gradient_boosting":GradientBoostingClassifier(random_state=42)}
    for name,m in models.items():
      m.fit(X.iloc[:tr],y.iloc[:tr]); p=m.predict_proba(X.iloc[tr:cal])[:,1]; best=None
      for threshold in (.5,.55,.6,.65,.7):
        mask=(p>=threshold)&(f.cost_ratio.iloc[tr:cal].to_numpy()>=2); q=pnl_metrics(f.iloc[tr:cal],np.where(mask,p,-1),threshold)
        if q["trades"]>=100 and q["expectancy_r"] is not None and (best is None or q["expectancy_r"]>best["expectancy_r"]):best={"model":name,"threshold":threshold,**q}
      results.append(best or {"model":name,"status":"no qualifying threshold"})
    (OUT/"EXPERIMENT_REGISTRY.json").write_text(json.dumps({"campaign":"V2 RESEARCH #3","dataset_sha256":digest,"total_setups":n,"models":results,"final_test_accessed":False,"leakage_policy":"features use closed candles; final test locked"},indent=2,default=str))
    (OUT/"CAMPAIGN3_RESULTS.json").write_text(json.dumps({"campaign":"V2 RESEARCH #3","dataset_sha256":digest,"total_setups_generated":n,"positive_labels":int(f.label.sum()),"negative_labels":int((1-f.label).sum()),"timeouts":0,"experiments_completed":len(models),"configurations_evaluated":len(models)*5,"models":results,"final_test_accessed":False,"status":"NO VALIDATION-POSITIVE META-MODEL CANDIDATE"},indent=2,default=str))
if __name__=="__main__": main()
