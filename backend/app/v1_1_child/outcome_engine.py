"""Deterministic, candle-only RR outcome resolution for paper trade contracts."""
from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

MAX_HOLD_M1 = 60
RR_MULTIPLIERS = {"RR_2": Decimal("2"), "RR_3": Decimal("3"), "RR_5": Decimal("5"), "RR_10": Decimal("10")}


def _decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"INVALID_{field.upper()}")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"INVALID_{field.upper()}") from error
    if not number.is_finite():
        raise ValueError(f"INVALID_{field.upper()}")
    return number


def _field(contract: Mapping[str, Any] | Any, name: str) -> Any:
    if isinstance(contract, Mapping):
        try:
            return contract[name]
        except KeyError as error:
            raise ValueError(f"MISSING_{name.upper()}") from error
    try:
        return getattr(contract, name)
    except AttributeError as error:
        raise ValueError(f"MISSING_{name.upper()}") from error


def _validated_contract(contract: Mapping[str, Any] | Any, rr_profile: str) -> tuple[str, Decimal, Decimal, Decimal, Decimal]:
    if rr_profile not in RR_MULTIPLIERS:
        raise ValueError("UNSUPPORTED_RR_PROFILE")
    decision = _field(contract, "decision")
    if decision not in {"BUY", "SELL"}:
        raise ValueError("INVALID_DECISION")
    entry = _decimal(_field(contract, "entry_price"), "entry_price")
    stop = _decimal(_field(contract, "stop_price"), "stop_price")
    risk = _decimal(_field(contract, "risk_distance"), "risk_distance")
    maximum = _decimal(_field(contract, "max_hold_m1"), "max_hold_m1")
    target = _decimal(_field(contract, f"{rr_profile.lower()}_target"), "target_price")
    if risk <= 0 or maximum <= 0:
        raise ValueError("INVALID_RISK_OR_MAX_HOLD")
    if decision == "BUY" and not (stop < entry < target):
        raise ValueError("INVALID_BUY_LEVELS")
    if decision == "SELL" and not (target < entry < stop):
        raise ValueError("INVALID_SELL_LEVELS")
    return decision, entry, stop, risk, target


def _validated_candle(
    candle: Any, previous_timestamp: str | None
) -> tuple[str, Decimal, Decimal, Decimal, Decimal]:
    if not isinstance(candle, Mapping) or {"timestamp", "open", "high", "low", "close"} - candle.keys():
        raise ValueError("INVALID_CANDLE")
    timestamp = candle["timestamp"]
    if not isinstance(timestamp, str) or not timestamp:
        raise ValueError("INVALID_CANDLE_TIMESTAMP")
    if previous_timestamp is not None and timestamp <= previous_timestamp:
        raise ValueError("CANDLES_NOT_CHRONOLOGICAL")
    open_price = _decimal(candle["open"], "candle_open")
    high = _decimal(candle["high"], "candle_high")
    low = _decimal(candle["low"], "candle_low")
    close = _decimal(candle["close"], "candle_close")
    if low > high or not low <= open_price <= high or not low <= close <= high:
        raise ValueError("INVALID_CANDLE_OHLC")
    return timestamp, open_price, high, low, close


def resolve_rr_outcome(
    contract: Mapping[str, Any] | Any, rr_profile: str, future_candles: Any
) -> dict[str, Any]:
    """Resolve one RR profile from chronological completed future M1 candles only."""
    decision, entry, stop, risk, target = _validated_contract(contract, rr_profile)
    try:
        iterator = iter(future_candles)
    except TypeError as error:
        raise ValueError("INVALID_CANDLES") from error

    previous_timestamp: str | None = None
    for index in range(1, MAX_HOLD_M1 + 1):
        try:
            candle = next(iterator)
        except StopIteration:
            return {"rr_profile": rr_profile, "target_price": target, "outcome": "UNRESOLVED",
                    "exit_timestamp": None, "exit_price": None, "holding_candles": index - 1,
                    "gross_r": None, "ambiguous_intrabar": False}
        timestamp, _open, high, low, close = _validated_candle(candle, previous_timestamp)
        previous_timestamp = timestamp
        stop_hit = low <= stop if decision == "BUY" else high >= stop
        target_hit = high >= target if decision == "BUY" else low <= target
        base = {"rr_profile": rr_profile, "target_price": target, "exit_timestamp": timestamp,
                "holding_candles": index}
        if stop_hit:
            return {**base, "outcome": "SL_HIT", "exit_price": stop, "gross_r": Decimal("-1"),
                    "ambiguous_intrabar": target_hit}
        if target_hit:
            return {**base, "outcome": "TP_HIT", "exit_price": target,
                    "gross_r": RR_MULTIPLIERS[rr_profile], "ambiguous_intrabar": False}
    gross_r = (close - entry) / risk if decision == "BUY" else (entry - close) / risk
    return {"rr_profile": rr_profile, "target_price": target, "outcome": "EXPIRED",
            "exit_timestamp": timestamp, "exit_price": close, "holding_candles": MAX_HOLD_M1,
            "gross_r": gross_r, "ambiguous_intrabar": False}
