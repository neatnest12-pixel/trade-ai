"""Deterministic Campaign #4 research infrastructure; never trains or trades on import."""
import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Signal:
    timestamp: pd.Timestamp; strategy_id: str; family: str; action: str
    signal_strength: float = 0.0; reason_codes: tuple[str,...] = (); parameters: dict = field(default_factory=dict)
    feature_provenance: tuple[str,...] = (); warmup_requirement: int = 0

class StrategyRegistry:
    families=('trend','momentum','mean_reversion','breakout','price_action','candlestick','volatility','levels','session','statistical','SMC_approximation')
    def __init__(self): self._items={}
    def register(self, strategy_id, family, fn):
        if family not in self.families: raise ValueError('unsupported family')
        self._items[strategy_id]=(family,fn)
    def signal(self, strategy_id, timestamp, **kwargs):
        family,fn=self._items[strategy_id]; action,strength,reasons=fn(**kwargs)
        return Signal(pd.Timestamp(timestamp,tz='UTC'),strategy_id,family,action,strength,tuple(reasons),kwargs.get('parameters',{}),tuple(kwargs.get('feature_provenance',())),kwargs.get('warmup_requirement',0))

def baseline(name, close, seed=0):
    c=pd.Series(close); rng=np.random.default_rng(seed); out=[]
    for i in range(len(c)):
        if name=='ALWAYS_LONG': a='LONG'
        elif name=='ALWAYS_SHORT': a='SHORT'
        elif name=='PREVIOUS_CANDLE_DIRECTION': a='LONG' if i==0 or c.iloc[i]>=c.iloc[i-1] else 'SHORT'
        elif name=='SIMPLE_MOMENTUM': a='LONG' if i<3 or c.iloc[i]>=c.iloc[i-3] else 'SHORT'
        elif name=='SIMPLE_MEAN_REVERSION': a='SHORT' if i>=5 and c.iloc[i]>c.iloc[max(0,i-5):i].mean() else 'LONG'
        elif name=='SIMPLE_MA_TREND': a='LONG' if i<10 or c.iloc[i]>=c.iloc[max(0,i-10):i].mean() else 'SHORT'
        elif name=='RANDOM': a='LONG' if rng.integers(0,2)==1 else 'SHORT'
        else: raise ValueError(name)
        out.append(a)
    return pd.Series(out,index=c.index)

class MLAdapter:
    def __init__(self, estimator, model_version, feature_version='unknown', seed=0): self.estimator=estimator; self.metadata={'model_version':model_version,'feature_version':feature_version,'seed':seed,'training_timestamp':None}
    def fit(self, X, y, dataset_id, dataset_hash, parameters=None): self.estimator.fit(X,y); self.metadata.update(dataset_id=dataset_id,dataset_hash=dataset_hash,parameters=parameters or {},training_timestamp=datetime.now(UTC).isoformat()); return self
    def predict(self,X): return self.estimator.predict(X)
    def predict_proba(self,X): return self.estimator.predict_proba(X) if hasattr(self.estimator,'predict_proba') else None
    def save(self,path): Path(path).write_text(json.dumps(self.metadata),encoding='utf-8')
    @property
    def model_metadata(self): return dict(self.metadata)

def model_registry(seed=0):
 from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
 from sklearn.linear_model import LogisticRegression
 return {'logistic_regression':MLAdapter(LogisticRegression(random_state=seed,max_iter=1000),'logistic-regression-v1',seed=seed),'random_forest':MLAdapter(RandomForestClassifier(random_state=seed,n_estimators=50),'random-forest-v1',seed=seed),'gradient_boosting':MLAdapter(GradientBoostingClassifier(random_state=seed),'gradient-boosting-v1',seed=seed)}

def causal_regime(close, spread=None, timestamp=None):
 c=pd.Series(close); ret=c.pct_change(); vol=ret.rolling(20,min_periods=20).std(); trend=c.pct_change(20)
 out=[]
 for i in range(len(c)):
  v=vol.iloc[i]; tr=trend.iloc[i]; session='UNKNOWN'
  if timestamp is not None:
   h=pd.Timestamp(timestamp.iloc[i]).hour; session='ASIAN' if h<8 else 'LONDON' if h<13 else 'NEW_YORK' if h<17 else 'OVERLAP' if h<21 else 'ASIAN'
  out.append({'trend':'TRENDING' if pd.notna(tr) and abs(tr)>.002 else 'RANGING','volatility':'HIGH_VOL' if pd.notna(v) and v>.001 else 'LOW_VOL' if pd.notna(v) and v<.0003 else 'NORMAL_VOL','session':session,'spread':'HIGH_SPREAD' if spread is not None and spread.iloc[i]>spread.median() else 'NORMAL_SPREAD'})
 return out

def walk_forward(frame, train_bars, validation_bars, step=None, purge=0, embargo_bars=0, expanding=True):
 step=step or validation_bars; n=len(frame); folds=[]; start=0
 while start+train_bars+purge+validation_bars<=n:
  tr_start=0 if expanding else start; tr_end=start+train_bars; val_start=tr_end+purge; val_end=val_start+validation_bars
  folds.append({'train':(tr_start,tr_end),'validation':(val_start,val_end),'purge':purge,'embargo':embargo_bars}); start+=step
 return folds

def aggregate_votes(signals, weights=None, threshold=.6):
 weights=weights or {}; scores={'LONG':0.,'SHORT':0.}; families=set()
 for s in signals:
  w=weights.get(s.family,1.); scores[s.action]=scores.get(s.action,0)+w*s.signal_strength; families.add(s.family)
 total=sum(scores.values()); p=(scores['LONG']/total) if total else .5; action='BUY' if p>=threshold else 'SELL' if p<=1-threshold else 'NO_TRADE'
 return {'decision':action,'raw_probability':p,'participating_families':sorted(families),'agreement':len({s.action for s in signals})<=1}

def cost_scenarios(spread, commission, slippage): return {k:(spread+commission+slippage)*m for k,m in [('IDEALIZED',0),('BASE',1),('STRESS',1.5),('SEVERE',2)]}

def position_size(balance, risk_pct, stop_distance, contract_size, min_lot, lot_step, conversion=1.):
 if stop_distance<=0: raise ValueError('stop distance must be positive')
 raw=balance*risk_pct/(stop_distance*contract_size*conversion); return max(min_lot,round(raw/lot_step)*lot_step)

@dataclass(frozen=True)
class Experiment:
 experiment_id:str; dataset_id:str; dataset_sha256:str; feature_version:str; target_version:str; strategy_version:str; model_version:str; parameters:dict; random_seed:int; cost_model_version:str; train_range:str; validation_range:str; test_range:str
 def digest(self): return hashlib.sha256(json.dumps(asdict(self),sort_keys=True).encode()).hexdigest()
