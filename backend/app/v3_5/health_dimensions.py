"""Diagnostic health dimensions kept separate from trade eligibility.

R6 supports retaining the 5-second new-trade gate. It observed material
quote-age rejection and many EURUSD-specific quiet intervals, while controls
often advanced. Control advancement does not make a stale EURUSD quote safe.
R6 establishes neither a better production threshold nor profitability/edge.

This module records observed diagnostics only. Its quote-age result is a gate,
not a feed-health judgment; other pipeline stages remain independently stated.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum


class _StringState(str, Enum):
    def __str__(self) -> str:
        return self.value


class QuoteAgeState(_StringState):
    FRESH = "FRESH"
    STALE = "STALE"
    FUTURE = "FUTURE"
    MISSING = "MISSING"
    INVALID = "INVALID"


class TerminalHealth(_StringState):
    HEALTHY = "HEALTHY"
    DISCONNECTED = "DISCONNECTED"
    API_ERROR = "API_ERROR"
    API_SLOW = "API_SLOW"
    SYMBOL_UNAVAILABLE = "SYMBOL_UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


class FeedHealth(_StringState):
    HEALTHY = "HEALTHY"
    EURUSD_QUIET = "EURUSD_QUIET"
    GLOBAL_QUIET = "GLOBAL_QUIET"
    CONTROL_ADVANCING = "CONTROL_ADVANCING"
    CONTROL_DEGRADED = "CONTROL_DEGRADED"
    UNKNOWN = "UNKNOWN"


class SymbolActivity(_StringState):
    ADVANCING = "ADVANCING"
    QUIET = "QUIET"
    UNKNOWN = "UNKNOWN"


class CollectorHealth(_StringState):
    HEALTHY = "HEALTHY"
    SAMPLING_GAP = "SAMPLING_GAP"
    INTERRUPTED = "INTERRUPTED"
    STOPPED = "STOPPED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class ResolverHealth(_StringState):
    HEALTHY = "HEALTHY"
    STOPPED = "STOPPED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class PipelineEligibility(_StringState):
    ELIGIBLE = "ELIGIBLE"
    INELIGIBLE = "INELIGIBLE"
    NOT_EVALUATED = "NOT_EVALUATED"
    UNKNOWN = "UNKNOWN"


class RejectionReason(_StringState):
    QUOTE_MISSING = "QUOTE_MISSING"
    QUOTE_FUTURE = "QUOTE_FUTURE"
    QUOTE_INVALID = "QUOTE_INVALID"
    QUOTE_STALE = "QUOTE_STALE"
    CLOCK_INVALID = "CLOCK_INVALID"
    TERMINAL_DISCONNECTED = "TERMINAL_DISCONNECTED"
    TERMINAL_API_ERROR = "TERMINAL_API_ERROR"
    SYMBOL_UNAVAILABLE = "SYMBOL_UNAVAILABLE"
    SESSION_INVALID = "SESSION_INVALID"
    OTHER_EXISTING_SAFETY_REASON = "OTHER_EXISTING_SAFETY_REASON"


@dataclass(frozen=True)
class MarketDataHealthSnapshot:
    observed_at_utc: str
    quote_age_seconds: float | None
    quote_timestamp_utc: str | None
    quote_age_state: QuoteAgeState
    quote_age_limit_seconds: float
    quote_age_eligible: bool
    rejection_reason: RejectionReason | None
    terminal_state: TerminalHealth = TerminalHealth.UNKNOWN
    feed_state: FeedHealth = FeedHealth.UNKNOWN
    eurusd_activity: SymbolActivity = SymbolActivity.UNKNOWN
    collector_state: CollectorHealth = CollectorHealth.UNKNOWN
    resolver_state: ResolverHealth = ResolverHealth.UNKNOWN
    control_symbol_advancement: tuple[tuple[str, bool | None], ...] = field(default_factory=tuple)
    api_latency_ms: float | None = None
    signal_eligibility: PipelineEligibility = PipelineEligibility.NOT_EVALUATED
    risk_eligibility: PipelineEligibility = PipelineEligibility.NOT_EVALUATED
    execution_eligibility: PipelineEligibility = PipelineEligibility.NOT_EVALUATED

    def to_dict(self) -> dict:
        value = asdict(self)
        for key in ("quote_age_state", "rejection_reason", "terminal_state", "feed_state",
                    "collector_state", "resolver_state", "signal_eligibility",
                    "risk_eligibility", "execution_eligibility", "eurusd_activity"):
            state = value[key]
            value[key] = state.value if state is not None else None
        value["control_symbol_advancement"] = dict(self.control_symbol_advancement)
        return value


def _utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("EXPLICIT_TIMEZONE_REQUIRED")
    return value.astimezone(UTC).isoformat()


def _feed_state(quote_state: QuoteAgeState,
                controls: Mapping[str, bool | None] | None) -> FeedHealth:
    if not controls:
        return FeedHealth.UNKNOWN
    known = [value for value in controls.values() if value is not None]
    if not known:
        return FeedHealth.UNKNOWN
    if all(value is False for value in known) and len(known) == len(controls):
        return FeedHealth.GLOBAL_QUIET
    if any(value is True for value in known):
        if quote_state is QuoteAgeState.STALE:
            return FeedHealth.CONTROL_ADVANCING
        if any(value is False for value in known):
            return FeedHealth.CONTROL_DEGRADED
        return FeedHealth.HEALTHY
    return FeedHealth.CONTROL_DEGRADED


def assess_quote(*, observed_at: datetime, quote_timestamp: datetime | None,
                 bid: float | None, ask: float | None, max_age_seconds: float,
                 quote_present: bool = True,
                 terminal_state: TerminalHealth = TerminalHealth.UNKNOWN,
                 collector_state: CollectorHealth = CollectorHealth.UNKNOWN,
                 resolver_state: ResolverHealth = ResolverHealth.UNKNOWN,
                 control_symbol_advancement: Mapping[str, bool | None] | None = None,
                 eurusd_advanced: bool | None = None,
                 api_latency_ms: float | None = None) -> MarketDataHealthSnapshot:
    """Classify quote age and diagnostic health without relaxing eligibility."""
    if max_age_seconds <= 0 or not math.isfinite(max_age_seconds):
        raise ValueError("INVALID_QUOTE_AGE_LIMIT")
    observed_iso = _utc_iso(observed_at)
    quote_iso = _utc_iso(quote_timestamp)
    age = ((observed_at.astimezone(UTC) - quote_timestamp.astimezone(UTC)).total_seconds()
           if quote_timestamp is not None else None)

    # Preserve the existing order: missing, future, invalid prices, then age.
    if not quote_present or quote_timestamp is None:
        state, reason = QuoteAgeState.MISSING, RejectionReason.QUOTE_MISSING
    elif age is not None and age < 0:
        state, reason = QuoteAgeState.FUTURE, RejectionReason.QUOTE_FUTURE
    else:
        try:
            prices_valid = (bid is not None and ask is not None
                            and math.isfinite(float(bid)) and math.isfinite(float(ask))
                            and 0 < float(bid) <= float(ask))
        except (TypeError, ValueError, OverflowError):
            prices_valid = False
        if not prices_valid:
            state, reason = QuoteAgeState.INVALID, RejectionReason.QUOTE_INVALID
        elif age is not None and age > max_age_seconds:
            state, reason = QuoteAgeState.STALE, RejectionReason.QUOTE_STALE
        else:
            state, reason = QuoteAgeState.FRESH, None

    controls = tuple(sorted((control_symbol_advancement or {}).items()))
    feed = _feed_state(state, dict(controls))
    activity = (SymbolActivity.ADVANCING if eurusd_advanced is True else
                SymbolActivity.QUIET if eurusd_advanced is False else SymbolActivity.UNKNOWN)
    return MarketDataHealthSnapshot(
        observed_at_utc=observed_iso or "", quote_age_seconds=age,
        quote_timestamp_utc=quote_iso, quote_age_state=state,
        quote_age_limit_seconds=max_age_seconds, quote_age_eligible=state is QuoteAgeState.FRESH,
        rejection_reason=reason, terminal_state=terminal_state, feed_state=feed,
        eurusd_activity=activity, collector_state=collector_state,
        resolver_state=resolver_state, control_symbol_advancement=controls,
        api_latency_ms=api_latency_ms,
    )
