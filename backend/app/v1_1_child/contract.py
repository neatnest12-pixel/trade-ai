from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from decimal import ROUND_DOWN, Decimal
from typing import Any

PARENT_SNAPSHOT="v1-paper-r4-0d891a2356e35464"
PARENT_CONFIG="0d891a2356e35464dbe3ba9f51c50fe240e40feff84fe4008d8b1564b55f9eac"
VERSION="v1.1-paper-trade-01"
FROZEN_AT="2026-08-18T04:45:31+00:00"
POLICY={"entry":"NEXT_M1_OPEN_APPROXIMATION","atr_period":14,"atr_multiplier":"1.5","max_hold_m1":60,"rr":[2,3,5,10],"intrabar":"STOP_FIRST","risk_inr":"25","reference_capital_inr":"2500","costs":{"IDEALIZED":"0","BASE":"observed_spread+0.05R","STRESS":"observed_spread*1.5+0.15R"},"start":FROZEN_AT}
HASH=hashlib.sha256(json.dumps(POLICY,sort_keys=True,separators=(",",":")).encode()).hexdigest()

def official_parents(rows:list[dict[str,Any]])->list[dict[str,Any]]:
 g:dict[tuple[Any,...],list[dict[str,Any]]]={}
 for r in rows:
  if r.get("type")!="prediction" or r.get("v1_snapshot_id")!=PARENT_SNAPSHOT or r.get("v1_config_hash")!=PARENT_CONFIG:continue
  k=(r.get("v1_snapshot_id"),r.get("v1_config_hash"),r.get("symbol"),r.get("timeframe"),r.get("candle_timestamp"));g.setdefault(k,[]).append(r)
 return [min(v,key=lambda x:x.get("prediction_time","")) for v in g.values()]

def eligible(rows:list[dict[str,Any]])->list[dict[str,Any]]:
 return [r for r in official_parents(rows) if r.get("decision") in {"BUY","SELL"} and r.get("candle_timestamp","")>FROZEN_AT]

def atr14(completed:list[dict[str,Any]])->Decimal:
 if len(completed)<15:raise ValueError("INSUFFICIENT_ATR_HISTORY")
 tr=[max(Decimal(str(x["high"]))-Decimal(str(x["low"])),abs(Decimal(str(x["high"]))-Decimal(str(p["close"]))),abs(Decimal(str(x["low"]))-Decimal(str(p["close"])))) for p,x in zip(completed[-15:-1],completed[-14:],strict=True)]
 return sum(tr,Decimal())/Decimal(14)

@dataclass(frozen=True)
class Contract:
 parent_prediction_id:str; parent_v1_snapshot_id:str; parent_v1_config_hash:str; decision:str; signal_candle_timestamp:str; entry_timestamp:str; entry_price:Decimal; atr_value:Decimal; stop_price:Decimal; risk_distance:Decimal; theoretical_volume:Decimal|None
 def record(self)->dict[str,Any]:
  x=asdict(self);sign=Decimal(1) if self.decision=="BUY" else Decimal(-1);x.update({"record_type":"TRADE_CONTRACT","paper_trade_id":hashlib.sha256((self.parent_prediction_id+HASH).encode()).hexdigest(),"trade_contract_version":VERSION,"trade_contract_hash":HASH,"trade_contract_frozen_at":FROZEN_AT,"entry_source":"NEXT_M1_OPEN_APPROXIMATION","atr_period":14,"atr_multiplier":"1.5","reference_capital":"2500","reference_risk":"25","max_hold_m1":60,"status":"OPEN","rr_2_target":self.entry_price+sign*self.risk_distance*2,"rr_3_target":self.entry_price+sign*self.risk_distance*3,"rr_5_target":self.entry_price+sign*self.risk_distance*5,"rr_10_target":self.entry_price+sign*self.risk_distance*10});return x

def make(parent:dict[str,Any],next_open:Decimal,atr:Decimal,tick_value:Decimal,step:Decimal,min_lot:Decimal)->Contract:
 if parent.get("decision") not in {"BUY","SELL"}:raise ValueError("INELIGIBLE_PARENT")
 risk=atr*Decimal("1.5");raw=(Decimal(25)/(risk/tick_value));vol=(raw/step).quantize(Decimal(1),rounding=ROUND_DOWN)*step
 if vol<min_lot:vol=None
 sign=Decimal(1) if parent["decision"]=="BUY" else Decimal(-1)
 return Contract(parent["record_hash"],PARENT_SNAPSHOT,PARENT_CONFIG,parent["decision"],parent["candle_timestamp"],"NEXT_M1",next_open,atr,next_open-sign*risk,risk,vol)

def outcome(c:Contract,rr:int,high:Decimal,low:Decimal,close:Decimal,n:int,cost:Decimal=Decimal()) -> dict[str,Any]:
 sign=Decimal(1) if c.decision=="BUY" else Decimal(-1);tp=c.entry_price+sign*c.risk_distance*rr;stop=(low<=c.stop_price if sign>0 else high>=c.stop_price);hit=(high>=tp if sign>0 else low<=tp)
 if stop and hit:return {"outcome":"SL_HIT","ambiguous_intrabar":True,"gross_r":Decimal(-1),"cost_r":cost,"net_r":Decimal(-1)-cost}
 if hit:return {"outcome":"TP_HIT","ambiguous_intrabar":False,"gross_r":Decimal(rr),"cost_r":cost,"net_r":Decimal(rr)-cost}
 if stop:return {"outcome":"SL_HIT","ambiguous_intrabar":False,"gross_r":Decimal(-1),"cost_r":cost,"net_r":Decimal(-1)-cost}
 if n>=60:return {"outcome":"EXPIRED","ambiguous_intrabar":False,"gross_r":sign*(close-c.entry_price)/c.risk_distance,"cost_r":cost,"net_r":sign*(close-c.entry_price)/c.risk_distance-cost}
 return {"outcome":"UNRESOLVED","ambiguous_intrabar":False}

def priced_outcome(c: Contract, rr: int, high: Decimal, low: Decimal, close: Decimal, n: int, profile: str, spread: Decimal | None) -> dict[str, Any]:
    """Apply frozen costs after path resolution; missing spread fails closed for non-ideal profiles."""
    if profile not in {"IDEALIZED", "BASE", "STRESS"}: raise ValueError("INVALID_COST_PROFILE")
    raw = outcome(c, rr, high, low, close, n)
    if raw["outcome"] == "UNRESOLVED": return raw
    if profile == "IDEALIZED": spread_cost = fixed = Decimal()
    elif spread is None: return {"outcome":"UNRESOLVED","cost_unavailable":True}
    else:
        multiplier, fixed = (Decimal(1), Decimal(".05")) if profile == "BASE" else (Decimal("1.5"), Decimal(".15"))
        spread_cost = multiplier * spread / c.risk_distance
    raw.update({"cost_profile":profile,"spread_cost_r":spread_cost,"fixed_cost_r":fixed,"cost_r":spread_cost+fixed,"net_r":raw["gross_r"]-spread_cost-fixed,"simulated_pnl_inr":(raw["gross_r"]-spread_cost-fixed)*Decimal(25)})
    return raw

def volume_for_risk(risk_distance: Decimal, meta: dict[str, Any]) -> Decimal | None:
    """Read-only metadata sizing. None means never representable under the ₹25 cap."""
    try:
        tick_size=Decimal(str(meta["trade_tick_size"]));tick_value=Decimal(str(meta["trade_tick_value"]));minimum=Decimal(str(meta["volume_min"]));step=Decimal(str(meta["volume_step"]));maximum=Decimal(str(meta["volume_max"]))
        raw=Decimal(25)/((risk_distance/tick_size)*tick_value);value=(min(raw,maximum)/step).quantize(Decimal(1),rounding=ROUND_DOWN)*step
        return value if value>=minimum else None
    except (KeyError,ArithmeticError): return None
