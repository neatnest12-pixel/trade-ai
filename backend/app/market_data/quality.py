"""Non-destructive data quality checks for approved canonical datasets."""
import numpy as np
import pandas as pd


def audit(frame: pd.DataFrame, timeframe_minutes: int = 1) -> dict[str, object]:
    ts = pd.to_datetime(frame.timestamp, utc=True)
    gaps = ts.diff().dt.total_seconds().div(60).sub(timeframe_minutes).dropna()
    prices = frame[['open','high','low','close']].apply(pd.to_numeric, errors='coerce')
    return {'rows': len(frame), 'duplicates': int(ts.duplicated().sum()), 'out_of_order': not ts.is_monotonic_increasing,
            'nan_inf': int(prices.isna().sum().sum() + np.isinf(prices.to_numpy()).sum()),
            'invalid_ohlc': int(((prices.high < prices[['open','close']].max(axis=1)) | (prices.low > prices[['open','close']].min(axis=1)) | (prices <= 0).any(axis=1)).sum()),
            'unexpected_gaps': int((gaps > 0).sum()), 'largest_gap_minutes': float(gaps.max() + timeframe_minutes) if len(gaps) else 0}
