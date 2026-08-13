from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.calibration import CalibratedClassifierCV
from sklearn.frozen import FrozenEstimator
from sklearn.inspection import permutation_importance
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    log_loss,
    precision_score,
    recall_score,
    roc_auc_score,
)

from app.analysis.features import build_features, generate_targets
from app.ml.models import CLASSIFIERS

MODEL_FEATURES = ["return_1", "return_5", "rsi_14", "macd_hist", "atr_pct", "adx_14",
                  "ema_spread", "bb_position", "roc_10", "volume_z"]


def metrics(y: np.ndarray, probability: np.ndarray) -> dict[str, object]:
    prediction=(probability>=.5).astype(int)
    return {"count":len(y),"accuracy":accuracy_score(y,prediction),
      "balanced_accuracy":balanced_accuracy_score(y,prediction),
      "precision":precision_score(y,prediction,zero_division=0),"recall":recall_score(y,prediction,zero_division=0),
      "f1":f1_score(y,prediction,zero_division=0),"roc_auc":roc_auc_score(y,probability) if len(np.unique(y))>1 else None,
      "brier_score":brier_score_loss(y,probability),"log_loss":log_loss(y,probability,labels=[0,1]),
      "confusion_matrix":confusion_matrix(y,prediction,labels=[0,1]).tolist()}


def baselines(frame: pd.DataFrame) -> dict[str, dict[str, object]]:
    y=frame.target_up.astype(int).to_numpy(); previous=(frame.close>frame.open).astype(float).to_numpy()
    trend=(frame.close>frame.ema_50).astype(float).to_numpy(); ema=(frame.ema_20>frame.ema_50).astype(float).to_numpy()
    rng=np.random.default_rng(42)
    candidates={"always_bullish":np.ones(len(y)),"always_bearish":np.zeros(len(y)),
      "previous_candle_direction":previous,"random_50_50":rng.integers(0,2,len(y)).astype(float),
      "trend_continuation":trend,"ema_20_50":ema}
    return {name:metrics(y,p) for name,p in candidates.items()}


def calibration_buckets(y: np.ndarray, probability: np.ndarray) -> list[dict[str, object]]:
    confidence=np.maximum(probability,1-probability); correct=((probability>=.5)==y)
    edges=[.5,.55,.6,.65,.7,.75,.8,1.000001]; labels=["50-55%","55-60%","60-65%","65-70%","70-75%","75-80%","80%+"]
    rows=[]
    for lo,hi,label in zip(edges[:-1],edges[1:],labels,strict=True):
        mask=(confidence>=lo)&(confidence<hi)
        rows.append({"bucket":label,"count":int(mask.sum()),"predicted_average":float(confidence[mask].mean()) if mask.any() else None,
          "actual_success_frequency":float(correct[mask].mean()) if mask.any() else None})
    return rows


def no_trade_thresholds(y: np.ndarray, probability: np.ndarray, returns: np.ndarray,
                        thresholds=(.55,.6,.65,.7,.75)) -> list[dict[str, object]]:
    rows=[]
    for threshold in thresholds:
        confidence=np.maximum(probability,1-probability); mask=confidence>=threshold
        signed=np.where(probability[mask]>=.5,1,-1)*returns[mask]
        wins=signed[signed>0]; losses=signed[signed<0]; curve=np.cumsum(signed); peak=np.maximum.accumulate(np.r_[0,curve])
        drawdown=np.r_[0,curve]-peak
        rows.append({"threshold":threshold,"trades_taken":int(mask.sum()),"trades_skipped":int((~mask).sum()),
          "accuracy":float(((probability[mask]>=.5)==y[mask]).mean()) if mask.any() else None,
          "expectancy_return":float(signed.mean()) if mask.any() else None,
          "profit_factor":float(wins.sum()/abs(losses.sum())) if len(losses) and losses.sum()!=0 else None,
          "maximum_drawdown_return":float(drawdown.min())})
    return rows


def evaluate_models(candles: pd.DataFrame) -> dict[str, object]:
    full=generate_targets(build_features(candles)).dropna(subset=MODEL_FEATURES+["target_up","target_return"]).reset_index(drop=True)
    if len(full)<500: raise ValueError("At least 500 usable observations are required for evaluation")
    train_end=int(len(full)*.6); validation_end=int(len(full)*.8)
    train=full.iloc[:train_end]; validation=full.iloc[train_end:validation_end]; test=full.iloc[validation_end:]
    results={}
    for name,base in CLASSIFIERS.items():
        model=clone(base).fit(train[MODEL_FEATURES],train.target_up.astype(int))
        validation_probability=model.predict_proba(validation[MODEL_FEATURES])[:,1]
        # Calibration is fit on validation predictions/labels; final test remains untouched.
        method="isotonic" if len(validation)>=1000 else "sigmoid"
        calibrated=CalibratedClassifierCV(FrozenEstimator(model),method=method).fit(
            validation[MODEL_FEATURES],validation.target_up.astype(int))
        probability=calibrated.predict_proba(test[MODEL_FEATURES])[:,1]
        importance=permutation_importance(model,validation[MODEL_FEATURES],validation.target_up.astype(int),n_repeats=5,random_state=42)
        results[name]={"validation":metrics(validation.target_up.astype(int).to_numpy(),validation_probability),
          "test":metrics(test.target_up.astype(int).to_numpy(),probability),"calibration_method":method,
          "calibration":calibration_buckets(test.target_up.astype(int).to_numpy(),probability),
          "feature_importance":dict(zip(MODEL_FEATURES,importance.importances_mean.tolist(),strict=True)),
          "no_trade_thresholds":no_trade_thresholds(test.target_up.astype(int).to_numpy(),probability,test.target_return.to_numpy())}
    periods={name:{"start":part.timestamp.iloc[0].isoformat(),"end":part.timestamp.iloc[-1].isoformat(),"rows":len(part)}
             for name,part in (("train",train),("validation",validation),("test",test))}
    return {"periods":periods,"baselines":baselines(test),"models":results}


def monte_carlo(r_multiples: list[float], risk_fraction: float, simulations: int=5000, seed: int=42) -> dict[str, float]:
    if not r_multiples: raise ValueError("Historical trade outcomes are required")
    rng=np.random.default_rng(seed); outcomes=np.asarray(r_multiples); max_dd=[]; ending=[]; streaks=[]
    for _ in range(simulations):
        sample=rng.choice(outcomes,size=len(outcomes),replace=True); equity=np.cumprod(1+risk_fraction*sample)
        peak=np.maximum.accumulate(np.r_[1,equity]); dd=(np.r_[1,equity]-peak)/peak; max_dd.append(dd.min());ending.append(equity[-1])
        streak=best=0
        for loss in sample<0: streak=streak+1 if loss else 0;best=max(best,streak)
        streaks.append(best)
    a=np.asarray(max_dd);e=np.asarray(ending)
    return {"simulations":simulations,"median_max_drawdown":float(np.median(a)),"p95_max_drawdown":float(np.quantile(a,.05)),
      "median_ending_multiple":float(np.median(e)),"median_largest_losing_streak":float(np.median(streaks)),
      "risk_losing_10_percent":float((e<=.9).mean()),"risk_losing_20_percent":float((e<=.8).mean()),
      "risk_losing_50_percent":float((e<=.5).mean()),"notice":"Bootstrap estimate, not a guarantee."}
