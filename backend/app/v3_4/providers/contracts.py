from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class Quality(StrEnum):
    VERIFIED = "VERIFIED"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNAVAILABLE = "UNAVAILABLE"


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def unavailable(reason: str) -> dict[str, Any]:
    return {"state": "UNAVAILABLE", "reason": reason, "quality": Quality.UNAVAILABLE.value}


@dataclass(frozen=True)
class CommissionRecord:
    account_id_hash: str
    server: str
    symbol: str
    deal_id: str
    timestamp: datetime
    volume: float
    commission: float | None
    commission_currency: str | None
    commission_per_lot: float | None
    source: str
    retrieved_at: datetime
    quality: Quality

    def normalized(self) -> dict[str, Any]:
        if self.commission is None:
            return unavailable("COMMISSION_UNAVAILABLE")
        data = asdict(self)
        data["timestamp"] = _utc(self.timestamp).isoformat()
        data["retrieved_at"] = _utc(self.retrieved_at).isoformat()
        data["quality"] = self.quality.value
        return data


def execution_quality_contract() -> dict[str, Any]:
    return {
        "signal_timestamp": None,
        "decision_timestamp": None,
        "order_send_timestamp": None,
        "broker_ack_timestamp": None,
        "fill_timestamp": None,
        "requested_price": None,
        "quote_at_signal": None,
        "quote_at_send": None,
        "actual_fill": None,
        "slippage_points": None,
        "slippage_pips": None,
        "slippage_R": None,
        "latency_signal_to_send_ms": None,
        "latency_send_to_ack_ms": None,
        "latency_ack_to_fill_ms": None,
        "latency_total_ms": None,
        "broker_retcode": None,
        "order_sent": False,
    }


@dataclass(frozen=True)
class MacroEventRecord:
    event_id: str
    event_timestamp_utc: datetime
    country: str
    currency: str
    event_type: str
    importance: str
    actual: float | None
    forecast: float | None
    previous: float | None
    revised_previous: float | None
    release_status: str
    source: str
    retrieved_at: datetime
    quality: Quality

    def normalized(self) -> dict[str, Any]:
        data = asdict(self)
        data["event_timestamp_utc"] = _utc(self.event_timestamp_utc).isoformat()
        data["retrieved_at"] = _utc(self.retrieved_at).isoformat()
        data["quality"] = self.quality.value
        data["staleness"] = (_utc(self.retrieved_at) - _utc(self.event_timestamp_utc)).total_seconds()
        return data


def economic_surprise(actual: float | None, forecast: float | None, sample_size: int = 0, dispersion: float | None = None) -> dict[str, Any]:
    if actual is None or forecast is None:
        return unavailable("SURPRISE_UNAVAILABLE")
    surprise = actual - forecast
    normalized = surprise / dispersion if dispersion and sample_size >= 30 else "UNAVAILABLE_INSUFFICIENT_HISTORY"
    return {"surprise_absolute": surprise, "normalized_surprise": normalized}


def central_bank_rate_record(central_bank: str, currency: str, policy_rate: float | None, effective_timestamp: datetime, previous_rate: float | None, source: str) -> dict[str, Any]:
    change_bps = None if policy_rate is None or previous_rate is None else (policy_rate - previous_rate) * 100
    state = "UNKNOWN" if change_bps is None else "TIGHTENING" if change_bps > 0 else "EASING" if change_bps < 0 else "NEUTRAL"
    return {"central_bank": central_bank, "currency": currency, "policy_rate": policy_rate, "effective_timestamp": _utc(effective_timestamp).isoformat(), "previous_rate": previous_rate, "change_bps": change_bps, "policy_state": state, "source": source, "retrieved_at": datetime.now(UTC).isoformat(), "quality": Quality.UNAVAILABLE.value if policy_rate is None else Quality.VERIFIED.value}


def yield_record(instrument_id: str, timestamp: datetime, value: float | None, source: str) -> dict[str, Any]:
    return {"instrument_id": instrument_id, "timestamp": _utc(timestamp).isoformat(), "yield": value, "change_1d": None, "change_5d": None, "source": source, "quality": Quality.UNAVAILABLE.value if value is None else Quality.VERIFIED.value}


def cross_asset_record(instrument: str, timestamp: datetime, price: float | None, source: str, max_age_seconds: int = 300, decision_timestamp: datetime | None = None) -> dict[str, Any]:
    decision = _utc(decision_timestamp or datetime.now(UTC))
    age = (decision - _utc(timestamp)).total_seconds()
    stale = age > max_age_seconds
    return {"instrument": instrument, "timestamp": _utc(timestamp).isoformat(), "bid_ask_or_last": price, "return_5m": None, "return_15m": None, "return_1h": None, "return_1d": None, "source": source, "quality": Quality.UNAVAILABLE.value if price is None or stale else Quality.VERIFIED.value, "stale": stale}


def futures_record(timestamp: datetime, price: float | None = None, volume: float | None = None, open_interest: float | None = None, source: str = "UNAVAILABLE") -> dict[str, Any]:
    return {"price": price, "volume": volume, "open_interest": open_interest, "timestamp": _utc(timestamp).isoformat(), "source": source, "warning": "FUTURES_VOLUME != GLOBAL_SPOT_FX_VOLUME"}


def cot_record(reference_date: datetime, release_timestamp: datetime, decision_timestamp: datetime, market: str) -> dict[str, Any]:
    available = _utc(decision_timestamp) >= _utc(release_timestamp)
    return {"reference_date": _utc(reference_date).date().isoformat(), "release_timestamp": _utc(release_timestamp).isoformat(), "market": market, "category": None, "long": None, "short": None, "net": None, "weekly_change": None, "percentile": None, "source": "UNAVAILABLE", "available_at_decision": available}


def value_macro_contract() -> dict[str, Any]:
    return {"inflation_differential": "UNAVAILABLE", "real_exchange_rate_inputs": "UNAVAILABLE", "PPP_like_value": "UNAVAILABLE", "terms_of_trade_proxy": "UNAVAILABLE", "state": "VALUE_UNAVAILABLE"}
