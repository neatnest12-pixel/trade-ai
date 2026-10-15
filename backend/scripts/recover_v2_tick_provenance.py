"""Read-only MT5 tick extraction and SELL-parent spread alignment."""
from __future__ import annotations

import csv
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import MetaTrader5 as mt5

ROOT = Path(__file__).resolve().parents[2]; OUT = ROOT / "certified_data" / "v2_research"
START = datetime(2026, 8, 14, tzinfo=UTC); END = datetime(2026, 8, 20, tzinfo=UTC)
def main():
    if not mt5.initialize(): raise RuntimeError(mt5.last_error())
    try: ticks = mt5.copy_ticks_range("EURUSD", START, END, mt5.COPY_TICKS_ALL)
    finally: mt5.shutdown()
    if ticks is None or len(ticks)==0: raise RuntimeError("No ticks")
    OUT.mkdir(parents=True,exist_ok=True); version=datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ'); path=OUT/f"EURUSD_V2_TICKS_{version}.csv"
    with path.open('w',newline='',encoding='utf8') as h:
        w=csv.writer(h); w.writerow(['timestamp_utc','bid','ask','last','volume','flags'])
        for t in ticks: w.writerow([datetime.fromtimestamp(int(t['time']),UTC).isoformat(),float(t['bid']),float(t['ask']),float(t['last']),float(t['volume']),int(t['flags'])])
    tick_rows=[{'timestamp_utc':datetime.fromtimestamp(int(t['time']),UTC),'bid':float(t['bid']),'ask':float(t['ask'])} for t in ticks]
    preds=[json.loads(x) for x in (ROOT/'certified_data'/'FORWARD_PAPER_JOURNAL.jsonl').read_text(encoding='utf8').splitlines() if x.strip()]
    preds=[p for p in preds if p.get('type')=='prediction' and p.get('decision')=='SELL']; aligned=[]
    for p in preds:
        entry=datetime.fromisoformat(p['prediction_time'].replace('Z','+00:00')).astimezone(UTC); prior=[t for t in tick_rows if t['timestamp_utc']<=entry]; t=prior[-1] if prior else None
        age=(entry-t['timestamp_utc']).total_seconds()*1000 if t else None; quality='HIGH' if age is not None and age<=5000 else 'MEDIUM' if age is not None and age<=30000 else 'LOW'
        aligned.append({'parent_prediction_id':p['record_hash'],'signal_time':p['prediction_time'],'entry_time':entry.isoformat(),'tick_time':t['timestamp_utc'].isoformat() if t else None,'spread_age_ms':age,'spread_bid':t['bid'] if t else None,'spread_ask':t['ask'] if t else None,'spread_value':(t['ask']-t['bid']) if t and t['ask']>0 and t['bid']>0 else None,'spread_pips':(t['ask']-t['bid'])*100000 if t and t['ask']>0 and t['bid']>0 else None,'spread_source':'MT5_TICK' if t else 'UNAVAILABLE','cost_quality':quality})
    prov=OUT/f"v2_cost_provenance_ticks_{version}.jsonl"; prov.write_text(''.join(json.dumps(x,sort_keys=True)+'\n' for x in aligned),encoding='utf8')
    print(json.dumps({'tick_dataset':str(path),'tick_rows':len(tick_rows),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'parents':len(aligned),'quality':{q:sum(x['cost_quality']==q for x in aligned) for q in ('HIGH','MEDIUM','LOW')},'provenance':str(prov)}))
if __name__=='__main__':main()
