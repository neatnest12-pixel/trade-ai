"""Generic research-integrity primitives; no trading or MT5 execution."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class Event:
    event_id: str
    event_start: datetime
    event_end: datetime

def purge_events(events: list[Event], boundary: datetime) -> tuple[list[Event], int]:
    kept=[e for e in events if e.event_end < boundary]
    return kept, len(events)-len(kept)

def embargo_events(events: list[Event], boundary: datetime, bars: int, bar_minutes: int=1) -> tuple[list[Event], int]:
    cutoff=boundary+timedelta(minutes=bars*bar_minutes)
    kept=[e for e in events if e.event_start >= cutoff]
    return kept, len(events)-len(kept)

def canonical_event_id(symbol: str, timeframe: str, entry_timestamp: str, direction: str, entry: float, sl: float, tp: float, horizon: int) -> str:
    raw=f"{symbol}|{timeframe}|{entry_timestamp}|{direction}|{entry:.5f}|{sl:.5f}|{tp:.5f}|{horizon}"
    return hashlib.sha256(raw.encode()).hexdigest()[:20]
