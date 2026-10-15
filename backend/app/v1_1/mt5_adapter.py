from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any


@dataclass(frozen=True)
class Candle:
    symbol: str
    timeframe: str
    timestamp_utc: datetime
    open: float
    high: float
    low: float
    close: float
    tick_volume: int
    spread: int
    real_volume: int
    complete: bool


class MT5Adapter:
    CLOCK_SKEW_SECONDS = 2

    def __init__(self, mt5: Any, *, symbol="EURUSD", broker="Raw Trading Ltd",
                 server="ICMarketsSC-Demo", now=None):
        self.mt5 = mt5
        self.symbol = symbol
        self.broker = broker
        self.server = server
        self.now = now or (lambda: datetime.now(UTC))
        self.offset_hours: int | None = None
        self.status = "DISCONNECTED"

    def connect(self) -> str:
        if not self.mt5.initialize():
            self.status = "DISCONNECTED"
            return self.status
        info = self.mt5.account_info()
        if info is not None and getattr(info, "company", self.broker) != self.broker:
            raise RuntimeError("broker mismatch")
        if info is not None and getattr(info, "server", self.server) != self.server:
            raise RuntimeError("server mismatch")
        if not self.mt5.symbol_select(self.symbol, True):
            self.status = "NO_DATA"
            return self.status
        tick = self.mt5.symbol_info_tick(self.symbol)
        if tick is None:
            self.status = "NO_DATA"
            return self.status
        raw = datetime.fromtimestamp(float(tick.time), UTC)
        delta = (raw - self.now()).total_seconds() / 3600
        rounded = round(delta)
        if abs(delta - rounded) > 0.25 or abs(rounded) > 6:
            self.status = "IMPLAUSIBLE_SERVER_OFFSET"
            return self.status
        self.offset_hours = rounded
        normalized = raw - timedelta(hours=rounded)
        age = abs((self.now() - normalized).total_seconds())
        self.status = "CONNECTED" if age <= 300 else "STALE_FEED"
        return self.status

    def _candles(self, timeframe: str, count: int) -> list[Candle]:
        if self.offset_hours is None:
            return []
        tf = {"M1": self.mt5.TIMEFRAME_M1, "M5": self.mt5.TIMEFRAME_M5,
              "M15": self.mt5.TIMEFRAME_M15}[timeframe]
        rows = self.mt5.copy_rates_from_pos(self.symbol, tf, 1, count)
        if rows is None:
            self.status = "NO_DATA"
            return []
        out: list[Candle] = []
        for row in rows:
            ts = datetime.fromtimestamp(float(row["time"]), UTC) - timedelta(hours=self.offset_hours)
            delta = (ts - self.now()).total_seconds()
            if delta > self.CLOCK_SKEW_SECONDS:
                self.status = "FUTURE_CANDLE"
                return []
            if delta > 0:
                self.status = "CURRENT_FORMING_CANDLE"
                continue
            out.append(Candle(self.symbol, timeframe, ts, float(row["open"]), float(row["high"]),
                              float(row["low"]), float(row["close"]), int(row["tick_volume"]),
                              int(row["spread"]), int(row["real_volume"]), True))
        return out

    def latest_completed_candle(self, timeframe="M1") -> Candle | None:
        rows = self._candles(timeframe, 1)
        return rows[-1] if rows else None

    def recent_completed_candles(self, timeframe="M1", count=100) -> list[Candle]:
        return self._candles(timeframe, count)

