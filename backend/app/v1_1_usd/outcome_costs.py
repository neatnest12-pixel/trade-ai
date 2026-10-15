"""Frozen USD cost-profile arithmetic for prospective RR outcome persistence."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

REFERENCE_RISK_USD = Decimal("0.30")
FIXED_COSTS = {"IDEALIZED": Decimal("0"), "BASE": Decimal("0.05"), "STRESS": Decimal("0.15")}


def price_outcome(outcome: dict[str, Any], profile: str, observed_spread: Any, risk_distance: Any) -> dict[str, Decimal] | None:
    """Return numeric fields, or fail closed when non-ideal spread provenance is absent."""
    gross = Decimal(str(outcome["gross_r"]))
    if profile == "IDEALIZED": spread_cost = Decimal()
    else:
        if observed_spread is None: return None
        spread, risk = Decimal(str(observed_spread)), Decimal(str(risk_distance))
        if spread < 0 or risk <= 0: return None
        spread_cost = spread / risk * (Decimal("1.5") if profile == "STRESS" else Decimal("1"))
    fixed = FIXED_COSTS[profile]; cost = spread_cost + fixed; net = gross - cost
    return {"gross_r": gross, "spread_cost_r": spread_cost, "fixed_cost_r": fixed, "cost_r": cost, "net_r": net, "simulated_pnl_usd": net * REFERENCE_RISK_USD}


def is_legacy_missing_cost(record: dict[str, Any]) -> bool:
    return record.get("record_type") == "RR_OUTCOME" and any(k not in record for k in ("spread_cost_r", "fixed_cost_r", "cost_r", "net_r", "simulated_pnl_usd"))
