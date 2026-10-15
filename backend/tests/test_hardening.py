from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.analysis.features import build_features
from app.analysis.liquidity import add_liquidity
from app.analysis.structure import add_structure
from app.db.models import Prediction
from app.db.session import Base
from app.market_data.datasets import DatasetStore, data_quality
from app.research.evaluation import calibration_buckets, monte_carlo, no_trade_thresholds
from app.services.scheduler import next_close


def test_all_features_are_prefix_invariant(candles):
    short=build_features(candles.iloc[:250]).iloc[-1]
    long=build_features(candles).iloc[249]
    for column in short.index:
        if column in {"bullish_order_block_price","bearish_order_block_price"}: continue
        a,b=short[column],long[column]
        if pd.isna(a) and pd.isna(b):
            continue
        if isinstance(a, float | np.floating):
            assert a==pytest.approx(b)
        else:
            assert a==b


def test_swing_is_exposed_on_confirmation_bar():
    frame=pd.DataFrame({"high":[1,2,5,2,1],"low":[0,-1,-2,-1,0],"open":[1]*5,"close":[1]*5})
    result=add_structure(frame,window=2)
    assert not result.swing_high.iloc[2]
    assert result.swing_high.iloc[4]
    assert result.confirmed_swing_high_price.iloc[4]==5


def test_order_block_does_not_change_with_future_bar(candles):
    a=add_liquidity(build_features(candles.iloc[:200])).iloc[-1]
    changed=candles.copy();changed.loc[200:,"close"]*=2
    b=add_liquidity(build_features(changed.iloc[:201])).iloc[199]
    assert a.bullish_order_block_candidate==b.bullish_order_block_candidate


def test_dataset_versions_are_content_addressed(tmp_path,candles):
    store=DatasetStore(tmp_path);a=store.persist(candles,symbol="EURUSD",timeframe="5m",source="fixture")
    b=store.persist(candles,symbol="EURUSD",timeframe="5m",source="fixture")
    assert a.version==b.version and len(a.sha256)==64


def test_quality_detects_duplicate_and_incomplete(candles):
    duplicate=pd.concat([candles,candles.iloc[[-1]]],ignore_index=True)
    report=data_quality(duplicate,"5m")
    assert report["duplicate_timestamps"]==1
    assert not report["accepted"]


def test_calibration_bucket_counts_every_prediction():
    rows=calibration_buckets(np.array([1,0,1]),np.array([.51,.61,.85]))
    assert sum(row["count"] for row in rows)==3


def test_no_trade_threshold_is_monotonic():
    rows=no_trade_thresholds(np.array([1,0,1]),np.array([.51,.61,.85]),np.array([.01,-.01,.02]))
    assert [r["trades_taken"] for r in rows]==sorted([r["trades_taken"] for r in rows],reverse=True)


def test_monte_carlo_is_deterministic():
    assert monte_carlo([1,-1,.5],.01,100,7)==monte_carlo([1,-1,.5],.01,100,7)


def test_prediction_forecast_fields_are_immutable():
    engine=create_engine("sqlite://");Base.metadata.create_all(engine)
    with Session(engine) as db:
        p=Prediction(created_at=datetime.now(UTC),symbol="EURUSD",timeframe="5m",candle_timestamp=datetime.now(UTC),
          probability_up=.5,probability_down=.5,expected_high=1.1,expected_low=1,expected_range=.1,
          expected_close_return=0,confidence=0,market_regime="range",decision="NO TRADE",strategy_outputs=[],
          feature_snapshot={},model_version="test",data_version="test")
        db.add(p);db.commit();p.probability_up=.9
        with pytest.raises(ValueError): db.commit()


def test_scheduler_boundary():
    assert next_close("5m",datetime(2025,1,1,10,2,tzinfo=UTC))==datetime(2025,1,1,10,5,tzinfo=UTC)
