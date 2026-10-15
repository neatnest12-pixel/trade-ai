import pandas as pd
import pytest

from app.market_data.canonical import ProviderMetadata, canonicalize, depth_classification


def meta(status='PERMITTED'):
    return ProviderMetadata('approved','EURUSD','BID','UTC','manual','2026-01-01T00:00:00Z',status)
def test_canonicalize_and_metadata():
    x=canonicalize(pd.DataFrame({'timestamp':['2026-01-01T00:00:00Z'],'open':[1.1],'high':[1.2],'low':[1.0],'close':[1.15]}),meta())
    assert x.source.iloc[0]=='approved' and x.timestamp.dt.tz is not None
def test_unclear_usage_fails_closed():
    with pytest.raises(ValueError): canonicalize(pd.DataFrame({'timestamp':['2026-01-01'],'open':[1],'high':[1],'low':[1],'close':[1]}),meta('UNCLEAR'))
def test_depth():
    assert depth_classification(pd.Timestamp('2020-01-01',tz='UTC'),pd.Timestamp('2024-01-01',tz='UTC'))=='EXCELLENT'
