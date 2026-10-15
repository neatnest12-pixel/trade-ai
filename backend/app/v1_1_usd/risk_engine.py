"""USD-only, fail-closed $0.30 paper position sizing."""
from __future__ import annotations

from collections.abc import Mapping
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Any

REFERENCE_CAPITAL_USD = Decimal("30.00")
REFERENCE_RISK_USD = Decimal("0.30")


def _d(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError("RISK_METADATA_UNAVAILABLE") from error
    if not result.is_finite() or result <= 0:
        raise ValueError("RISK_METADATA_UNAVAILABLE")
    return result


def size_usd(entry: Any, stop: Any, decision: str, metadata: Mapping[str, Any], account_currency: str) -> dict[str, Any]:
    base = {"reference_currency": "USD", "reference_capital_usd": REFERENCE_CAPITAL_USD, "reference_risk_usd": REFERENCE_RISK_USD, "account_currency": account_currency, "raw_volume": None, "final_volume": None, "actual_risk_usd": None}
    if account_currency != "USD": return {**base, "status": "SKIPPED", "reason": "ACCOUNT_CURRENCY_MISMATCH"}
    try:
        entry_d, stop_d, tick_size, tick_value, minimum, step, maximum = _d(entry), _d(stop), _d(metadata["trade_tick_size"]), _d(metadata["trade_tick_value"]), _d(metadata["volume_min"]), _d(metadata["volume_step"]), _d(metadata["volume_max"])
    except (KeyError, ValueError): return {**base, "status": "SKIPPED", "reason": "RISK_METADATA_UNAVAILABLE"}
    if decision not in {"BUY", "SELL"} or (decision == "BUY" and stop_d >= entry_d) or (decision == "SELL" and stop_d <= entry_d): return {**base, "status": "SKIPPED", "reason": "INVALID_ENTRY_STOP"}
    loss = abs(entry_d - stop_d) / tick_size * tick_value; raw = REFERENCE_RISK_USD / loss; rounded = (raw / step).to_integral_value(rounding=ROUND_DOWN) * step
    candidate = minimum if rounded < minimum else min(rounded, maximum); actual = candidate * loss
    values = {**base, "loss_per_lot_usd": loss, "raw_volume": raw, "final_volume": candidate, "actual_risk_usd": actual, "volume_min": minimum, "volume_step": step, "volume_max": maximum}
    if actual > REFERENCE_RISK_USD: return {**values, "status": "SKIPPED", "reason": "RISK_TOO_SMALL_FOR_MIN_LOT"}
    return {**values, "status": "READY", "reason": "NONE"}
