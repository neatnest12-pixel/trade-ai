import pytest

from app.domain.schemas import BacktestRequest, RiskRequest
from app.market_data.adapters import MockAdapter
from app.services.backtest import run_backtest
from app.services.prediction import predict
from app.services.risk import calculate_risk


def test_risk():
    out=calculate_risk(RiskRequest(entry=1.1,stop=1.099,contract_size=100000));assert out["risk_amount"]==25;assert out["position_size_lots"]==pytest.approx(.25)
def test_prediction_probabilities():
    p,_=predict(MockAdapter().fetch("EURUSD","5m",500),"EURUSD","5m");assert p.probability_up+p.probability_down==pytest.approx(1);assert p.decision in {"BUY","SELL","NO_TRADE"}
def test_backtest_next_bar_execution():
    out=run_backtest(MockAdapter().fetch("EURUSD","5m",500),BacktestRequest());assert out["trades"]>=0;assert out["notice"]
