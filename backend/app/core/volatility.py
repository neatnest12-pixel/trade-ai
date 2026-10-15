"""Campaign-scoped volatility boundaries for paper prediction."""
from __future__ import annotations

from dataclasses import dataclass

DEFAULT_MIN_ATR_PCT = 0.0004
DEFAULT_MAX_ATR_PCT = 0.004


@dataclass(frozen=True)
class VolatilityBounds:
    min_atr_pct: float = DEFAULT_MIN_ATR_PCT
    max_atr_pct: float = DEFAULT_MAX_ATR_PCT

    def __post_init__(self) -> None:
        if not 0 < self.min_atr_pct <= self.max_atr_pct:
            raise ValueError("volatility bounds must be positive and ordered")


LEGACY_VOLATILITY_BOUNDS = VolatilityBounds()
V1_1C_VOLATILITY_BOUNDS = VolatilityBounds(min_atr_pct=0.00008)
