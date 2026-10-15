"""Isolated V1.2 demo-execution preparation; never starts a collector."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from typing import Any

CAMPAIGN_ID = "NAVORA-QUANT-V1.2-DEMO-PREP"
POLICY = {"account": "DEMO_ONLY", "broker": "Raw Trading Ltd", "server": "ICMarketsSC-Demo",
          "max_risk_rupees": "25", "risk_percentage": "0.01", "one_position_per_signal": True,
          "max_slippage_points": 20}
CONFIG_HASH = hashlib.sha256(json.dumps(POLICY, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
SNAPSHOT_ID = f"v1.2-demo-{CONFIG_HASH[:16]}"
DEMO_MODE = 0
REAL_MODE = 2
MAX_RISK_RUPEES = Decimal("25")
INR_PER_USD = Decimal("84")
UNITS_PER_LOT = Decimal("100000")


class ExecutionBlocked(RuntimeError):
    pass


@dataclass(frozen=True)
class OrderPlan:
    signal_id: str
    decision: str
    entry: Decimal | None
    stop: Decimal | None
    target: Decimal | None
    lot_size: Decimal | None
    expected_rr: Decimal | None
    max_slippage_points: int
    risk_rupees: Decimal | None


@dataclass(frozen=True)
class ExecutionAudit:
    signal_id: str
    order_request: dict[str, Any]
    broker_response: dict[str, Any] | None = None
    fill_price: Decimal | None = None
    slippage: Decimal | None = None
    commission: Decimal | None = None
    spread: Decimal | None = None
    open_time: str | None = None
    close_time: str | None = None
    close_reason: str | None = None
    realized_r: Decimal | None = None
    realized_pnl: Decimal | None = None


def account_eligibility(account: Any, *, broker=POLICY["broker"], server=POLICY["server"]) -> bool:
    if account is None or getattr(account, "trade_mode", None) != DEMO_MODE:
        return False
    return getattr(account, "company", None) == broker and getattr(account, "server", None) == server


def require_demo_account(account: Any) -> None:
    if not account_eligibility(account):
        raise ExecutionBlocked("V1.2 requires the pinned DEMO account")


def prepare_order(*, signal_id: str, decision: str, entry: Decimal, stop_distance: Decimal,
                  balance: Decimal, max_slippage_points: int = 20) -> OrderPlan:
    if decision == "NO_TRADE":
        return OrderPlan(signal_id, decision, None, None, None, None, None, max_slippage_points, None)
    if decision not in {"BUY", "SELL"} or entry <= 0 or stop_distance <= 0:
        raise ValueError("invalid order economics")
    risk = min(balance * Decimal("0.01"), MAX_RISK_RUPEES)
    lot = (risk / (stop_distance * UNITS_PER_LOT * INR_PER_USD)).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
    sign = Decimal("1") if decision == "BUY" else Decimal("-1")
    return OrderPlan(signal_id, decision, entry, entry - sign * stop_distance,
                     entry + sign * stop_distance * 2, lot, Decimal("2"), max_slippage_points, risk)


class DemoOrderWrapper:
    """Eligibility-only wrapper. It never submits an order during preparation."""

    def __init__(self, mt5: Any):
        self.mt5 = mt5
        self._submitted: set[str] = set()

    def prepare(self, account: Any, plan: OrderPlan) -> dict[str, Any]:
        require_demo_account(account)
        if plan.signal_id in self._submitted:
            raise ExecutionBlocked("duplicate signal")
        if plan.decision == "NO_TRADE":
            return {"action": "NO_TRADE", "signal_id": plan.signal_id}
        return {"action": "PREPARED_ONLY", "signal_id": plan.signal_id, "decision": plan.decision,
                "volume": str(plan.lot_size), "sl": str(plan.stop), "tp": str(plan.target),
                "max_slippage_points": plan.max_slippage_points}

    def mark_prepared(self, signal_id: str) -> None:
        if signal_id in self._submitted:
            raise ExecutionBlocked("duplicate signal")
        self._submitted.add(signal_id)

