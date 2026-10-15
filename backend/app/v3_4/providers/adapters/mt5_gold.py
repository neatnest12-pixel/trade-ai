from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.core.mt5_offset import configured_offset_seconds
from app.v3_4.mt5_time import (
    ClockSkewError,
    normalize_mt5_epoch,
    require_not_future,
)
from app.v3_4.providers.contracts import Quality


@dataclass(frozen=True)
class Mt5GoldTick:
    instrument_id: str
    timestamp: datetime
    raw_timestamp: datetime
    bid: float
    ask: float
    source: str = "mt5_icmarkets_demo_xauusd"
    normalization: str = "MT5_RAW_UTC"

    @property
    def price(self) -> float:
        return (self.bid + self.ask) / 2


class Mt5GoldAdapter:
    provider_id = "mt5_icmarkets_demo_xauusd"
    instrument_id = "XAUUSD"
    max_age_seconds = 300

    def fetch_latest(self) -> Mt5GoldTick | dict[str, Any]:
        try:
            import MetaTrader5 as mt5  # type: ignore[import-not-found]
        except Exception as exc:
            return self._unavailable(f"MT5_IMPORT_FAILED:{type(exc).__name__}")

        initialized = mt5.initialize()
        if not initialized:
            return self._unavailable("MT5_INITIALIZE_FAILED")
        try:
            info = mt5.symbol_info(self.instrument_id)
            tick = mt5.symbol_info_tick(self.instrument_id)
            if info is None or tick is None:
                return self._unavailable("XAUUSD_UNAVAILABLE")
            bid = float(getattr(tick, "bid", 0.0) or 0.0)
            ask = float(getattr(tick, "ask", 0.0) or 0.0)
            if bid <= 0 or ask <= 0 or ask < bid:
                return self._unavailable("INVALID_XAUUSD_BID_ASK")
            raw_time_msc = getattr(tick, "time_msc", None)
            epoch = float(raw_time_msc) / 1000 if raw_time_msc else float(tick.time)
            try:
                normalized = normalize_mt5_epoch(
                    epoch,
                    offset_seconds=configured_offset_seconds(),
                    source="XAUUSD_SYMBOL_INFO_TICK",
                )
                require_not_future(
                    normalized.timestamp,
                    system_utc=datetime.now(UTC),
                    source=normalized.source,
                )
            except ClockSkewError:
                return self._unavailable("CLOCK_SKEW_DETECTED")
            except RuntimeError as exc:
                return self._unavailable(str(exc))
            return Mt5GoldTick(
                self.instrument_id,
                normalized.timestamp,
                normalized.raw_timestamp,
                bid,
                ask,
                normalization=normalized.normalization,
            )
        finally:
            mt5.shutdown()

    def normalized(self) -> dict[str, Any]:
        tick = self.fetch_latest()
        if isinstance(tick, dict):
            return tick
        retrieved_at = datetime.now(UTC)
        age = (retrieved_at - tick.timestamp).total_seconds()
        stale = age > self.max_age_seconds
        return {
            "instrument_id": tick.instrument_id,
            "timestamp": tick.timestamp.isoformat(),
            "raw_timestamp": tick.raw_timestamp.isoformat(),
            "timestamp_normalization": tick.normalization,
            "price": tick.price,
            "bid": tick.bid,
            "ask": tick.ask,
            "spread": tick.ask - tick.bid,
            "return_5m": None,
            "return_15m": None,
            "return_1h": None,
            "return_1d": None,
            "source": tick.source,
            "retrieved_at": retrieved_at.isoformat(),
            "quality": Quality.UNAVAILABLE.value if stale else Quality.VERIFIED.value,
            "stale": stale,
            "staleness_seconds": age,
        }

    def _unavailable(self, reason: str) -> dict[str, Any]:
        return {
            "instrument_id": self.instrument_id,
            "state": "UNAVAILABLE",
            "reason": reason,
            "source": self.provider_id,
            "quality": Quality.UNAVAILABLE.value,
        }
