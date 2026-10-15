"""USD-policy reporting helpers, intentionally separate from INR reports."""
from decimal import Decimal
from typing import Any

from .outcome_costs import is_legacy_missing_cost

STARTING_BALANCE_USD = Decimal("30.00")
RISK_PER_TRADE_USD = Decimal("0.30")


def pnl_usd(net_r: Decimal) -> Decimal:
    return net_r * RISK_PER_TRADE_USD


def ending_balance_usd(net_r: Decimal) -> Decimal:
    return STARTING_BALANCE_USD + pnl_usd(net_r)


def official_net_outcomes(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Exclude immutable pre-fix legacy outcomes from net-cost profitability statistics."""
    return [row for row in rows if row.get("record_type") == "RR_OUTCOME" and not is_legacy_missing_cost(row)]
