import hashlib
import json
from pathlib import Path

import pandas as pd

ROOT=Path(__file__).resolve().parents[2]; OUT=ROOT/"campaign_v2_03d/dukascopy"; dfile=next((OUT/"week_sample").glob("*.csv")); d=pd.read_csv(dfile); d["ts"]=pd.to_datetime(d.timestamp,unit="ms",utc=True).dt.floor("min"); dm=d.groupby("ts").agg(bid=("bidPrice","last"),ask=("askPrice","last"),spread=("askPrice",lambda x:0)); dm["spread"]=d.assign(spread=d.askPrice-d.bidPrice).groupby("ts").spread.median(); dm.to_csv(OUT/"SAMPLE_M1.csv"); rows=[]
for chunk in pd.read_csv(ROOT/"campaign_v2_03d/truefx/TRUEFX_TICKS_NORMALIZED.csv",parse_dates=["timestamp"],chunksize=250000):
 q=chunk[(chunk.timestamp>="2025-11-10")&(chunk.timestamp<"2025-11-15")].copy()
 if len(q): rows.append(q)
t=pd.concat(rows); t["timestamp"]=pd.to_datetime(t.timestamp,utc=True,format="mixed"); t["ts"]=t.timestamp.dt.floor("min"); tm=t.groupby("ts").agg(bid=("bid","last"),ask=("ask","last"),spread=("spread","median")); x=dm.join(tm,lsuffix="_d",rsuffix="_t").dropna()
def pct(s,q): return float(s.quantile(q))
res={"dukascopy_minutes":len(dm),"truefx_minutes":len(tm),"common_minutes":len(x),"dukascopy_only":len(dm)-len(x),"truefx_only":len(tm)-len(x),"common_coverage":len(x)/len(dm),"bid_return_correlation":float(x.bid_d.pct_change().corr(x.bid_t.pct_change())),"ask_return_correlation":float(x.ask_d.pct_change().corr(x.ask_t.pct_change())),"directional_agreement":float(((x.bid_d.diff()*x.bid_t.diff())>0).mean()),"bid_abs_diff_p50":pct((x.bid_d-x.bid_t).abs(),.5),"bid_abs_diff_p95":pct((x.bid_d-x.bid_t).abs(),.95),"bid_abs_diff_p99":pct((x.bid_d-x.bid_t).abs(),.99),"ask_abs_diff_p50":pct((x.ask_d-x.ask_t).abs(),.5),"ask_abs_diff_p95":pct((x.ask_d-x.ask_t).abs(),.95),"ask_abs_diff_p99":pct((x.ask_d-x.ask_t).abs(),.99),"truefx_spread_p50":pct(x.spread_t*1e4,.5),"truefx_spread_p95":pct(x.spread_t*1e4,.95),"dukascopy_spread_p50":pct(x.spread_d*1e4,.5),"dukascopy_spread_p95":pct(x.spread_d*1e4,.95)}
(OUT/"CROSS_SOURCE_METRICS.json").write_text(json.dumps(res,indent=2)); (OUT/"SAMPLE_M1_MANIFEST.json").write_text(json.dumps({"ticks":len(d),"m1_rows":len(dm),"first_m1":dm.index.min().isoformat(),"last_m1":dm.index.max().isoformat(),"sha256":hashlib.sha256(dfile.read_bytes()).hexdigest()},indent=2)); (OUT/"SAMPLE_SPREAD_ANALYSIS.md").write_text(json.dumps({"p50":pct(d.askPrice.sub(d.bidPrice)*1e4,.5),"p75":pct(d.askPrice.sub(d.bidPrice)*1e4,.75),"p90":pct(d.askPrice.sub(d.bidPrice)*1e4,.9),"p95":pct(d.askPrice.sub(d.bidPrice)*1e4,.95),"p99":pct(d.askPrice.sub(d.bidPrice)*1e4,.99),"max":pct(d.askPrice.sub(d.bidPrice)*1e4,1)},indent=2))
if __name__=="__main__": pass
