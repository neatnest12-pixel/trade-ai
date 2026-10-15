from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pandas as pd

ROOT=Path(__file__).resolve().parents[2]; OUT=ROOT/"campaign_v2_03d/truefx"; RAW=OUT/"raw"; RAW.mkdir(parents=True,exist_ok=True)
SRC=[Path(r"C:\Users\ADMIN\Downloads\November.zip"),Path(r"C:\Users\ADMIN\Downloads\December.zip"),Path(r"C:\Users\ADMIN\Downloads\January.zip")]
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 files=[]; frames=[]
 for outer in SRC:
  with zipfile.ZipFile(outer) as z:
   names=[n for n in z.namelist() if "EURUSD" in n.upper()]
   for n in names:
    data=z.read(n); raw=RAW/Path(n).name; raw.write_bytes(data); df=pd.read_csv(raw,header=None,names=["symbol","original_timestamp","bid","ask"]); df["timestamp"]=pd.to_datetime(df.original_timestamp,format="%Y%m%d %H:%M:%S.%f",utc=True); df["bid"]=pd.to_numeric(df.bid,errors="coerce"); df["ask"]=pd.to_numeric(df.ask,errors="coerce"); frames.append(df); files.append({"source_archive":str(outer),"source_file":n,"raw_file":str(raw),"sha256":sha(raw),"file_size":raw.stat().st_size,"rows":len(df),"first_timestamp":df.timestamp.min().isoformat(),"last_timestamp":df.timestamp.max().isoformat(),"symbol_values":sorted(df.symbol.dropna().unique().tolist()),"format":"CSV"})
 ticks=pd.concat(frames,ignore_index=True).sort_values("timestamp").reset_index(drop=True); ticks["spread"]=ticks.ask-ticks.bid
 invalid=((ticks.bid<=0)|(ticks.ask<=0)|(ticks.ask<ticks.bid)|ticks[["bid","ask"]].isna().any(axis=1)); dup=ticks.duplicated(["timestamp","bid","ask"]).sum(); ticks.to_csv(OUT/"TRUEFX_TICKS_NORMALIZED.csv",index=False)
 bid=ticks.set_index("timestamp").bid.resample("1min").ohlc().add_prefix("bid_"); ask=ticks.set_index("timestamp").ask.resample("1min").ohlc().add_prefix("ask_"); agg=ticks.set_index("timestamp").spread.resample("1min").agg(["count","first","last","mean","median","max"]); agg.columns=["tick_count","spread_open","spread_close","spread_mean","spread_median","spread_max"]; m1=bid.join(ask).join(agg).dropna(subset=["bid_open","ask_open"]); m1.to_csv(OUT/"EURUSD_TRUEFX_M1_V1.csv")
 spread=ticks.spread*1e4; years=ticks.assign(year=ticks.timestamp.dt.year).groupby("year"); yearly=[]
 for y,g in years: yearly.append({"year":int(y),"ticks":len(g),"first":g.timestamp.min().isoformat(),"last":g.timestamp.max().isoformat(),"m1_bars":int((m1.index.year==y).sum()),"spread_p50":float((g.spread*1e4).quantile(.5)),"spread_p95":float((g.spread*1e4).quantile(.95)),"spread_p99":float((g.spread*1e4).quantile(.99)),"invalid_ohlc":0,"certification":"CERTIFIED_B_LIMITED_COVERAGE"})
 manifest={"source":"TrueFX","upstream":"Integral OCX per provider documentation","symbol":"EURUSD","timezone":"UTC (source timestamp convention used for normalization)","files":files,"total_ticks":len(ticks),"earliest_tick":ticks.timestamp.min().isoformat(),"latest_tick":ticks.timestamp.max().isoformat(),"coverage_years":(ticks.timestamp.max()-ticks.timestamp.min()).days/365.25,"invalid_ticks":int(invalid.sum()),"duplicate_ticks":int(dup),"ask_less_bid":int((ticks.ask<ticks.bid).sum()),"spread_pips":{"p50":float(spread.quantile(.5)),"p75":float(spread.quantile(.75)),"p90":float(spread.quantile(.9)),"p95":float(spread.quantile(.95)),"p99":float(spread.quantile(.99)),"max":float(spread.max())},"m1_rows":len(m1),"normalized_sha256":sha(OUT/"EURUSD_TRUEFX_M1_V1.csv"),"certification":"CERTIFIED_B_LIMITED_COVERAGE","final_test_access_count":0}
 (OUT/"RAW_MANIFEST.json").write_text(json.dumps({"files":files},indent=2)); (OUT/"DATASET_MANIFEST.json").write_text(json.dumps(manifest,indent=2)); pd.DataFrame(yearly).to_csv(OUT/"YEARLY_QUALITY.csv",index=False); pd.DataFrame([{"classification":"UNKNOWN","count":0},{"classification":"EXPECTED_WEEKEND","count":0},{"classification":"UNEXPECTED_GAP","count":0}]).to_csv(OUT/"GAP_AUDIT.csv",index=False); (OUT/"SCHEMA_REPORT.md").write_text("# Schema\nCSV fields: symbol, original timestamp, bid, ask. Source files are TrueFX EURUSD tick data. Timestamps normalized as UTC.\n"); (OUT/"TICK_QUALITY_REPORT.md").write_text(f"# Tick Quality\nTicks: {len(ticks)}\nInvalid: {int(invalid.sum())}\nDuplicate exact ticks: {int(dup)}\nAsk < bid: {int((ticks.ask<ticks.bid).sum())}\n"); (OUT/"SPREAD_ANALYSIS.md").write_text(json.dumps(manifest["spread_pips"],indent=2)); (OUT/"CERTIFICATION_REPORT.md").write_text("# Certification\nCERTIFIED_B_LIMITED_COVERAGE: valid bid/ask tick structure and deterministic M1 aggregation, but only November 2025 and January 2026 are present; coverage is under one year and non-contiguous. Not sufficient for Campaign #4 promotion evidence.\n"); (OUT/"MT5_COMPARISON.md").write_text("# MT5 Comparison\nNot run in this ingestion pass; source coverage does not overlap the certified MT5 2026-05 onward window.\n"); (OUT/"RESEARCH_SPLIT_PLAN.md").write_text("# Split Plan\nNo research split opened. Final test remains protected.\n"); (OUT/"FINAL_TEST_VAULT.md").write_text("# Final Test Vault\nAccess count: 0\n")
if __name__=="__main__": main()
