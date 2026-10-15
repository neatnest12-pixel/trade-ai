from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.v1_1.contract import STARTING_BALANCE, TradeOutcome


def metrics(outcomes: list[TradeOutcome], starting_balance: Decimal = STARTING_BALANCE) -> dict[str, Any]:
    ambiguous = sum(item.status == "AMBIGUOUS_INTRABAR" for item in outcomes)
    closed = [item for item in outcomes if item.realized_r is not None]
    values = [item.realized_r for item in closed if item.realized_r is not None]
    if not values:
        return {"total_trades": 0, "ambiguous": ambiguous, "expectancy_r": "NOT_AVAILABLE_FROM_CURRENT_SCHEMA"}
    wins, losses = [x for x in values if x > 0], [x for x in values if x < 0]
    gross_profit, gross_loss = sum(wins, Decimal()), -sum(losses, Decimal())
    balances = [starting_balance] + [x.balance_after_trade for x in closed if x.balance_after_trade is not None]
    peak, drawdown = starting_balance, Decimal()
    for balance in balances[1:]:
        peak = max(peak, balance); drawdown = max(drawdown, peak - balance)
    current = balances[-1]
    return {"total_trades": len(values), "wins": len(wins), "losses": len(losses), "breakeven": sum(x == 0 for x in values), "ambiguous": ambiguous, "win_rate": Decimal(len(wins)) / len(values), "gross_profit_r": gross_profit, "gross_loss_r": gross_loss, "net_r": sum(values, Decimal()), "expectancy_r": sum(values, Decimal()) / len(values), "profit_factor": gross_profit / gross_loss if gross_loss else "NOT_AVAILABLE_FROM_CURRENT_SCHEMA", "max_drawdown_rupees": drawdown, "starting_balance": starting_balance, "current_balance": current, "peak_balance": peak, "return_percentage": (current - starting_balance) / starting_balance * 100}

