"""Isolated V1.1 simulated trade-economics contract; never places broker orders."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN

STARTING_BALANCE = Decimal("2500")
MAX_RISK_RUPEES = Decimal("25")
RISK_PERCENTAGE = Decimal("0.01")


@dataclass(frozen=True)
class TradePlan:
    prediction_hash: str
    decision: str
    entry_price: Decimal | None
    stop_loss: Decimal | None
    target_price: Decimal | None
    risk_distance: Decimal | None
    reward_distance: Decimal | None
    planned_rr: Decimal | None
    risk_rupees: Decimal | None
    position_size_simulated: Decimal | None


def plan_trade(*, prediction_hash: str, decision: str, entry_price: Decimal, atr: Decimal, balance: Decimal) -> TradePlan:
    """Freeze a 1R stop and 2R target; NO_TRADE creates no simulated position."""
    if decision == "NO_TRADE":
        return TradePlan(prediction_hash, decision, None, None, None, None, None, None, None, None)
    if decision not in {"BUY", "SELL"} or atr <= 0:
        raise ValueError("actionable decision and positive ATR are required")
    risk = min(balance * RISK_PERCENTAGE, MAX_RISK_RUPEES)
    distance = atr
    side = Decimal("1") if decision == "BUY" else Decimal("-1")
    stop = entry_price - side * distance
    target = entry_price + side * distance * Decimal("2")
    size = (risk / distance).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
    return TradePlan(prediction_hash, decision, entry_price, stop, target, distance, distance * 2, Decimal("2"), risk, size)


def evaluate_bar(plan: TradePlan, *, high: Decimal, low: Decimal, close: Decimal) -> tuple[str, Decimal | None]:
    """Conservative same-bar policy: ambiguity is not a profitable outcome."""
    if plan.decision == "NO_TRADE":
        return "CANCELLED", None
    assert plan.stop_loss is not None and plan.target_price is not None
    stop_hit = low <= plan.stop_loss if plan.decision == "BUY" else high >= plan.stop_loss
    target_hit = high >= plan.target_price if plan.decision == "BUY" else low <= plan.target_price
    if stop_hit and target_hit:
        return "AMBIGUOUS_INTRABAR", None
    if stop_hit:
        return "STOP_LOSS", plan.stop_loss
    if target_hit:
        return "TAKE_PROFIT", plan.target_price
    return "OPEN", close
