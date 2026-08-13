import numpy as np
from sklearn.metrics import (
    accuracy_score,
    brier_score_loss,
    f1_score,
    log_loss,
    precision_score,
    recall_score,
    roc_auc_score,
)


def prediction_metrics(actual:list[int],probabilities:list[float])->dict[str,float|None]:
    if not actual:return {"number_of_predictions":0}
    y=np.array(actual); p=np.array(probabilities); pred=(p>=.5).astype(int)
    return {"number_of_predictions":len(y),"directional_accuracy":accuracy_score(y,pred),
      "precision":precision_score(y,pred,zero_division=0),"recall":recall_score(y,pred,zero_division=0),
      "f1":f1_score(y,pred,zero_division=0),"roc_auc":roc_auc_score(y,p) if len(np.unique(y))>1 else None,
      "brier_score":brier_score_loss(y,p),"log_loss":log_loss(y,p,labels=[0,1])}
