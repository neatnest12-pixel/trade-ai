"""Synthetic-fixture validation infrastructure; no data acquisition or model fitting on import."""
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class TradeRecord:
 signal_timestamp: str; entry_timestamp: str; entry_price: float; direction: str; spread: float; commission: float; slippage: float; stop_price: float; target_price: float; exit_timestamp: str; exit_price: float; exit_reason: str; gross_pnl: float; total_cost: float; net_pnl: float

def conservative_backtest(candles, signals, spread=0., commission=0., slippage=0., policy='stop_first'):
 records=[]; open_trade=None
 for i,row in candles.iterrows():
  if open_trade is None and i in signals:
   s=signals[i]; open_trade={'signal':row.timestamp,'entry':row.timestamp,'price':row.open,'direction':s,'stop':row.open-.001 if s=='LONG' else row.open+.001,'target':row.open+.001 if s=='LONG' else row.open-.001}
   continue
  if open_trade is None: continue
  hit_stop=(row.low<=open_trade['stop']) if open_trade['direction']=='LONG' else (row.high>=open_trade['stop'])
  hit_target=(row.high>=open_trade['target']) if open_trade['direction']=='LONG' else (row.low<=open_trade['target'])
  if not (hit_stop or hit_target): continue
  reason='AMBIGUOUS_INTRABAR' if hit_stop and hit_target else 'STOP' if hit_stop else 'TARGET'; exit_price=open_trade['stop'] if (reason in ('STOP','AMBIGUOUS_INTRABAR') and policy=='stop_first') else open_trade['target']
  gross=(exit_price-open_trade['price'])*(1 if open_trade['direction']=='LONG' else -1); cost=spread+commission+slippage
  records.append(asdict(TradeRecord(str(open_trade['signal']),str(open_trade['entry']),open_trade['price'],open_trade['direction'],spread,commission,slippage,open_trade['stop'],open_trade['target'],str(row.timestamp),exit_price,reason,gross,cost,gross-cost))); open_trade=None
 return pd.DataFrame(records)

class Calibration:
 def __init__(self, method='platt'): self.method=method; self.fitted=False; self._model=None
 def fit(self, probabilities, outcomes, split='validation'):
  if split!='validation': raise ValueError('calibration must use validation data only')
  from sklearn.isotonic import IsotonicRegression
  from sklearn.linear_model import LogisticRegression
  self._model=IsotonicRegression(out_of_bounds='clip').fit(probabilities,outcomes) if self.method=='isotonic' else LogisticRegression().fit(np.asarray(probabilities).reshape(-1,1),outcomes); self.fitted=True; return self
 def predict(self, probabilities):
  if not self.fitted: raise RuntimeError('calibrator is unfitted')
  return self._model.predict(np.asarray(probabilities) if self.method=='isotonic' else np.asarray(probabilities).reshape(-1,1))
 def save(self,path): Path(path).write_text(json.dumps({'method':self.method,'fitted':self.fitted}),encoding='utf-8')

BUCKETS=((.5,.55),(.55,.60),(.60,.65),(.65,.70),(.70,.80),(.80,1.01))
def probability_buckets(probabilities, outcomes):
 p=np.asarray(probabilities); y=np.asarray(outcomes); return [{'bucket':f'{lo:.0%}-{min(hi,1):.0%}','sample_count':int(((p>=lo)&(p<hi)).sum()),'mean_probability':float(p[(p>=lo)&(p<hi)].mean()) if ((p>=lo)&(p<hi)).any() else None,'observed_frequency':float(y[(p>=lo)&(p<hi)].mean()) if ((p>=lo)&(p<hi)).any() else None} for lo,hi in BUCKETS]

@dataclass(frozen=True)
class Prediction:
 prediction_id:str; created_at:str; market_timestamp:str; symbol:str; timeframe:str; decision:str; raw_probability:float; calibrated_probability:float|None; model_version:str; strategy_version:str; feature_version:str; context_version:str; git_commit:str; reason_codes:tuple[str,...]

class PaperJournal:
 def __init__(self): self.predictions={}; self.evaluations=[]
 def append_prediction(self,p):
  if p.prediction_id in self.predictions: raise ValueError('duplicate prediction')
  self.predictions[p.prediction_id]=p
 def append_evaluation(self,prediction_id, **fields):
  if prediction_id not in self.predictions: raise KeyError(prediction_id)
  self.evaluations.append({'prediction_id':prediction_id,'evaluation_timestamp':datetime.now(UTC).isoformat(),**fields})

def multiple_testing_report(fold_values, candidate_count, configuration_count, parameter_stability=None):
 x=np.asarray(fold_values,dtype=float); return {'candidate_count':candidate_count,'configuration_count':configuration_count,'mean_fold_performance':float(x.mean()) if len(x) else None,'median_fold_performance':float(np.median(x)) if len(x) else None,'std_fold_performance':float(x.std()) if len(x) else None,'worst_fold':float(x.min()) if len(x) else None,'best_fold':float(x.max()) if len(x) else None,'positive_fold_percentage':float((x>0).mean()) if len(x) else None,'performance_dispersion':float(x.std()) if len(x) else None,'parameter_stability':parameter_stability,'warnings':['tiny-sample'] if len(x)<3 else []}
