"""Isolated TrustedClock experiment; deliberately not imported by production.

This module corrects acquisition time only. It never changes the supplied
market timestamp. A measurement is accepted only when its sign convention,
freshness, sample spread, network uncertainty and Windows root dispersion are
available and within explicit experimental bounds.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import math
import statistics
from typing import Iterable

from app.v3_4.mt5_time import normalize_mt5_epoch

BROKER_OFFSET_SECONDS = 10800
MAX_MEASUREMENT_AGE_SECONDS = 15.0
MAX_SAMPLE_SPREAD_SECONDS = 0.05
MAX_TOTAL_UNCERTAINTY_SECONDS = 0.05
MAX_OFFSET_STEP_SECONDS = 0.10
MAX_HOST_CLOCK_STEP_SECONDS = 0.05
MAX_TICK_AGE_SECONDS = 5.0
MAX_ABSOLUTE_NTP_OFFSET_SECONDS = 5.0


class TrustedClockError(ValueError):
    """Fail-closed trusted-clock or tick validation failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise TrustedClockError("NAIVE_TIMESTAMP")
    return value.astimezone(UTC)


def production_age_status(age_seconds: float) -> str:
    """Unchanged production tick rule, copied for isolated replay comparison."""
    if not math.isfinite(age_seconds):
        return "INVALID_TICK_AGE"
    if age_seconds < 0:
        return "FUTURE_TICK"
    if age_seconds > MAX_TICK_AGE_SECONDS:
        return "STALE_TICK"
    return "ACCEPT"


@dataclass(frozen=True)
class NtpMeasurement:
    source: str
    measured_at_utc: datetime
    offset_seconds: float
    sample_offsets_seconds: tuple[float, ...]
    # w32tm stripchart does not expose a defensible RTT/error bound; callers
    # must supply it from a measurement method that does.
    network_uncertainty_seconds: float | None
    root_dispersion_seconds: float | None
    offset_definition: str = "trusted_minus_host"

    @classmethod
    def from_samples(
        cls,
        *,
        source: str,
        measured_at_utc: datetime,
        sample_offsets_seconds: Iterable[float],
        network_uncertainty_seconds: float | None,
        root_dispersion_seconds: float | None,
        offset_definition: str = "trusted_minus_host",
    ) -> "NtpMeasurement":
        samples = tuple(float(x) for x in sample_offsets_seconds)
        if len(samples) < 3 or not all(math.isfinite(x) for x in samples):
            raise TrustedClockError("NTP_UNAVAILABLE_OR_INVALID")
        return cls(source, _utc(measured_at_utc), statistics.mean(samples), samples,
                   network_uncertainty_seconds, root_dispersion_seconds,
                   offset_definition)

    @property
    def sample_spread_seconds(self) -> float:
        return max(self.sample_offsets_seconds) - min(self.sample_offsets_seconds)

    @property
    def uncertainty_seconds(self) -> float:
        if self.network_uncertainty_seconds is None or self.root_dispersion_seconds is None:
            raise TrustedClockError("NTP_UNCERTAINTY_UNAVAILABLE")
        if not (math.isfinite(self.network_uncertainty_seconds)
                and math.isfinite(self.root_dispersion_seconds)):
            raise TrustedClockError("NTP_UNCERTAINTY_INVALID")
        # Conservatively include measured network error, observed dispersion,
        # and within-sample variation; these are uncertainty terms, not offsets.
        return (max(0.0, self.network_uncertainty_seconds)
                + max(0.0, self.root_dispersion_seconds)
                + self.sample_spread_seconds / 2.0)


@dataclass(frozen=True)
class TrustedAcquisition:
    raw_host_acquisition_utc: datetime
    ntp_offset_seconds: float
    ntp_measurement_age_seconds: float
    ntp_uncertainty_seconds: float
    ntp_sample_spread_seconds: float
    trusted_acquisition_utc: datetime


@dataclass(frozen=True)
class TickValidation:
    raw_market_epoch_seconds: float
    normalized_market_utc: datetime
    acquisition: TrustedAcquisition
    production_host_age_seconds: float
    trusted_age_seconds: float
    status: str


class TrustedClock:
    """Pure experimental validator. Caller supplies fresh NTP evidence."""

    def __init__(self, *, max_measurement_age_seconds: float = MAX_MEASUREMENT_AGE_SECONDS,
                 max_sample_spread_seconds: float = MAX_SAMPLE_SPREAD_SECONDS,
                 max_uncertainty_seconds: float = MAX_TOTAL_UNCERTAINTY_SECONDS,
                 max_offset_step_seconds: float = MAX_OFFSET_STEP_SECONDS,
                 max_host_clock_step_seconds: float = MAX_HOST_CLOCK_STEP_SECONDS,
                 max_tick_age_seconds: float = MAX_TICK_AGE_SECONDS):
        self.max_measurement_age_seconds = max_measurement_age_seconds
        self.max_sample_spread_seconds = max_sample_spread_seconds
        self.max_uncertainty_seconds = max_uncertainty_seconds
        self.max_offset_step_seconds = max_offset_step_seconds
        self.max_host_clock_step_seconds = max_host_clock_step_seconds
        self.max_tick_age_seconds = max_tick_age_seconds
        self._last_offset_seconds: float | None = None
        self._last_host_utc: datetime | None = None
        self._last_monotonic_seconds: float | None = None
        self._seen_tick_identities: set[tuple[object, ...]] = set()

    def acquire(self, *, host_acquisition_utc: datetime, now_host_utc: datetime,
                ntp: NtpMeasurement, monotonic_seconds: float | None = None) -> TrustedAcquisition:
        host = _utc(host_acquisition_utc)
        now = _utc(now_host_utc)
        if ntp.offset_definition != "trusted_minus_host":
            raise TrustedClockError("NTP_OFFSET_SIGN_CONTRACT_INVALID")
        if (not ntp.source or not math.isfinite(ntp.offset_seconds)
                or len(ntp.sample_offsets_seconds) < 3
                or not all(math.isfinite(x) for x in ntp.sample_offsets_seconds)):
            raise TrustedClockError("NTP_UNAVAILABLE_OR_INVALID")
        recomputed_offset = statistics.mean(ntp.sample_offsets_seconds)
        if abs(recomputed_offset - ntp.offset_seconds) > 1e-6:
            raise TrustedClockError("NTP_OFFSET_SAMPLE_MISMATCH")
        if abs(ntp.offset_seconds) > MAX_ABSOLUTE_NTP_OFFSET_SECONDS:
            raise TrustedClockError("NTP_OFFSET_OUTSIDE_BOUND")
        if host > now:
            raise TrustedClockError("ACQUISITION_TIMESTAMP_FROM_FUTURE")
        if ntp.sample_spread_seconds > self.max_sample_spread_seconds:
            raise TrustedClockError("NTP_SPREAD_EXCESSIVE")
        uncertainty = ntp.uncertainty_seconds
        if uncertainty > self.max_uncertainty_seconds:
            raise TrustedClockError("NTP_UNCERTAINTY_EXCESSIVE")
        measurement_age = (now - _utc(ntp.measured_at_utc)).total_seconds()
        if measurement_age < 0:
            raise TrustedClockError("NTP_MEASUREMENT_FROM_FUTURE")
        if measurement_age > self.max_measurement_age_seconds:
            raise TrustedClockError("NTP_MEASUREMENT_STALE")
        if self._last_offset_seconds is not None and abs(ntp.offset_seconds - self._last_offset_seconds) > self.max_offset_step_seconds:
            raise TrustedClockError("NTP_OFFSET_JUMP")
        if monotonic_seconds is not None and self._last_host_utc is not None:
            if self._last_monotonic_seconds is None:
                raise TrustedClockError("MONOTONIC_REFERENCE_MISSING")
            host_elapsed = (host - self._last_host_utc).total_seconds()
            monotonic_elapsed = monotonic_seconds - self._last_monotonic_seconds
            if abs(host_elapsed - monotonic_elapsed) > self.max_host_clock_step_seconds:
                raise TrustedClockError("HOST_CLOCK_JUMP")
        trusted = host + timedelta(seconds=ntp.offset_seconds)
        self._last_offset_seconds = ntp.offset_seconds
        self._last_host_utc = host
        self._last_monotonic_seconds = monotonic_seconds
        return TrustedAcquisition(host, ntp.offset_seconds, measurement_age,
                                  uncertainty, ntp.sample_spread_seconds, trusted)

    def validate_tick(self, *, raw_market_epoch_seconds: float,
                      host_acquisition_utc: datetime, now_host_utc: datetime,
                      ntp: NtpMeasurement, monotonic_seconds: float | None = None,
                      broker_offset_seconds: int = BROKER_OFFSET_SECONDS,
                      tick_identity: tuple[object, ...] | None = None) -> TickValidation:
        if broker_offset_seconds != BROKER_OFFSET_SECONDS:
            raise TrustedClockError("BROKER_OFFSET_MISMATCH")
        if not math.isfinite(raw_market_epoch_seconds):
            raise TrustedClockError("INVALID_MARKET_TIMESTAMP")
        if tick_identity is not None and tick_identity in self._seen_tick_identities:
            raise TrustedClockError("REPLAYED_TICK")
        # The broker normalization is applied once here. The returned market
        # timestamp is immutable and never receives the NTP correction.
        normalized = normalize_mt5_epoch(float(raw_market_epoch_seconds),
                                         offset_seconds=BROKER_OFFSET_SECONDS,
                                         source="EURUSD").timestamp
        acquisition = self.acquire(host_acquisition_utc=host_acquisition_utc,
                                   now_host_utc=now_host_utc, ntp=ntp,
                                   monotonic_seconds=monotonic_seconds)
        host_age = (acquisition.raw_host_acquisition_utc - normalized).total_seconds()
        trusted_age = (acquisition.trusted_acquisition_utc - normalized).total_seconds()
        uncertainty = acquisition.ntp_uncertainty_seconds
        if trusted_age + uncertainty < 0:
            status = "FUTURE_TICK"
        elif trusted_age - uncertainty < 0:
            status = "TICK_TIME_UNCERTAIN"
        elif trusted_age - uncertainty > self.max_tick_age_seconds:
            status = "STALE_TICK"
        elif trusted_age + uncertainty > self.max_tick_age_seconds:
            status = "TICK_FRESHNESS_UNCERTAIN"
        else:
            status = "ACCEPT"
        if tick_identity is not None:
            self._seen_tick_identities.add(tick_identity)
        return TickValidation(float(raw_market_epoch_seconds), normalized,
                              acquisition, host_age, trusted_age, status)
