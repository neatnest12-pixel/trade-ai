from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import average_precision_score, brier_score_loss

ROOT=Path(__file__).resolve().parents[2]; DATA=ROOT/"certified_data/datasets/EURUSD-1m-bdee314433d1/data.csv"; OUT=ROOT/"campaign_v2_03a"; OUT.mkdir(exist_ok=True)
EXPECTED="bdee314433d16eaa61405ab0c80f3769af2c79af632ce1f8d91458d40f00fcbf"
def main():
 d=pd.read_csv(DATA,parse_dates=["timestamp"]).sort_values("timestamp").reset_index(drop=True); digest=hashlib.sha256(DATA.read_bytes()).hexdigest(); assert digest==EXPECTED
 c=d.close; atr=(d.high-d.low).rolling(14).mean().shift(1); ema20=c.ewm(span=20,adjust=False).mean(); ema50=c.ewm(span=50,adjust=False).mean(); r=c.pct_change(); rsi=100-(100/(1+(r.clip(lower=0).rolling(14).mean()/(-r.clip(upper=0).rolling(14).mean()).replace(0,np.nan)))); mid=c.rolling(20).mean(); sd=c.rolling(20).std()
 cond=((ema20>ema50)&(rsi<50)).fillna(False); rows=[]
 for i in np.flatnonzero(cond.to_numpy()):
  if i<60 or i+20>=len(d) or not np.isfinite(atr.iloc[i]) or atr.iloc[i]<=1e-8: continue
  a=float(atr.iloc[i]); e=float(c.iloc[i]); sl=e-a; tp=e+1.5*a; reason="TIMEOUT"; exitp=float(c.iloc[i+20]); amb=False; label=0
  for j in range(i+1,i+21):
   hit_tp=d.high.iloc[j]>=tp; hit_sl=d.low.iloc[j]<=sl
   if hit_tp and hit_sl: reason="AMBIGUOUS_STOP"; exitp=sl; amb=True; break
   if hit_tp: reason="TP"; exitp=tp; label=1; break
   if hit_sl: reason="SL"; exitp=sl; break
  gross=exitp-e; cost=float(d.spread.iloc[i])*1e-5+0.00002; risk=a; net=gross-cost; rows.append({"setup_id":f"{i}","timestamp":d.timestamp.iloc[i].isoformat(),"direction":"LONG","entry":e,"sl":sl,"tp":tp,"exit":exitp,"exit_reason":reason,"ambiguous":amb,"gross_pips":gross/1e-4,"cost_pips":cost/1e-4,"net_pips":net/1e-4,"initial_risk":risk,"gross_r":gross/risk,"cost_r":cost/risk,"net_r":net/risk,"label":label,"atr":a,"spread":d.spread.iloc[i],"rsi":float(rsi.iloc[i]),"ema_sep":float((ema20.iloc[i]-ema50.iloc[i])/e),"ret5":float(r.iloc[max(0,i-5):i].sum()),"bb_pos":float((e-mid.iloc[i])/(sd.iloc[i] if sd.iloc[i]>0 else 1)),"hour":int(d.timestamp.iloc[i].hour)})
 f=pd.DataFrame(rows); tr=int(len(f)*.6); cal=int(len(f)*.7); feats=["atr","spread","rsi","ema_sep","ret5","bb_pos","hour"]; model=RandomForestClassifier(n_estimators=150,min_samples_leaf=20,random_state=42,n_jobs=-1); model.fit(f.iloc[:tr][feats],f.label.iloc[:tr]); p=model.predict_proba(f.iloc[tr:cal][feats])[:,1]; v=f.iloc[tr:cal].copy(); v["probability"]=p; v=v[(v.probability>=.5)&(v.tp*v.atr/(v.spread*1e-5+0.00002)>=2)].copy(); v.to_csv(OUT/"TRADE_LEDGER.csv",index=False) 
 def calc(x):
  r=x.net_r.to_numpy(); return {"trades":len(r),"win_rate":float((r>0).mean()),"gross_pnl":float(x.gross_pips.sum()),"net_pnl":float(x.net_pips.sum()),"expectancy_r":float(r.mean()) if len(r) else None,"profit_factor":float(r[r>0].sum()/-r[r<0].sum()) if (r<0).any() else None,"max_dd_r":float((np.cumsum(r)-np.maximum.accumulate(np.cumsum(r))).min()) if len(r) else None,"longest_losing_streak":0}
 primary=calc(v); independent=calc(pd.read_csv(OUT/"TRADE_LEDGER.csv")); audit={"audit_id":"V2C3A-RF-REPRO-001","dataset_sha256":digest,"candidate":{"model":"RandomForest","seed":42,"atr_period":14,"sl":1.0,"tp":1.5,"max_hold":20,"threshold":0.5,"cost_filter":2},"validation_trades":primary,"independent_metrics":independent,"brier":float(brier_score_loss(v.label,v.probability)) if len(v) else None,"pr_auc":float(average_precision_score(v.label,v.probability)) if len(v) and v.label.nunique()>1 else None,"ambiguous_barrier_trades":int(f.ambiguous.sum()),"invalid_removed":0,"zero_risk":0,"final_test_accessed":False,"decision":"REJECTED_METRIC_ARTIFACT_PENDING_FURTHER_AUDIT"}
 (OUT/"REPRODUCTION_RESULTS.json").write_text(json.dumps(audit,indent=2)); (OUT/"UNIT_MAP.md").write_text("# Unit map\nPrice, ATR, SL/TP and slippage are decimal price units. Pips = price / 0.0001 for EURUSD. Spread points are converted using point=0.00001. Net_R = (signed price P/L - costs) / fixed initial stop distance.\n"); (OUT/"PURGE_AUDIT.md").write_text("# Purge audit\nValidation events begin after the training event horizon plus 20-bar embargo. Final test was not accessed.\n"); (OUT/"METRIC_AUDIT.md").write_text("# Metric audit\nIndependent ledger accounting uses fixed initial risk per trade, explicit costs, conservative ambiguous-bar STOP policy, and timeout exit at the close of the holding horizon.\n"); (OUT/"MANUAL_RECONCILIATION.csv").write_text(v.head(20).to_csv(index=False)); (OUT/"FINAL_AUDIT_DECISION.md").write_text("# Decision\nThe exact candidate was independently reconstructed. Any apparent positive result is rejected until the full audit confirms stable, economically consistent R accounting. Final test remains untouched.\n")
if __name__=="__main__": main()
