from __future__ import annotations

from decimal import Decimal
from typing import Any

from .demo_shadow import STARTING_BALANCE_USD


def report(rows: list[dict[str, Any]], profile: str, rr: str, cost: str) -> dict[str, Any]:
    x = [
        r
        for r in rows
        if r.get("record_type") == "SHADOW_OUTCOME"
        and (r.get("profile"), r.get("rr_profile"), r.get("cost_profile")) == (profile, rr, cost)
    ]
    values = [Decimal(str(r["net_r"])) for r in x]
    net = sum(values, Decimal())
    losses = [v for v in values if v < 0]
    wins = [v for v in values if v > 0]
    peak = eq = dd = Decimal()
    streak = longest = 0
    for value in values:
        eq += value
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
        streak = streak + 1 if value < 0 else 0
        longest = max(longest, streak)
    return {
        "trades": len(x),
        "wins": len(wins),
        "losses": len(losses),
        "expired": sum(r.get("outcome") == "EXPIRED" for r in x),
        "gross_r": sum((Decimal(str(r["gross_r"])) for r in x), Decimal()),
        "net_r": net,
        "expectancy": net / len(x) if x else None,
        "profit_factor": sum(wins, Decimal()) / -sum(losses, Decimal()) if losses else None,
        "max_drawdown_r": dd,
        "max_losing_streak": longest,
        "starting_balance": STARTING_BALANCE_USD,
        "current_balance": Decimal(str(x[-1]["balance_after"])) if x else STARTING_BALANCE_USD,
        "net_profit": Decimal(str(x[-1]["balance_after"])) - STARTING_BALANCE_USD
        if x
        else Decimal(),
    }
