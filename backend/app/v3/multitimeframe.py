from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

FEATURE_POLICY_VERSION="v3-mtf-context-r1"
FEATURE_POLICY_HASH=hashlib.sha256(FEATURE_POLICY_VERSION.encode()).hexdigest()
TIMEFRAMES={"M1":1,"M5":5,"M15":15,"H1":60,"H6":360,"H12":720}

@dataclass(frozen=True)
class CompletedCandle:
    timeframe: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    provenance: str

def completed_before(candles:list[CompletedCandle], decision:datetime, timeframe:str)->CompletedCandle|None:
    decision=decision.astimezone(UTC)
    eligible=[c for c in candles if c.timeframe==timeframe and c.timestamp+timedelta(minutes=TIMEFRAMES[timeframe])<=decision]
    return max(eligible,key=lambda c:c.timestamp) if eligible else None

def alignment(states:dict[str,str])->dict[str,object]:
    bullish=sum(v=="BULLISH" for v in states.values()); bearish=sum(v=="BEARISH" for v in states.values()); neutral=sum(v=="NEUTRAL" for v in states.values())
    pairs={}
    names=list(states)
    for i,a in enumerate(names):
        for b in names[i+1:]: pairs[f"{a}_{b}_ALIGNMENT"]=states[a]==states[b]
    return {**pairs,"BULLISH_TIMEFRAME_COUNT":bullish,"BEARISH_TIMEFRAME_COUNT":bearish,"NEUTRAL_TIMEFRAME_COUNT":neutral,"GLOBAL_ALIGNMENT_SCORE":abs(bullish-bearish)}

def hierarchical(states:dict[str,str])->dict[str,str]:
    def combine(a,b):
        return a if a==b else "MIXED"
    return {"MICRO_DIRECTION":combine(states.get("M1","UNAVAILABLE"),states.get("M5","UNAVAILABLE")),"INTRADAY_DIRECTION":combine(states.get("M15","UNAVAILABLE"),states.get("H1","UNAVAILABLE")),"HIGHER_TIMEFRAME_DIRECTION":combine(states.get("H6","UNAVAILABLE"),states.get("H12","UNAVAILABLE"))}

def policy_manifest()->dict[str,object]:
    return {"feature_policy_version":FEATURE_POLICY_VERSION,"feature_policy_hash":FEATURE_POLICY_HASH,"timeframes":TIMEFRAMES,"completed_candles_only":True,"provenance":{k:"DIRECT_MT5" for k in TIMEFRAMES}}
