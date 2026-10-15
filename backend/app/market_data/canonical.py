"""Provider-neutral, fail-closed canonical M1 preparation utilities."""
from dataclasses import dataclass
from typing import Literal

import pandas as pd

PriceSide = Literal['BID', 'ASK', 'MID', 'OHLC', 'OTHER']

@dataclass(frozen=True)
class ProviderMetadata:
    provider: str
    instrument: str
    price_side: PriceSide
    source_timezone: str
    retrieval_method: str
    retrieval_timestamp: str
    usage_status: str

def canonicalize(frame: pd.DataFrame, metadata: ProviderMetadata) -> pd.DataFrame:
    required = ['timestamp','open','high','low','close']
    missing = [c for c in required if c not in frame.columns]
    if missing: raise ValueError(f'missing canonical fields: {missing}')
    out = frame.copy(); out['timestamp'] = pd.to_datetime(out['timestamp'], utc=True, errors='raise')
    for c in ['open','high','low','close']:
        out[c] = pd.to_numeric(out[c], errors='raise')
    if out.timestamp.duplicated().any(): raise ValueError('duplicate timestamps')
    if not out.timestamp.is_monotonic_increasing: raise ValueError('timestamps out of order')
    if not ((out[['open','high','low','close']] > 0).all().all()): raise ValueError('non-positive OHLC')
    if (out.high < out[['open','close']].max(axis=1)).any() or (out.low > out[['open','close']].min(axis=1)).any(): raise ValueError('invalid OHLC')
    if metadata.usage_status not in {'PERMITTED','PERMITTED_WITH_PLAN'}: raise ValueError('usage rights not approved')
    out['source'] = metadata.provider; out['price_side'] = metadata.price_side
    out['provenance'] = metadata.retrieval_method
    return out

def depth_classification(start: pd.Timestamp, end: pd.Timestamp) -> str:
    years = (end - start).total_seconds() / (365.25 * 86400)
    return 'EXCELLENT' if years >= 3 else 'ACCEPTABLE_WITH_LIMITATIONS' if years >= 2 else 'INSUFFICIENT'
