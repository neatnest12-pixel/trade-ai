import pandas as pd

from app.analysis.features import build_features, generate_targets
from app.analysis.indicators import add_indicators
from app.analysis.patterns import detect_patterns
from app.analysis.structure import add_structure
from app.market_data.adapters import identify_gaps, validate_candles
from app.ml.models import chronological_split


def test_indicators(candles):
    x=add_indicators(candles); assert {"ema_200","rsi_14","macd","atr_14","adx_14","bb_upper"}<=set(x)
    assert x.rsi_14.dropna().between(0,100).all()
def test_patterns_deterministic(candles):
    x=candles.iloc[:3].copy();x.loc[0,["open","close"]]=[1.1,1.09];x.loc[1,["open","close","high","low"]]=[1.089,1.101,1.102,1.088]
    assert bool(detect_patterns(x).loc[1,"bullish_engulfing"])
def test_structure_no_future_edge_label(candles):
    x=add_structure(candles);assert not x.swing_high.iloc[:2].any();assert not x.swing_low.iloc[-2:].any()
def test_target_alignment_and_last_unknown(candles):
    x=generate_targets(candles);assert x.loc[0,"target_high"]==candles.loc[1,"high"];assert pd.isna(x.iloc[-1].target_up)
def test_features_exclude_targets(candles):
    assert not any(c.startswith("target_") for c in build_features(candles).columns)
def test_chronological_split():
    a,b,c=chronological_split(100);assert (a.stop,b.start,b.stop,c.start)==(60,60,80,80)
def test_validation_and_gap(candles):
    x=validate_candles(candles);assert not identify_gaps(x,"5m")
    broken=x.drop(index=10).reset_index(drop=True);assert identify_gaps(broken,"5m")[0]["missing_intervals"]==1

