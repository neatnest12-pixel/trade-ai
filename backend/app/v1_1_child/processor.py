"""Manual-only read-only processor; no model inference or broker execution."""
from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path

from .contract import HASH, atr14, eligible, make, priced_outcome
from .journal import append, rows

PARENT=Path("certified_data/FORWARD_PAPER_JOURNAL.jsonl");CHILD=Path("certified_data/PAPER_TRADE_JOURNAL.jsonl")
def guard()->None:
 if os.getenv("MT5_READ_ONLY","").lower()!="true" or os.getenv("TRADING_EXECUTION_ENABLED","").lower()!="false":raise RuntimeError("READ_ONLY_GUARD")
def process(history_for_parent):
 """Caller supplies strictly pre-entry completed candles and next M1 open; no MT5 writes."""
 guard();parents=eligible([json.loads(x) for x in PARENT.read_text(encoding="utf8").splitlines() if x.strip()])
 known={(x.get("parent_prediction_id"),x.get("trade_contract_hash")) for x in rows(CHILD)}
 created=[]
 for p in parents:
  if (p["record_hash"],HASH) in known:continue
  h,next_open,meta=history_for_parent(p)
  c=make(p,Decimal(str(next_open)),atr14(h),Decimal(str(meta["tick_value"])),Decimal(str(meta["lot_step"])),Decimal(str(meta["min_lot"])))
  r=c.record();created.append(append(CHILD,r))
 return created

def persist_outcomes(contract, candles, spread, timestamp: str) -> int:
 """Persist at most 12 immutable results after a contract is resolvable."""
 count=0
 for rr in (2,3,5,10):
  final=None
  for n,candle in enumerate(candles,1):
   final=priced_outcome(contract,rr,Decimal(str(candle["high"])),Decimal(str(candle["low"])),Decimal(str(candle["close"])),n,"IDEALIZED",spread)
   if final.get("outcome")!="UNRESOLVED":break
  if not final or final.get("outcome")=="UNRESOLVED":continue
  for profile in ("IDEALIZED","BASE","STRESS"):
   result=priced_outcome(contract,rr,Decimal(str(candle["high"])),Decimal(str(candle["low"])),Decimal(str(candle["close"])),n,profile,spread)
   rec={"record_type":"RR_OUTCOME","created_at":timestamp,"paper_trade_id":contract.record()["paper_trade_id"],"parent_prediction_id":contract.parent_prediction_id,"rr_profile":f"RR_{rr}","cost_profile":profile,"entry_price":contract.entry_price,"stop_price":contract.stop_price,"target_price":contract.entry_price+(Decimal(1) if contract.decision=="BUY" else Decimal(-1))*contract.risk_distance*rr,"holding_candles":n,**result}
   count+=bool(append(CHILD,rec))
 return count
