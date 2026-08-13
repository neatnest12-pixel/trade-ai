from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

TIMEFRAME_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240}
REQUIRED = {"timestamp", "open", "high", "low", "close", "tick_volume"}


def validate_candles(frame: pd.DataFrame) -> pd.DataFrame:
    missing = REQUIRED - set(frame.columns)
    if missing:
        raise ValueError(f"Missing candle columns: {sorted(missing)}")
    result = frame.copy()
    result["timestamp"] = pd.to_datetime(result["timestamp"], utc=True)
    result = result.sort_values("timestamp")
    if result["timestamp"].duplicated().any():
        raise ValueError("Duplicate candle timestamps detected")
    numeric = ["open", "high", "low", "close", "tick_volume"]
    if result[numeric].isna().any().any():
        raise ValueError("NaN in required candle fields")
    if ((result["high"] < result[["open", "close"]].max(axis=1)) |
            (result["low"] > result[["open", "close"]].min(axis=1))).any():
        raise ValueError("Invalid OHLC relationship")
    return result.reset_index(drop=True)


def identify_gaps(frame: pd.DataFrame, timeframe: str) -> list[dict[str, object]]:
    expected = pd.Timedelta(minutes=TIMEFRAME_MINUTES[timeframe])
    delta = frame["timestamp"].diff()
    return [{"after": frame.loc[i - 1, "timestamp"], "before": frame.loc[i, "timestamp"],
             "missing_intervals": int(delta.iloc[i] / expected) - 1}
            for i in range(1, len(frame)) if delta.iloc[i] > expected]


class MarketDataAdapter(ABC):
    @abstractmethod
    def fetch(self, symbol: str, timeframe: str, limit: int = 500) -> pd.DataFrame: ...


class CSVAdapter(MarketDataAdapter):
    def __init__(self, path: str | Path): self.path = Path(path)
    def fetch(self, symbol: str, timeframe: str, limit: int = 500) -> pd.DataFrame:
        frame = pd.read_csv(self.path)
        if "symbol" in frame: frame = frame[frame.symbol == symbol]
        if "timeframe" in frame: frame = frame[frame.timeframe == timeframe]
        return validate_candles(frame.tail(limit))


class MockAdapter(MarketDataAdapter):
    """Deterministic synthetic data, clearly labelled and never presented as live."""
    def __init__(self, seed: int = 42): self.seed = seed
    def fetch(self, symbol: str, timeframe: str, limit: int = 500) -> pd.DataFrame:
        if timeframe not in TIMEFRAME_MINUTES: raise ValueError("Unsupported timeframe")
        rng = np.random.default_rng(self.seed + TIMEFRAME_MINUTES[timeframe])
        end = datetime.now(UTC).replace(second=0, microsecond=0)
        step = timedelta(minutes=TIMEFRAME_MINUTES[timeframe])
        timestamps = [end - step * (limit - i) for i in range(limit)]
        returns = rng.normal(0, .00025 * np.sqrt(TIMEFRAME_MINUTES[timeframe]), limit)
        close = 1.085 + np.cumsum(returns)
        open_ = np.r_[close[0], close[:-1]]
        wick = rng.uniform(.00005, .00035, limit)
        return validate_candles(pd.DataFrame({"timestamp": timestamps, "open": open_,
            "high": np.maximum(open_, close) + wick, "low": np.minimum(open_, close) - wick,
            "close": close, "tick_volume": rng.integers(100, 2000, limit),
            "source": "mock", "symbol": symbol, "timeframe": timeframe}))


class MT5Adapter(MarketDataAdapter):
    """Read-only MT5 adapter. Order functions are intentionally absent."""
    def fetch(self, symbol: str, timeframe: str, limit: int = 500) -> pd.DataFrame:
        try: import MetaTrader5 as mt5  # noqa: I001
        except ImportError as exc: raise RuntimeError("Install optional mt5 dependency on Windows") from exc
        mapping = {"1m": mt5.TIMEFRAME_M1, "5m": mt5.TIMEFRAME_M5, "15m": mt5.TIMEFRAME_M15,
                   "1h": mt5.TIMEFRAME_H1, "4h": mt5.TIMEFRAME_H4}
        if not mt5.initialize(): raise RuntimeError(f"MT5 initialization failed: {mt5.last_error()}")
        try: rates = mt5.copy_rates_from_pos(symbol, mapping[timeframe], 1, limit)  # skip forming bar
        finally: mt5.shutdown()
        if rates is None: raise RuntimeError("MT5 returned no data")
        frame = pd.DataFrame(rates).rename(columns={"time": "timestamp"})
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], unit="s", utc=True)
        frame["source"] = "mt5"
        return validate_candles(frame)
