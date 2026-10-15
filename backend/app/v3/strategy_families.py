from __future__ import annotations

import hashlib
from dataclasses import dataclass

STRATEGY_POLICY_VERSION="v3-strategy-family-r1"
STRATEGY_POLICY_HASH=hashlib.sha256(STRATEGY_POLICY_VERSION.encode()).hexdigest()

@dataclass(frozen=True)
class FamilyResult:
    family:str; setup:bool; side:str; reason_codes:tuple[str,...]; conditions_passed:tuple[str,...]; conditions_failed:tuple[str,...]; feature_policy_version:str= "v3-mtf-context-r1"; strategy_policy_version:str=STRATEGY_POLICY_VERSION; strategy_policy_hash:str=STRATEGY_POLICY_HASH

def _fail(f, reasons, failed): return FamilyResult(f,False,"NONE",tuple(reasons),(),tuple(failed))
def evaluate(features:dict, family:str)->FamilyResult:
    try:
        states=features["states"]; vol=float(features["normalized_atr"]); close=float(features["close"]); high=float(features["prior_high"]); low=float(features["prior_low"])
        bullish=states.get("H1")==states.get("H6")==states.get("H12")=="BULLISH"; bearish=states.get("H1")==states.get("H6")==states.get("H12")=="BEARISH"; valid=0.00005<=vol<=0.004
        if family=="TREND":
            if bullish and states.get("M1") in ("BULLISH","NEUTRAL") and valid:return FamilyResult(family,True,"BUY",("HTF_ALIGNMENT",), ("BULLISH_HTF","LOWER_SUPPORT","VOLATILITY_VALID"),())
            if bearish and states.get("M1") in ("BEARISH","NEUTRAL") and valid:return FamilyResult(family,True,"SELL",("HTF_ALIGNMENT",), ("BEARISH_HTF","LOWER_SUPPORT","VOLATILITY_VALID"),())
            return _fail(family,("TREND_ALIGNMENT_OR_VOLATILITY",),("alignment", "volatility"))
        if family=="BREAKOUT":
            if close>high and valid:return FamilyResult(family,True,"BUY",("RESISTANCE_BREAK",),("BREAKOUT","VOLATILITY_VALID"),())
            if close<low and valid:return FamilyResult(family,True,"SELL",("SUPPORT_BREAK",),("BREAKOUT","VOLATILITY_VALID"),())
            return _fail(family,("NO_PRIOR_BOUNDARY_BREAK",),("breakout",))
        if family=="PULLBACK":
            if bullish and features.get("m1_return",0)<0 and features.get("continuation",False):return FamilyResult(family,True,"BUY",("RETRACE_CONTINUATION",),("TREND","RETRACE","CONTINUATION"),())
            if bearish and features.get("m1_return",0)>0 and features.get("continuation",False):return FamilyResult(family,True,"SELL",("RETRACE_CONTINUATION",),("TREND","RETRACE","CONTINUATION"),())
            return _fail(family,("NO_TREND_RETRACE_CONTINUATION",),("trend", "retrace", "continuation"))
        if family=="MEAN_REVERSION":
            deviation=abs(float(features.get("deviation",0))); range_state=features.get("regime")=="RANGE"
            if range_state and deviation>=2*vol and features.get("m1_return",0)<0:return FamilyResult(family,True,"BUY",("RANGE_STRETCH",),("RANGE","DEVIATION","VOLATILITY"),())
            if range_state and deviation>=2*vol and features.get("m1_return",0)>0:return FamilyResult(family,True,"SELL",("RANGE_STRETCH",),("RANGE","DEVIATION","VOLATILITY"),())
            return _fail(family,("NO_RANGE_STRETCH",),("range", "deviation"))
        if family=="VOLATILITY":
            expansion=features.get("range_expansion",False)
            if expansion and bullish:return FamilyResult(family,True,"BUY",("VOLATILITY_EXPANSION",),("EXPANSION","DIRECTION"),())
            if expansion and bearish:return FamilyResult(family,True,"SELL",("VOLATILITY_EXPANSION",),("EXPANSION","DIRECTION"),())
            return _fail(family,("NO_DIRECTIONAL_EXPANSION",),("expansion", "direction"))
        raise ValueError("unknown family")
    except (KeyError,TypeError,ValueError): return _fail(family,("INVALID_OR_MISSING_FEATURE",),("required_features",))

def evaluate_all(features): return {f:evaluate(features,f) for f in ("TREND","BREAKOUT","PULLBACK","MEAN_REVERSION","VOLATILITY")}
