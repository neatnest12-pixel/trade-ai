"""Deterministic, read-only ₹25 paper position-sizing engine."""
from __future__ import annotations

from collections.abc import Mapping
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Any

REFERENCE_RISK_INR = Decimal("25")


def _number(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise ValueError
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError from error
    if not result.is_finite():
        raise ValueError
    return result


def _result(**values: Any) -> dict[str, Any]:
    base = {
        "decision": None, "entry_price": None, "stop_price": None, "risk_distance": None,
        "reference_risk_inr": REFERENCE_RISK_INR, "account_currency": None,
        "conversion_rate_used": None, "tick_size": None, "tick_value": None,
        "contract_size": None, "volume_min": None, "volume_step": None, "volume_max": None,
        "loss_per_lot_account_currency": None, "raw_volume": None, "final_volume": None,
        "actual_risk_account_currency": None, "actual_risk_inr": None, "status": "SKIPPED",
        "reason": "RISK_METADATA_UNAVAILABLE",
    }
    base.update(values)
    return base


def _metadata(metadata: Mapping[str, Any]) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal, Decimal]:
    try:
        point = _number(metadata["point"])
        tick_size = _number(metadata["trade_tick_size"])
        tick_value = _number(metadata["trade_tick_value"])
        contract_size = _number(metadata["trade_contract_size"])
        volume_min = _number(metadata["volume_min"])
        volume_step = _number(metadata["volume_step"])
        volume_max = _number(metadata["volume_max"])
    except (KeyError, ValueError) as error:
        raise ValueError("RISK_METADATA_UNAVAILABLE") from error
    if point <= 0 or tick_size <= 0 or tick_value <= 0 or contract_size <= 0:
        raise ValueError("RISK_METADATA_UNAVAILABLE")
    if volume_min <= 0 or volume_step <= 0 or volume_max < volume_min:
        raise ValueError("INVALID_VOLUME_METADATA")
    return tick_size, tick_value, contract_size, volume_min, volume_step, volume_max


def _inr_to_account_rate(
    account_currency: str, inr_to_account_currency_rate: Any, account_currency_to_inr_rate: Any
) -> Decimal:
    if account_currency == "INR":
        return Decimal("1")
    supplied = [rate for rate in (inr_to_account_currency_rate, account_currency_to_inr_rate) if rate is not None]
    if len(supplied) != 1:
        raise ValueError("RISK_FX_CONVERSION_UNAVAILABLE")
    try:
        rate = _number(supplied[0])
    except ValueError as error:
        raise ValueError("RISK_FX_CONVERSION_UNAVAILABLE") from error
    if rate <= 0:
        raise ValueError("RISK_FX_CONVERSION_UNAVAILABLE")
    return rate if inr_to_account_currency_rate is not None else Decimal("1") / rate


def size_paper_position(
    entry_price: Any,
    stop_price: Any,
    decision: str,
    symbol_metadata: Mapping[str, Any],
    account_currency: str,
    *,
    inr_to_account_currency_rate: Any = None,
    account_currency_to_inr_rate: Any = None,
) -> dict[str, Any]:
    """Size a paper position without exceeding ₹25 theoretical stop loss.

    For non-INR accounts exactly one externally supplied conversion rate is
    required. ``inr_to_account_currency_rate`` is normalized for the result.
    """
    try:
        entry = _number(entry_price)
        stop = _number(stop_price)
    except ValueError:
        return _result(decision=decision, account_currency=account_currency, reason="INVALID_ENTRY_STOP")
    if decision not in {"BUY", "SELL"} or not isinstance(account_currency, str) or not account_currency:
        return _result(decision=decision, entry_price=entry, stop_price=stop, account_currency=account_currency, reason="INVALID_ENTRY_STOP")
    distance = abs(entry - stop)
    if distance <= 0 or (decision == "BUY" and stop >= entry) or (decision == "SELL" and stop <= entry):
        return _result(decision=decision, entry_price=entry, stop_price=stop, risk_distance=distance, account_currency=account_currency, reason="INVALID_ENTRY_STOP")
    try:
        tick_size, tick_value, contract_size, volume_min, volume_step, volume_max = _metadata(symbol_metadata)
    except ValueError as error:
        return _result(decision=decision, entry_price=entry, stop_price=stop, risk_distance=distance, account_currency=account_currency, reason=str(error))
    base = {"decision": decision, "entry_price": entry, "stop_price": stop, "risk_distance": distance,
            "account_currency": account_currency, "tick_size": tick_size, "tick_value": tick_value,
            "contract_size": contract_size, "volume_min": volume_min, "volume_step": volume_step,
            "volume_max": volume_max}
    try:
        conversion = _inr_to_account_rate(account_currency, inr_to_account_currency_rate, account_currency_to_inr_rate)
    except ValueError as error:
        return _result(**base, reason=str(error))
    loss_per_lot = distance / tick_size * tick_value
    allowed_account = REFERENCE_RISK_INR * conversion
    raw_volume = allowed_account / loss_per_lot
    final_volume = (raw_volume / volume_step).to_integral_value(rounding=ROUND_DOWN) * volume_step
    if final_volume < volume_min:
        final_volume = volume_min
    if final_volume > volume_max:
        final_volume = volume_max
    actual_account = final_volume * loss_per_lot
    actual_inr = actual_account / conversion
    values = {**base, "conversion_rate_used": conversion, "loss_per_lot_account_currency": loss_per_lot,
              "raw_volume": raw_volume, "final_volume": final_volume,
              "actual_risk_account_currency": actual_account, "actual_risk_inr": actual_inr}
    if actual_inr > REFERENCE_RISK_INR + Decimal("0.000000001"):
        reason = "RISK_TOO_SMALL_FOR_MIN_LOT" if final_volume == volume_min else "RISK_METADATA_UNAVAILABLE"
        return _result(**values, reason=reason)
    return _result(**values, status="READY", reason="NONE")
