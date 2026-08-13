from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def candles():
    n=300; rng=np.random.default_rng(7); close=1.1+np.cumsum(rng.normal(0,.0002,n)); open_=np.r_[close[0],close[:-1]]
    return pd.DataFrame({"timestamp":[datetime(2025,1,1,tzinfo=UTC)+timedelta(minutes=5*i) for i in range(n)],
      "open":open_,"high":np.maximum(open_,close)+.0002,"low":np.minimum(open_,close)-.0002,"close":close,"tick_volume":1000})

