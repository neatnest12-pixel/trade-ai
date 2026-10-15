from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class DataOrigin(StrEnum):
    HISTORICAL_RECONSTRUCTED = "HISTORICAL_RECONSTRUCTED"
    PROSPECTIVE_OBSERVED = "PROSPECTIVE_OBSERVED"

class Outcome(StrEnum):
    TP_HIT = "TP_HIT"
    SL_HIT = "SL_HIT"
    TIME_EXIT = "TIME_EXIT"
    EXPIRED = "EXPIRED"
    AMBIGUOUS = "AMBIGUOUS"
    ENTRY_UNAVAILABLE = "ENTRY_UNAVAILABLE"
    ATR_UNAVAILABLE = "ATR_UNAVAILABLE"

@dataclass(frozen=True)
class DecisionObservation:
    timestamp: datetime
    symbol: str
    bid: float
    ask: float
    spread: float
    spread_pips: float
    tick_timestamp: datetime
    tick_age_ms: float
    origin: DataOrigin = DataOrigin.PROSPECTIVE_OBSERVED

    def validate(self) -> None:
        if self.bid <= 0 or self.ask <= 0 or self.ask < self.bid:
            raise ValueError("invalid bid/ask")
        if self.tick_timestamp > self.timestamp:
            raise ValueError("future tick")

def session_bucket(hour: int) -> str:
    if 0 <= hour < 7: return "ASIA"
    if 7 <= hour < 12: return "LONDON"
    if 12 <= hour < 17: return "OVERLAP"
    if 17 <= hour < 22: return "NEW_YORK"
    return "OFF_HOURS"
