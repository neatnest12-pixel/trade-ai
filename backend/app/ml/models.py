from dataclasses import dataclass

import pandas as pd
from sklearn.base import clone
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import (
    GradientBoostingClassifier,
    GradientBoostingRegressor,
    RandomForestClassifier,
    RandomForestRegressor,
)
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

CLASSIFIERS={"logistic":make_pipeline(StandardScaler(),LogisticRegression(max_iter=1000)),
 "random_forest":RandomForestClassifier(n_estimators=200,max_depth=6,random_state=42),
 "gradient_boosting":GradientBoostingClassifier(random_state=42)}
REGRESSORS={"random_forest":RandomForestRegressor(n_estimators=200,max_depth=8,random_state=42),
 "gradient_boosting":GradientBoostingRegressor(random_state=42)}

def chronological_split(n:int, train=.6, validation=.2)->tuple[slice,slice,slice]:
    a=int(n*train); b=int(n*(train+validation)); return slice(0,a),slice(a,b),slice(b,n)

@dataclass
class FoldMetric:
    train_end:int; test_end:int; brier:float; log_loss:float

def walk_forward(X:pd.DataFrame,y:pd.Series,min_train:int=200,step:int=50)->list[FoldMetric]:
    folds=[]
    for end in range(min_train,len(X),step):
        test_end=min(end+step,len(X)); model=clone(CLASSIFIERS["logistic"])
        model.fit(X.iloc[:end],y.iloc[:end]); p=model.predict_proba(X.iloc[end:test_end])[:,1]
        folds.append(FoldMetric(end,test_end,brier_score_loss(y.iloc[end:test_end],p),log_loss(y.iloc[end:test_end],p,labels=[0,1])))
    return folds

def calibrated_classifier(X_train,y_train):
    model=CalibratedClassifierCV(clone(CLASSIFIERS["logistic"]),method="sigmoid",cv=3)
    return model.fit(X_train,y_train)

