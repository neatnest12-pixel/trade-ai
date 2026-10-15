"""EURUSD context r2: pure evidence-aware shadow diagnostics, no MT5 actions.

Broker assertions must arrive from an authenticated, identified source. Generic
calendar templates never become broker authority. This module cannot execute
orders, modify decisions or write campaign stores.
"""
from dataclasses import dataclass, asdict
from datetime import datetime
import math
from .market_context import context, utc, Config

VERSION = "v3.5-broker-context-r2"
CLOSED = {"CLOSED_WEEKEND", "CLOSED_SCHEDULED", "CLOSED_HOLIDAY", "CLOSED_BROKER"}


class FutureEvidenceError(ValueError):
    pass


@dataclass(frozen=True)
class CalendarEvidence:
    instrument: str
    broker: str
    server: str
    state: str
    available_at: datetime
    valid_from: datetime
    valid_until: datetime
    source: str
    authoritative: bool = False
    next_open_utc: datetime | None = None

    def __post_init__(self):
        if self.state not in CLOSED | {"OPEN", "UNKNOWN"}:
            raise ValueError("INVALID_CALENDAR_STATE")
        if utc(self.valid_until) <= utc(self.valid_from):
            raise ValueError("INVALID_CALENDAR_INTERVAL")


@dataclass(frozen=True)
class TerminalEvidence:
    observed_at: datetime
    available_at: datetime
    initialized: bool | None
    connected: bool | None
    authenticated: bool | None
    server: str | None
    symbol_available: bool | None
    symbol_visible: bool | None
    clock_safe: bool | None
    account_mode: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class TickEvidence:
    timestamp: datetime
    available_at: datetime
    bid: float
    ask: float
    raw_timestamp: str | float | None = None


@dataclass(frozen=True)
class MarketContextSnapshot:
    context_version: str
    observation_id: str | None
    context: dict
    quote_health: dict
    market: dict
    signal_integration: str = "SHADOW_MODE_NOT_USED_FOR_DECISIONS"

    def to_dict(self):
        return asdict(self)


def _known(observed, available, t):
    if utc(observed) > t or utc(available) > t:
        raise FutureEvidenceError("FUTURE_OR_LATE_AVAILABLE_EVIDENCE")
    if utc(available) < utc(observed):
        raise ValueError("AVAILABILITY_PRECEDES_OBSERVATION")


def snapshot(timestamp, *, broker, expected_server, terminal=None, tick=None,
             previous_tick=None, calendar=None, observation_id=None,
             spread_history=(), volatility_history=(), bars=(), config=Config(),
             terminal_max_age_seconds=30):
    """Build one shadow snapshot using only evidence available at timestamp.

    Freshness is fixed at the existing 5-second worker gate. Caller supplies
    normalized timestamps; future contamination is rejected, never silently used.
    No outcomes/strategy objects are accepted.
    """
    t=utc(timestamp)
    if not broker or not expected_server or terminal_max_age_seconds <= 0:
        raise ValueError("IDENTIFIED_BROKER_SERVER_REQUIRED")
    for sample in (*spread_history,*volatility_history):
        _known(sample.timestamp,sample.available_at,t)
        if sample.symbol != "EURUSD":raise ValueError("EURUSD_ONLY")
    for bar in bars:
        _known(bar.close_time,bar.available_at,t)
        if bar.symbol != "EURUSD":raise ValueError("EURUSD_ONLY")
    if terminal:
        _known(terminal.observed_at,terminal.available_at,t)
    if previous_tick:_known(previous_tick.timestamp,previous_tick.available_at,t)
    # Future tick timestamps are a clock error; evidence acquired in the future
    # is contamination and must fail rather than generate a snapshot.
    if tick and utc(tick.available_at)>t:raise FutureEvidenceError("FUTURE_TICK_AVAILABILITY")
    if tick and utc(tick.timestamp)<=t:_known(tick.timestamp,tick.available_at,t)
    if calendar and utc(calendar.available_at)>t:raise FutureEvidenceError("FUTURE_CALENDAR_EVIDENCE")
    f=context(t,"EURUSD",config=config,
              bid=tick.bid if tick else None,ask=tick.ask if tick else None,
              quote_time=tick.timestamp if tick else None,quote_available_at=tick.available_at if tick else None,
              spread_history=spread_history,volatility_history=volatility_history,bars=bars)
    f.update(is_asia=bool(f['session_asia']),is_london=bool(f['session_london']),
             is_new_york=bool(f['session_new_york']),is_london_new_york_overlap=f['is_london_ny_overlap'],is_rollover=f['is_rollover_window'])
    age=(t-utc(tick.timestamp)).total_seconds() if tick else None
    valid=bool(tick and all(math.isfinite(x) for x in (tick.bid,tick.ask)) and 0<tick.bid<=tick.ask)
    fresh=valid and age is not None and 0<=age<=5
    advancing=None
    if tick and previous_tick:
        if utc(previous_tick.available_at)>utc(tick.available_at):raise ValueError("TICK_SAMPLE_ORDER_INVALID")
        advancing=utc(tick.timestamp)>utc(previous_tick.timestamp)
    terminal_current=terminal is not None and (t-utc(terminal.observed_at)).total_seconds()<=terminal_max_age_seconds
    verified_server=terminal_current and terminal.server==expected_server
    authority=bool(calendar and calendar.authoritative and calendar.source and calendar.instrument=="EURUSD"
                   and calendar.broker==broker and calendar.server==expected_server
                   and utc(calendar.valid_from)<=t<utc(calendar.valid_until)
                   and verified_server and terminal.authenticated is True)
    broker_state=calendar.state if authority else "UNKNOWN"
    nominal="OPEN" if f['nominal_market_open'] else "CLOSED_WEEKEND" if f['is_weekend'] else "CLOSED_SCHEDULED"
    effective=broker_state
    if broker_state=="OPEN" and not fresh:effective="OPEN_BUT_NO_FRESH_TICKS"
    if broker_state in CLOSED and fresh:effective="UNKNOWN" # contradictory evidence
    if terminal_current and (terminal.initialized is False or terminal.connected is False or terminal.authenticated is False):
        feed="MT5_DISCONNECTED"
    elif terminal_current and (terminal.symbol_available is False or terminal.symbol_visible is False):
        feed="SYMBOL_UNAVAILABLE"
    elif terminal_current and terminal.clock_safe is False or age is not None and age<0:
        feed="CLOCK_OR_TIMESTAMP_ERROR"
    elif tick and not valid:
        feed="INVALID_QUOTE"
    elif not terminal_current or not verified_server or terminal.clock_safe is not True or terminal.initialized is not True or terminal.connected is not True or terminal.authenticated is not True or terminal.symbol_available is not True or terminal.symbol_visible is not True:
        feed="INSUFFICIENT_EVIDENCE"
    elif broker_state in CLOSED and not fresh:
        feed="EXPECTED_MARKET_CLOSURE"
    elif fresh and advancing is True and terminal.symbol_available is True and terminal.symbol_visible is True and broker_state not in CLOSED:
        feed="HEALTHY_LIVE_FEED"
        effective="OPEN" # live quote activity, not schedule certification
    elif broker_state=="OPEN" and not fresh and advancing is False:
        feed="STALE_FEED_MARKET_EXPECTED_OPEN"
    else:feed="INSUFFICIENT_EVIDENCE"
    market=dict(instrument="EURUSD",broker=broker,server=terminal.server if terminal else None,timestamp_utc=t.isoformat(),
                nominal_market_state=nominal,broker_market_state=broker_state,effective_market_state=effective,
                broker_calendar_authority=authority,calendar_source=calendar.source if authority else None,
                next_market_open_utc=utc(calendar.next_open_utc).isoformat() if authority and calendar.next_open_utc else None)
    quote=dict(symbol="EURUSD",bid=tick.bid if tick else None,ask=tick.ask if tick else None,
               spread_price=tick.ask-tick.bid if valid else None,spread_pips=(tick.ask-tick.bid)/.0001 if valid else None,
               raw_tick_timestamp=tick.raw_timestamp if tick else None,normalized_tick_timestamp=utc(tick.timestamp).isoformat() if tick else None,
               tick_age_seconds=age,tick_is_future=age is not None and age<0,tick_is_fresh=fresh,tick_is_advancing=advancing,
               terminal_connected=terminal.connected if terminal_current else None,
               account_authenticated=terminal.authenticated if terminal_current else None,server_verified=bool(verified_server),
               quote_valid=valid,market_state=effective,feed_state=feed,account_mode=terminal.account_mode if terminal else None,
               error=terminal.error if terminal else None)
    f.update(spread_pips=f['current_spread']/.0001 if f['current_spread'] is not None else None,
             spread_vs_reference_median=f['spread_vs_session_median'])
    return MarketContextSnapshot(VERSION,observation_id,f,quote,market)


def shadow_observation(observation_id, timestamp, *, recorder=None, **evidence):
    """Explicit prospective hook: emits separate context, has no decision input.

    Not wired to live collector pending integration authorization. Recorder must
    target a new context-only store, never the observation/outcome databases.
    """
    result=snapshot(timestamp,observation_id=observation_id,**evidence)
    if recorder is not None:recorder(result.to_dict())
    return result


def status_view(result):
    f,q,m=result.context,result.quote_health,result.market
    return dict(utc_time=f['timestamp_utc'],instrument="EURUSD",broker=m['broker'],server=m['server'],
                market_state=m['effective_market_state'],feed_state=q['feed_state'],
                current_session=f['current_session'],next_session=f['next_session'],
                tick_age=q['tick_age_seconds'],tick_fresh=q['tick_is_fresh'],tick_advancing=q['tick_is_advancing'],
                spread_pips=q['spread_pips'],spread_state=f['spread_state'],volatility_state=f['volatility_state'],
                context_version=result.context_version,signal_integration=result.signal_integration)
