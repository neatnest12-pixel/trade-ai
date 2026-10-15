from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class StrategyFamily(StrEnum):
    TREND = "TREND"
    BREAKOUT = "BREAKOUT"
    PULLBACK = "PULLBACK"
    MEAN_REVERSION = "MEAN_REVERSION"
    VOLATILITY = "VOLATILITY"
    PRICE_ACTION = "PRICE_ACTION"
    V1_FEATURE = "V1_FEATURE"

@dataclass(frozen=True)
class CandidateSignal:
    family: StrategyFamily
    side: str
    score: float
    timestamp: str

    def validate(self) -> None:
        if self.side not in {"BUY", "SELL", "NO_TRADE"}:
            raise ValueError("invalid side")
