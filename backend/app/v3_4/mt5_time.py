from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

TIME_NORMALIZATION_VERSION = "v3.4-mt5-explicit-offset-r1"
OFFSET_ENV = "V3_MT5_SERVER_UTC_OFFSET_SECONDS"
MAX_CLOCK_SKEW_SECONDS = 5.0
MAX_MARKET_AGE_SECONDS = 300.0
MAX_ABSOLUTE_OFFSET_SECONDS = 14 * 60 * 60

TIMEFRAME_SECONDS = {
    "M1": 60,
    "M5": 5 * 60,
    "M15": 15 * 60,
    "H1": 60 * 60,
    "H6": 6 * 60 * 60,
    "H12": 12 * 60 * 60,
}


@dataclass(frozen=True)
class NormalizedTimestamp:
    source: str
    raw_epoch_seconds: float
    raw_timestamp: datetime
    timestamp: datetime
    configured_offset_seconds: int

    @property
    def normalization(self) -> str:
        return TIME_NORMALIZATION_VERSION


class ClockSkewError(RuntimeError):
    def __init__(
        self,
        *,
        market_timestamp: datetime,
        system_utc: datetime,
        source: str,
        difference_seconds: float,
    ) -> None:
        self.market_timestamp = _utc(market_timestamp)
        self.system_utc = _utc(system_utc)
        self.source = source
        self.difference_seconds = difference_seconds
        super().__init__("CLOCK_SKEW_DETECTED")

    def as_dict(self) -> dict[str, object]:
        return {
            "code": "CLOCK_SKEW_DETECTED",
            "market_timestamp": self.market_timestamp.isoformat(),
            "system_utc": self.system_utc.isoformat(),
            "difference_seconds": self.difference_seconds,
            "source": self.source,
        }


class CompletedCandleError(RuntimeError):
    pass


class MarketDataStaleError(RuntimeError):
    def __init__(self, source: str, age_seconds: float) -> None:
        self.source = source
        self.age_seconds = age_seconds
        super().__init__(f"MARKET_DATA_STALE:{source}:{age_seconds:.3f}")


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def configured_offset_seconds() -> int:
    raw = os.getenv(OFFSET_ENV, "").strip()
    if not raw:
        raise RuntimeError(f"{OFFSET_ENV}_REQUIRED")
    try:
        offset = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{OFFSET_ENV}_INVALID") from exc
    if abs(offset) > MAX_ABSOLUTE_OFFSET_SECONDS:
        raise RuntimeError(f"{OFFSET_ENV}_IMPLAUSIBLE")
    return offset


def normalize_mt5_epoch(
    epoch_seconds: float,
    *,
    offset_seconds: int,
    source: str,
) -> NormalizedTimestamp:
    raw_timestamp = datetime.fromtimestamp(float(epoch_seconds), UTC)
    timestamp = raw_timestamp - timedelta(seconds=offset_seconds)
    return NormalizedTimestamp(
        source=source,
        raw_epoch_seconds=float(epoch_seconds),
        raw_timestamp=raw_timestamp,
        timestamp=timestamp,
        configured_offset_seconds=offset_seconds,
    )


def require_not_future(
    market_timestamp: datetime,
    *,
    system_utc: datetime,
    source: str,
    tolerance_seconds: float = MAX_CLOCK_SKEW_SECONDS,
) -> None:
    market_timestamp = _utc(market_timestamp)
    system_utc = _utc(system_utc)
    difference = (market_timestamp - system_utc).total_seconds()
    if difference > tolerance_seconds:
        raise ClockSkewError(
            market_timestamp=market_timestamp,
            system_utc=system_utc,
            source=source,
            difference_seconds=difference,
        )


def require_recent(
    market_timestamp: datetime,
    *,
    system_utc: datetime,
    source: str,
    max_age_seconds: float = MAX_MARKET_AGE_SECONDS,
) -> None:
    market_timestamp = _utc(market_timestamp)
    system_utc = _utc(system_utc)
    age_seconds = (system_utc - market_timestamp).total_seconds()
    if age_seconds > max_age_seconds:
        raise MarketDataStaleError(source, age_seconds)


def require_completed_candle(
    candle_timestamp: datetime,
    *,
    timeframe: str,
    decision_timestamp: datetime,
) -> None:
    if timeframe not in TIMEFRAME_SECONDS:
        raise CompletedCandleError(f"UNKNOWN_TIMEFRAME:{timeframe}")
    completed_at = _utc(candle_timestamp) + timedelta(seconds=TIMEFRAME_SECONDS[timeframe])
    if completed_at > _utc(decision_timestamp):
        raise CompletedCandleError(
            f"FUTURE_OR_INCOMPLETE_CONTEXT:{timeframe}:"
            f"{completed_at.isoformat()}>{_utc(decision_timestamp).isoformat()}"
        )
