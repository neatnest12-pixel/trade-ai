import pandas as pd
import pytest

from app.research.validation import (
 Calibration,
 PaperJournal,
 Prediction,
 conservative_backtest,
 multiple_testing_report,
 probability_buckets,
)


def test_backtest_costs_and_ambiguity():
 x=pd.DataFrame({'timestamp':pd.date_range('2025-01-01',periods=2,tz='UTC'),'open':[1,1],'high':[1.002,1.001],'low':[.998,.999],'close':[1,1]}); r=conservative_backtest(x,{0:'LONG'},spread=.0001,commission=.0001,slippage=.0001); assert r.exit_reason.iloc[0]=='AMBIGUOUS_INTRABAR' and r.total_cost.iloc[0]>.0
def test_calibration_validation_only_and_buckets():
 c=Calibration('platt'); c.fit([.2,.8],[0,1]); assert len(c.predict([.5]))==1; assert len(probability_buckets([.52,.82],[1,1]))==6
 with pytest.raises(ValueError): c.fit([.2],[1],split='final')
def test_journal_immutability_and_no_trade():
 j=PaperJournal(); p=Prediction('p','t','m','EURUSD','M1','NO_TRADE',.5,None,'m','s','f','c','g',()); j.append_prediction(p); j.append_evaluation('p',actual_outcome='NEUTRAL'); assert j.predictions['p']==p and len(j.evaluations)==1
def test_multiple_testing_report(): assert multiple_testing_report([1,-1,2],2,8)['positive_fold_percentage']==pytest.approx(2/3)
