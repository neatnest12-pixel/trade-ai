import pandas as pd

from app.research.framework import (
    Experiment,
    aggregate_votes,
    baseline,
    causal_regime,
    cost_scenarios,
    model_registry,
    position_size,
    walk_forward,
)


def test_baselines_deterministic_and_costs():
    x=[1,2,1,2]; assert baseline('RANDOM',x,7).equals(baseline('RANDOM',x,7)); assert cost_scenarios(1,2,3)['STRESS']==9
def test_walk_forward_and_regime_causal():
    folds=walk_forward(pd.DataFrame({'x':range(30)}),10,5,purge=2,embargo_bars=1); assert folds[0]['validation']==(12,17); assert causal_regime(pd.Series([1]*25))[-1]['trend']=='RANGING'
def test_model_adapters_declared_but_unfitted():
    r=model_registry(3); assert set(r)=={'logistic_regression','random_forest','gradient_boosting'}; assert r['logistic_regression'].model_metadata['training_timestamp'] is None
def test_sizing_and_ensemble():
    assert position_size(2500,.005,.001,100000,.01,.01)>0
    assert aggregate_votes([])['decision']=='NO_TRADE'
def test_audit_digest():
    e=Experiment('e','d','h','f','t','s','m',{},1,'c','a','b','c'); assert len(e.digest())==64
