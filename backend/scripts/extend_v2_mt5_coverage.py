"""Read-only MT5 extraction and deterministic M5/M15 research derivation."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "certified_data" / "v2_research"
START = datetime(2026, 8, 13, tzinfo=UTC)

def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    import MetaTrader5 as mt5
    if not mt5.initialize(): raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    try:
        end = datetime.now(UTC)
        rates = mt5.copy_rates_range("EURUSD", mt5.TIMEFRAME_M1, START, end)
    finally:
        mt5.shutdown()
    if rates is None or len(rates) == 0: raise RuntimeError("No MT5 M1 history returned")
    frame = pd.DataFrame(rates).rename(columns={"time":"timestamp"})
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], unit="s", utc=True)
    frame = frame.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    frame = frame[frame["timestamp"] < pd.Timestamp(end)].copy()
    OUT.mkdir(parents=True, exist_ok=True)
    version = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    m1 = OUT / f"EURUSD_V2_RESEARCH_M1_{version}.csv"
    frame.to_csv(m1, index=False, date_format="%Y-%m-%dT%H:%M:%S%z")
    frame = frame.set_index("timestamp")
    agg = {"open":"first","high":"max","low":"min","close":"last","tick_volume":"sum","spread":"mean","real_volume":"sum"}
    outputs = {"M1":m1}
    for tf, rule in (("M5","5min"),("M15","15min")):
        out = frame.resample(rule, origin="epoch", label="left", closed="left").agg(agg).dropna(subset=["open","high","low","close"])
        path = OUT / f"EURUSD_V2_RESEARCH_{tf}_{version}.csv"; out.reset_index().to_csv(path,index=False,date_format="%Y-%m-%dT%H:%M:%S%z"); outputs[tf]=path
    manifest = {"dataset_version":version,"source":"MT5 ICMarketsSC-Demo read-only","symbol":"EURUSD","start":str(frame.index.min()),"end":str(frame.index.max()),"files":{k:{"path":str(v),"rows":sum(1 for _ in v.open(encoding='utf8'))-1,"sha256":digest(v)} for k,v in outputs.items()},"created_at":datetime.now(UTC).isoformat()}
    (OUT/f"manifest_{version}.json").write_text(json.dumps(manifest,indent=2),encoding="utf8")
    print(json.dumps(manifest))

if __name__ == "__main__": main()
