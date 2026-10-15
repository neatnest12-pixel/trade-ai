from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any


def utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


@dataclass(frozen=True)
class CrossAssetObservation:
    instrument: str
    provider: str
    raw_timestamp: datetime
    raw_timezone: str
    normalized_timestamp_utc: datetime
    retrieved_at: datetime
    price: float | None
    native_frequency: str
    market_session: str
    max_staleness_seconds: int

    def normalized(self) -> dict[str, Any]:
        return {
            "instrument": self.instrument,
            "provider": self.provider,
            "raw_timestamp": self.raw_timestamp.isoformat(),
            "raw_timezone": self.raw_timezone,
            "normalized_timestamp_utc": self.normalized_timestamp_utc.isoformat(),
            "retrieved_at": self.retrieved_at.isoformat(),
            "price": self.price,
            "native_frequency": self.native_frequency,
            "market_session": self.market_session,
            "max_staleness_seconds": self.max_staleness_seconds,
        }


STALENESS_LIMITS_SECONDS = {
    "USD_PROXY": 10 * 24 * 60 * 60,
    "GOLD": 5 * 60,
    "EQUITY_PROXY": 4 * 24 * 60 * 60,
    "VOLATILITY_PROXY": 4 * 24 * 60 * 60,
}


def make_observation(
    instrument: str,
    provider: str,
    timestamp: datetime,
    price: float | None,
    *,
    retrieved_at: datetime,
    native_frequency: str,
    market_session: str,
    raw_timezone: str = "UTC",
    raw_timestamp: datetime | None = None,
    max_staleness_seconds: int | None = None,
) -> CrossAssetObservation:
    return CrossAssetObservation(
        instrument=instrument,
        provider=provider,
        raw_timestamp=utc(raw_timestamp or timestamp),
        raw_timezone=raw_timezone,
        normalized_timestamp_utc=utc(timestamp),
        retrieved_at=utc(retrieved_at),
        price=price,
        native_frequency=native_frequency,
        market_session=market_session,
        max_staleness_seconds=max_staleness_seconds or STALENESS_LIMITS_SECONDS[instrument],
    )


def alignment_quality(age_seconds: float, max_staleness_seconds: int, price: float | None) -> str:
    if price is None or age_seconds < 0 or age_seconds > max_staleness_seconds:
        return "UNAVAILABLE"
    if age_seconds <= max_staleness_seconds * 0.25:
        return "HIGH"
    if age_seconds <= max_staleness_seconds * 0.75:
        return "MEDIUM"
    return "LOW"


def asof_join(
    decision_timestamp: datetime,
    observations: Iterable[CrossAssetObservation],
) -> dict[str, Any]:
    decision = utc(decision_timestamp)
    eligible = [obs for obs in observations if obs.normalized_timestamp_utc <= decision]
    if not eligible:
        return {
            "state": "UNAVAILABLE",
            "reason": "NO_ASOF_OBSERVATION",
            "decision_timestamp": decision.isoformat(),
        }
    latest = max(eligible, key=lambda obs: obs.normalized_timestamp_utc)
    age = (decision - latest.normalized_timestamp_utc).total_seconds()
    quality = alignment_quality(age, latest.max_staleness_seconds, latest.price)
    return {
        **latest.normalized(),
        "decision_timestamp": decision.isoformat(),
        "source_age_seconds": age,
        "alignment_quality": quality,
        "state": "AVAILABLE" if quality != "UNAVAILABLE" else "UNAVAILABLE",
        "no_lookahead": latest.normalized_timestamp_utc <= decision,
    }


def build_cross_asset_snapshot(
    decision_timestamp: datetime,
    observations_by_instrument: dict[str, Iterable[CrossAssetObservation]],
) -> dict[str, Any]:
    joined = {
        instrument: asof_join(decision_timestamp, observations)
        for instrument, observations in observations_by_instrument.items()
    }
    complete = all(item.get("state") == "AVAILABLE" and item.get("no_lookahead") is True for item in joined.values())
    return {
        "decision_timestamp": utc(decision_timestamp).isoformat(),
        "instruments": joined,
        "mixed_frequency_safety": mixed_frequency_feature_names(),
        "cross_asset_complete": complete,
    }


def mixed_frequency_feature_names() -> dict[str, list[str]]:
    return {
        "intraday": ["gold_return_5m", "gold_return_15m", "gold_return_1h"],
        "daily": ["usd_proxy_daily_change", "equity_proxy_daily_change", "volatility_proxy_daily_change"],
    }


def coverage_audit(decision_timestamps: Iterable[datetime], observations_by_instrument: dict[str, Iterable[CrossAssetObservation]]) -> dict[str, Any]:
    decisions = [utc(ts) for ts in decision_timestamps]
    report: dict[str, Any] = {"total_decisions": len(decisions), "instruments": {}}
    for instrument, observations in observations_by_instrument.items():
        joined = [asof_join(ts, observations) for ts in decisions]
        ages = [item["source_age_seconds"] for item in joined if item.get("state") == "AVAILABLE"]
        qualities = {quality: sum(1 for item in joined if item.get("alignment_quality") == quality) for quality in ("HIGH", "MEDIUM", "LOW", "UNAVAILABLE")}
        report["instruments"][instrument] = {
            "available": sum(1 for item in joined if item.get("state") == "AVAILABLE"),
            "unavailable": sum(1 for item in joined if item.get("state") != "AVAILABLE"),
            **qualities,
            "median_age": median(ages) if ages else None,
            "p95_age": percentile(ages, 95) if ages else None,
        }
    return report


def percentile(values: list[float], pct: int) -> float:
    if not values:
        raise ValueError("percentile requires values")
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((pct / 100) * (len(ordered) - 1))))
    return ordered[index]


def daily_market_session(timestamp: datetime) -> str:
    ts = utc(timestamp)
    if ts.weekday() >= 5:
        return "CLOSED_WEEKEND"
    return "DAILY_CLOSE"


def gold_market_session(timestamp: datetime) -> str:
    ts = utc(timestamp)
    if ts.weekday() >= 5:
        return "FX_CLOSED_WEEKEND"
    if timedelta(hours=21) <= timedelta(hours=ts.hour, minutes=ts.minute) <= timedelta(hours=22):
        return "ROLLOVER_WINDOW"
    return "FX_SESSION"
