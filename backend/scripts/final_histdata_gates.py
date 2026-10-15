import json
from pathlib import Path

import pandas as pd

ROOT=Path(__file__).resolve().parents[2]; out=ROOT/"campaign_v2_03d/histdata_v2"; h=pd.read_csv(out/"EURUSD_HISTDATA_M1_V2.csv",parse_dates=["timestamp"]); h["ts"]=h.timestamp.dt.floor("min")
def compare(path,kind):
 if kind=="truefx":
  parts=[]
  for c in pd.read_csv(path,parse_dates=["timestamp"],chunksize=250000):
   c=c[(c.timestamp>="2025-11-02")&(c.timestamp<="2026-01-30")]; parts.append(c)
  x=pd.concat(parts); x["timestamp"]=pd.to_datetime(x.timestamp,utc=True,format="mixed"); x["ts"]=x.timestamp.dt.floor("min"); x=x.groupby("ts").bid.last().rename("ref")
 else:
  d=pd.read_csv(path); d["ts"]=pd.to_datetime(d.timestamp,unit="ms",utc=True).dt.floor("min"); x=d.groupby("ts").bidPrice.last().rename("ref")
 a=h.set_index("ts").close.rename("hist").to_frame().join(x).dropna(); ret=a.hist.pct_change(); rr=a.ref.pct_change(); return {"common":len(a),"corr":float(ret.corr(rr)),"direction":float(((ret*rr)>0).mean()),"med_abs_pips":float((a.hist-a.ref).abs().median()*1e4),"p95_pips":float((a.hist-a.ref).abs().quantile(.95)*1e4)}
res={"truefx":compare(ROOT/"campaign_v2_03d/truefx/TRUEFX_TICKS_NORMALIZED.csv","truefx"),"dukascopy":compare(next((ROOT/"campaign_v2_03d/dukascopy/week_sample").glob("*.csv")),"duka")}; (out/"CROSS_SOURCE_METRICS.json").write_text(json.dumps(res,indent=2))
