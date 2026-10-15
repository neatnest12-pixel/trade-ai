"""Versioned, pure contextual features. No store writes, MT5 calls or decisions.

Session hours are configurable analytical conventions, not exchange hours.
Instrument calendar templates are indicative until verified against the broker.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, time, date
from zoneinfo import ZoneInfo

VERSION = "v3.5-market-context-r1"
UTC = timezone.utc


def utc(value: datetime | str) -> datetime:
    value = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("AWARE_TIMESTAMP_REQUIRED")
    return value.astimezone(UTC)


@dataclass(frozen=True)
class Session:
    name: str
    zone: str
    open: time
    close: time


@dataclass(frozen=True)
class InstrumentSchedule:
    symbol: str
    asset_class: str
    zone: str = "America/New_York"
    weekly_open_day: int = 6
    weekly_open: time = time(17)
    weekly_close_day: int = 4
    weekly_close: time = time(17)
    daily_break: tuple[time, time] | None = None
    closed_dates: frozenset[date] = frozenset()
    verified: bool = False
    evidence: str | None = None

    def __post_init__(self):
        if self.verified and not self.evidence:
            raise ValueError("VERIFIED_CALENDAR_REQUIRES_EVIDENCE")


INSTRUMENTS = {
    "EURUSD": InstrumentSchedule("EURUSD", "FX"),
    "XAUUSD": InstrumentSchedule("XAUUSD", "PRECIOUS_METAL", weekly_open=time(18), daily_break=(time(17), time(18))),
    "XAGUSD": InstrumentSchedule("XAGUSD", "PRECIOUS_METAL", weekly_open=time(18), daily_break=(time(17), time(18))),
}


@dataclass(frozen=True)
class Config:
    sessions: tuple[Session, ...] = (
        Session("ASIA", "Asia/Tokyo", time(9), time(18)),
        Session("LONDON", "Europe/London", time(8), time(17)),
        Session("NEW_YORK", "America/New_York", time(8), time(17)),
    )
    open_window_minutes: int = 30
    close_window_minutes: int = 30
    transition_minutes: int = 30
    rollover_minutes: int = 15
    weekly_window_minutes: int = 60
    minimum_reference_samples: int = 20
    reference_max_age_minutes: int = 30 * 24 * 60
    spread_percentiles: tuple[float, float] = (90, 99)
    volatility_percentiles: tuple[float, float, float] = (25, 75, 95)
    atr_period: int = 14
    realized_volatility_period: int = 20
    max_quote_age_seconds: int = 300
    bar_interval_seconds: int = 60

    def __post_init__(self):
        if any(getattr(self, n) <= 0 for n in ("open_window_minutes", "close_window_minutes", "transition_minutes", "rollover_minutes", "weekly_window_minutes", "minimum_reference_samples", "reference_max_age_minutes", "atr_period", "realized_volatility_period", "max_quote_age_seconds", "bar_interval_seconds")):
            raise ValueError("POSITIVE_CONFIGURATION_REQUIRED")
        if {s.name for s in self.sessions} != {"ASIA", "LONDON", "NEW_YORK"} or len(self.sessions) != 3:
            raise ValueError("THREE_UNIQUE_SESSIONS_REQUIRED")
        for thresholds in (self.spread_percentiles, self.volatility_percentiles):
            if list(thresholds) != sorted(set(thresholds)) or not all(0 < x < 100 for x in thresholds):
                raise ValueError("ORDERED_PERCENTILES_REQUIRED")


@dataclass(frozen=True)
class Sample:
    symbol: str
    timestamp: datetime
    available_at: datetime
    value: float
    session: str


@dataclass(frozen=True)
class Bar:
    symbol: str
    close_time: datetime
    available_at: datetime
    high: float
    low: float
    close: float


def _intervals(t, config):
    intervals = []
    for session in config.sessions:
        zone = ZoneInfo(session.zone)
        day = t.astimezone(zone).date()
        for offset in range(-8, 9):
            d = day + timedelta(days=offset)
            if d.weekday() >= 5:
                continue
            start = datetime.combine(d, session.open, zone).astimezone(UTC)
            endday = d + timedelta(days=int(session.close <= session.open))
            end = datetime.combine(endday, session.close, zone).astimezone(UTC)
            intervals.append((start, end, session.name))
    return sorted(intervals)


def _label(t, intervals, config):
    active = {name for start, end, name in intervals if start <= t < end}
    ny = t.astimezone(ZoneInfo("America/New_York"))
    roll = abs((ny - datetime.combine(ny.date(), time(17), ny.tzinfo)).total_seconds()) < config.rollover_minutes * 60
    if roll and ny.weekday() < 5:
        return "ROLLOVER"
    if {"LONDON", "NEW_YORK"} <= active:
        return "LONDON_NEW_YORK_OVERLAP"
    if {"ASIA", "LONDON"} <= active:
        return "ASIA_LONDON_TRANSITION"
    # Also describe the short gap leading into London, if present.
    london = [start for start, end, name in intervals if name == "LONDON" and start > t]
    if "ASIA" in active and london and (min(london)-t).total_seconds() <= config.transition_minutes*60:
        return "ASIA_LONDON_TRANSITION"
    for name in ("LONDON", "NEW_YORK", "ASIA"):
        if name in active:
            return name + "_ONLY"
    return "OFF_MARKET"


def _calendar(t, schedule, config):
    zone = ZoneInfo(schedule.zone)
    local = t.astimezone(zone)
    monday = local.date() - timedelta(days=local.weekday())
    opens, closes = [], []
    for w in range(-2, 3):
        base = monday + timedelta(weeks=w)
        opens.append(datetime.combine(base+timedelta(days=schedule.weekly_open_day), schedule.weekly_open, zone).astimezone(UTC))
        closes.append(datetime.combine(base+timedelta(days=schedule.weekly_close_day), schedule.weekly_close, zone).astimezone(UTC))
    last_open = max(x for x in opens if x <= t)
    last_close = max(x for x in closes if x <= t)
    nominal = last_open > last_close and local.date() not in schedule.closed_dates
    if schedule.daily_break:
        a, b = schedule.daily_break
        tm = local.time().replace(tzinfo=None)
        nominal &= not (a <= tm < b if a < b else tm >= a or tm < b)
    return {
        "is_market_open": nominal if schedule.verified else None,
        "nominal_market_open": nominal,
        "calendar_verified": schedule.verified,
        "calendar_evidence": schedule.evidence,
        "is_near_weekly_open": any(abs((t-x).total_seconds()) <= config.weekly_window_minutes*60 for x in opens),
        "is_near_weekly_close": any(abs((t-x).total_seconds()) <= config.weekly_window_minutes*60 for x in closes),
    }


def _reference(samples, t, symbol, session, config):
    # Strictly earlier timestamps prevent the current value contaminating its baseline.
    return [s.value for s in samples if s.symbol == symbol and s.session == session
            and t-timedelta(minutes=config.reference_max_age_minutes) <= utc(s.timestamp) < t
            and utc(s.timestamp) <= utc(s.available_at) <= t and math.isfinite(s.value) and s.value >= 0]


def _relative(value, values, minimum):
    if value is None or len(values) < minimum:
        return None, None
    median = statistics.median(values)
    percentile = 100 * (sum(x < value for x in values)+0.5*sum(x == value for x in values)) / len(values)
    return percentile, value / median if median > 0 else None


def context(timestamp, symbol, *, schedule=None, config=Config(), broker_time=None,
            bid=None, ask=None, quote_time=None, quote_available_at=None,
            spread_history=(), volatility_history=(), bars=()):
    t = utc(timestamp)
    symbol = symbol.upper()
    schedule = schedule or INSTRUMENTS[symbol]
    if schedule.symbol != symbol:
        raise ValueError("INSTRUMENT_SCHEDULE_MISMATCH")
    intervals = _intervals(t, config)
    active = [(a,b,n) for a,b,n in intervals if a <= t < b]
    label = _label(t, intervals, config)
    calendar = _calendar(t, schedule, config)
    if calendar["is_market_open"] is False:
        label = "CLOSED"
    edges = sorted({x for a,b,n in intervals for x in (a,b)} |
                   {a-timedelta(minutes=config.transition_minutes) for a,b,n in intervals if n == "LONDON"} |
                   {datetime.combine((t+timedelta(days=d)).astimezone(ZoneInfo("America/New_York")).date(), time(17), ZoneInfo("America/New_York")).astimezone(UTC)+timedelta(minutes=m) for d in range(-8,9) for m in (-config.rollover_minutes,config.rollover_minutes)})
    if schedule.verified:
        zone = ZoneInfo(schedule.zone)
        extra = set()
        for offset in range(-8,9):
            day = t.astimezone(zone).date()+timedelta(days=offset)
            times = [time(0)]
            if day.weekday() == schedule.weekly_open_day: times.append(schedule.weekly_open)
            if day.weekday() == schedule.weekly_close_day: times.append(schedule.weekly_close)
            if schedule.daily_break: times.extend(schedule.daily_break)
            extra.update(datetime.combine(day,tm,zone).astimezone(UTC) for tm in times)
        edges = sorted(set(edges)|extra)
    def state(at):
        return "CLOSED" if _calendar(at,schedule,config)["is_market_open"] is False else _label(at,intervals,config)
    previous, next_label = None, None
    for edge in reversed([x for x in edges if x <= t]):
        candidate = state(edge-timedelta(microseconds=1))
        if candidate != label: previous = candidate; break
    next_edge = None
    for edge in [x for x in edges if x > t]:
        candidate = state(edge)
        if candidate != label: next_label = candidate; next_edge = edge; break
    age = min(((t-a).total_seconds()/60 for a,b,n in active), default=None)
    remaining = min(((b-t).total_seconds()/60 for a,b,n in active), default=None)
    names = {n for a,b,n in active}
    opened = lambda name: any(n == name and 0 <= (t-a).total_seconds()/60 < config.open_window_minutes for a,b,n in active)
    spread = None
    if bid is not None and ask is not None and quote_time is not None and quote_available_at is not None:
        if t-timedelta(seconds=config.max_quote_age_seconds) <= utc(quote_time) <= utc(quote_available_at) <= t and math.isfinite(bid) and math.isfinite(ask) and 0 < bid <= ask:
            spread = ask-bid
    refs = _reference(spread_history,t,symbol,label,config)
    sp, ratio = _relative(spread,refs,config.minimum_reference_samples)
    spread_state = "UNKNOWN" if sp is None else "EXTREME" if sp >= config.spread_percentiles[1] else "ELEVATED" if sp >= config.spread_percentiles[0] else "NORMAL"
    eligible = sorted([b for b in bars if b.symbol == symbol and utc(b.close_time) <= utc(b.available_at) <= t and all(math.isfinite(x) and x > 0 for x in (b.high,b.low,b.close)) and b.low <= b.close <= b.high],key=lambda b:utc(b.close_time))
    if len({utc(b.close_time) for b in eligible}) != len(eligible):
        raise ValueError("DUPLICATE_BAR_CLOSE_TIME")
    atr = None
    def regular(window):
        return all((utc(b.close_time)-utc(a.close_time)).total_seconds() == config.bar_interval_seconds for a,b in zip(window,window[1:]))
    if len(eligible) >= config.atr_period+1:
        window = eligible[-config.atr_period-1:]
        if regular(window):
            atr = statistics.mean(max(b.high-b.low,abs(b.high-prev.close),abs(b.low-prev.close)) for prev,b in zip(window,window[1:]))
    rv = None
    if len(eligible) >= config.realized_volatility_period+1:
        window = eligible[-config.realized_volatility_period-1:]
        if regular(window):
            rv = statistics.pstdev(math.log(b.close/a.close) for a,b in zip(window,window[1:]))
    vr = _reference(volatility_history,t,symbol,label,config)
    vp, vratio = _relative(rv,vr,config.minimum_reference_samples)
    volstate = "UNKNOWN" if vp is None else "LOW" if vp < config.volatility_percentiles[0] else "NORMAL" if vp < config.volatility_percentiles[1] else "HIGH" if vp < config.volatility_percentiles[2] else "EXTREME"
    seconds = t.hour*3600+t.minute*60+t.second+t.microsecond/1e6
    return dict(version=VERSION, source_timestamp=timestamp.isoformat() if isinstance(timestamp,datetime) else timestamp,
                timestamp_utc=t.isoformat(),broker_time=broker_time,instrument=symbol,asset_class=schedule.asset_class,
                day_of_week=t.weekday(),hour_utc=t.hour,minute_utc=t.minute,seconds_from_midnight=seconds,
                week_of_year=t.isocalendar().week,iso_week_year=t.isocalendar().year,month=t.month,quarter=(t.month-1)//3+1,
                is_monday=t.weekday()==0,is_friday=t.weekday()==4,is_weekend=t.weekday()>=5,
                current_session=label,previous_session=previous,next_session=next_label,
                active_sessions=sorted(names),minutes_since_session_open=age,minutes_until_session_close=remaining,
                minutes_until_next_session=(next_edge-t).total_seconds()/60 if next_edge else None,
                is_session_open_window=any(opened(n) for n in names),is_session_close_window=remaining is not None and remaining <= config.close_window_minutes,
                is_london_open_window=opened("LONDON"),is_new_york_open_window=opened("NEW_YORK"),
                is_london_ny_overlap={"LONDON","NEW_YORK"}<=names,is_rollover_window=_label(t,intervals,config)=="ROLLOVER",
                session_asia=int("ASIA" in names),session_london=int("LONDON" in names),session_new_york=int("NEW_YORK" in names),session_overlap=int(len(names)>1),
                hour_sin=math.sin(2*math.pi*seconds/86400),hour_cos=math.cos(2*math.pi*seconds/86400),
                current_spread=spread,spread_percentile=sp,spread_vs_session_median=ratio,spread_state=spread_state,
                atr=atr,short_term_realized_volatility=rv,session_relative_volatility=vratio,volatility_percentile=vp,volatility_state=volstate,
                spread_reference_samples=len(refs),volatility_reference_samples=len(vr),
                liquidity_assumption="UNVALIDATED_INSTRUMENT_SPECIFIC",**calendar)


def status_view(features):
    """Informational adapter for later monitoring integration; no live clock calls."""
    keys = ("timestamp_utc","broker_time","instrument","is_market_open","calendar_verified","current_session",
            "minutes_since_session_open","next_session","is_london_ny_overlap","spread_state","volatility_state")
    return {key: features[key] for key in keys}
