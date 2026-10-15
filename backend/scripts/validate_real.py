"""First real-data validation using certified datasets only."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.calibration import CalibratedClassifierCV
from sklearn.frozen import FrozenEstimator

from app.analysis.features import build_features, generate_targets
from app.ml.models import CLASSIFIERS
from app.research.evaluation import MODEL_FEATURES, calibration_buckets, metrics, monte_carlo

POINT=0.00001
SLIPPAGE_PRICE=0.00002
COMMISSION_PRICE=0.0
THRESHOLDS=(.55,.60,.65,.70,.75)


def trade_statistics(r_values: np.ndarray) -> dict[str, object]:
    wins=r_values[r_values>0];losses=r_values[r_values<0]
    curve=np.cumsum(r_values);peak=np.maximum.accumulate(np.r_[0,curve]);drawdown=np.r_[0,curve]-peak
    downside=r_values[r_values<0];streak=largest=0
    for loss in r_values<0: streak=streak+1 if loss else 0;largest=max(largest,streak)
    return {"trades":len(r_values),"win_rate":float((r_values>0).mean()) if len(r_values) else None,
      "average_win_r":float(wins.mean()) if len(wins) else None,"average_loss_r":float(losses.mean()) if len(losses) else None,
      "expectancy_r":float(r_values.mean()) if len(r_values) else None,
      "profit_factor":float(wins.sum()/abs(losses.sum())) if len(losses) and losses.sum()!=0 else None,
      "maximum_drawdown_r":float(drawdown.min()),"sharpe":float(r_values.mean()/r_values.std(ddof=1)*np.sqrt(252)) if len(r_values)>1 and r_values.std(ddof=1)>0 else None,
      "sortino":float(r_values.mean()/downside.std(ddof=1)*np.sqrt(252)) if len(downside)>1 and downside.std(ddof=1)>0 else None,
      "largest_losing_streak":largest}


def r_outcomes(frame: pd.DataFrame, probability: np.ndarray, threshold: float) -> np.ndarray:
    confidence=np.maximum(probability,1-probability);mask=confidence>=threshold
    side=np.where(probability[mask]>=.5,1,-1)
    gross=side*(frame.loc[mask,"target_close"].to_numpy()-frame.loc[mask,"target_open"].to_numpy())
    spread=frame.loc[mask,"target_spread"].fillna(frame.spread.median()).to_numpy()*POINT
    net=gross-spread-SLIPPAGE_PRICE-COMMISSION_PRICE
    risk=(1.5*frame.loc[mask,"atr_14"].to_numpy())
    return net/risk


def account_simulation(r_values: np.ndarray) -> dict[str, object]:
    result={}
    for fraction in (.0025,.005,.01):
        equity=2500*np.cumprod(1+fraction*r_values);series=np.r_[2500,equity]
        peak=np.maximum.accumulate(series);drawdown=(series-peak)/peak
        mc=monte_carlo(r_values.tolist(),fraction,simulations=2000,seed=42)
        result[f"{fraction*100:.2f}%"]={"planned_loss_inr":2500*fraction,
          "ending_balance":float(series[-1]),"maximum_drawdown":float(drawdown.min()),
          "risk_of_ruin_approximation":mc["risk_losing_50_percent"],"monte_carlo":mc}
    return result


def run(manifest_path: Path) -> dict[str, object]:
    manifest=json.loads(manifest_path.read_text())
    certified=[(tf,item) for tf,item in manifest["datasets"].items() if item.get("certified")]
    if not certified: raise RuntimeError("No certified dataset available")
    all_results={}
    for timeframe,item in certified:
        path=manifest_path.parent/"datasets"/item["version"]/"data.csv"
        candles=pd.read_csv(path);candles["timestamp"]=pd.to_datetime(candles.timestamp,utc=True)
        featured=build_features(candles);featured["target_open"]=featured.open.shift(-1)
        featured["target_close"]=featured.close.shift(-1);featured["target_spread"]=featured.spread.shift(-1)
        full=generate_targets(featured).dropna(subset=MODEL_FEATURES+["target_up","target_return","target_open","target_close"]).reset_index(drop=True)
        train_end=int(len(full)*.6);validation_end=int(len(full)*.8);calibration_end=train_end+(validation_end-train_end)//2
        train=full.iloc[:train_end];calibration=full.iloc[train_end:calibration_end]
        selection=full.iloc[calibration_end:validation_end];test=full.iloc[validation_end:]
        validation_results={};fitted={}
        for name,base in CLASSIFIERS.items():
            model=clone(base).fit(train[MODEL_FEATURES],train.target_up.astype(int))
            method="isotonic" if len(calibration)>=1000 else "sigmoid"
            calibrated=CalibratedClassifierCV(FrozenEstimator(model),method=method).fit(
                calibration[MODEL_FEATURES],calibration.target_up.astype(int))
            probability=calibrated.predict_proba(selection[MODEL_FEATURES])[:,1]
            validation_results[name]={**metrics(selection.target_up.astype(int).to_numpy(),probability),
                                      "calibration_method":method}
            fitted[name]=(calibrated,probability)
        selected_name=min(validation_results,key=lambda name:validation_results[name]["brier_score"])
        selected_model,selection_probability=fitted[selected_name]
        threshold_rows=[]
        for threshold in THRESHOLDS:
            outcomes=r_outcomes(selection,selection_probability,threshold)
            threshold_rows.append({"threshold":threshold,**trade_statistics(outcomes),
              "no_trade_rate":float((np.maximum(selection_probability,1-selection_probability)<threshold).mean())})
        eligible=[row for row in threshold_rows if row["trades"]>=100]
        selected_threshold=max(eligible,key=lambda row:row["expectancy_r"])["threshold"] if eligible else .75
        # The following is the single primary untouched-test probability evaluation.
        test_probability=selected_model.predict_proba(test[MODEL_FEATURES])[:,1]
        final_metrics=metrics(test.target_up.astype(int).to_numpy(),test_probability)
        baselines={"always_up":metrics(test.target_up.astype(int).to_numpy(),np.ones(len(test))),
          "always_down":metrics(test.target_up.astype(int).to_numpy(),np.zeros(len(test))),
          "previous_continuation":metrics(test.target_up.astype(int).to_numpy(),(test.close>test.open).astype(float).to_numpy()),
          "previous_reversal":metrics(test.target_up.astype(int).to_numpy(),(test.close<=test.open).astype(float).to_numpy()),
          "ema_trend":metrics(test.target_up.astype(int).to_numpy(),(test.ema_20>test.ema_50).astype(float).to_numpy()),
          "random_seed_42":metrics(test.target_up.astype(int).to_numpy(),np.random.default_rng(42).integers(0,2,len(test)).astype(float))}
        outcomes=r_outcomes(test,test_probability,selected_threshold);trades=trade_statistics(outcomes)
        experiment={"dataset_version":item["version"],"dataset_sha256":item["sha256"],
          "features":MODEL_FEATURES,"split":{"train":{"start":train.timestamp.iloc[0].isoformat(),"end":train.timestamp.iloc[-1].isoformat(),"rows":len(train)},
          "calibration":{"start":calibration.timestamp.iloc[0].isoformat(),"end":calibration.timestamp.iloc[-1].isoformat(),"rows":len(calibration)},
          "validation_selection":{"start":selection.timestamp.iloc[0].isoformat(),"end":selection.timestamp.iloc[-1].isoformat(),"rows":len(selection)},
          "final_test":{"start":test.timestamp.iloc[0].isoformat(),"end":test.timestamp.iloc[-1].isoformat(),"rows":len(test)}},
          "validation_models":validation_results,"threshold_selection":threshold_rows,
          "selected_model":selected_name,"selected_threshold":selected_threshold,"final_test":final_metrics,
          "calibration_buckets":calibration_buckets(test.target_up.astype(int).to_numpy(),test_probability),
          "baselines":baselines,"trading_after_costs":trades,"no_trade_rate":float((np.maximum(test_probability,1-test_probability)<selected_threshold).mean()),
          "costs":{"spread":"actual next-candle MT5 spread points","slippage_price":SLIPPAGE_PRICE,"commission_price":COMMISSION_PRICE},
          "account_simulation":account_simulation(outcomes)}
        canonical=json.dumps(experiment,sort_keys=True,separators=(",",":"));experiment["experiment_sha256"]=hashlib.sha256(canonical.encode()).hexdigest()
        all_results[timeframe]=experiment
    output={"manifest_sha256":hashlib.sha256(manifest_path.read_bytes()).hexdigest(),"results":all_results}
    (manifest_path.parent/"REAL_VALIDATION_RESULTS.json").write_text(json.dumps(output,indent=2),encoding="utf-8")
    return output


if __name__=="__main__": print(json.dumps(run(Path("certified_data/DATASET_MANIFEST.json")),indent=2))
