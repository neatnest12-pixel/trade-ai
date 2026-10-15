"""V1.2 $50K demo-only shadow-risk policy. It contains no broker execution API."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

VERSION = "v1.2-demo-50k-01"
STARTING_BALANCE_USD = Decimal("50000")
POLICY: dict[str, Any] = {
    "mode": "PAPER_SHADOW_ONLY", "account": {"trade_mode": "DEMO", "server": "ICMarketsSC-Demo", "currency": "USD"},
    "reference_balance_usd": "50000", "profiles": {"PROFILE_A": "0.0025", "PROFILE_B": "0.005", "PROFILE_C": "0.01"},
    "rr": [2, 3, 5, 10], "daily_loss_limit_pct": "0.02", "consecutive_loss_limit": 5,
    "drawdown_warning_pct": "0.05", "drawdown_pause_pct": "0.10", "drawdown_review_pct": "0.15",
}
HASH = hashlib.sha256(json.dumps(POLICY, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
RISK_PROFILES = {name: Decimal(value) for name, value in POLICY["profiles"].items()}
MILESTONES = (Decimal("55000"), Decimal("60000"), Decimal("75000"), Decimal("100000"), Decimal("150000"))


class DemoSafetyBlocked(RuntimeError):
    pass


def require_demo_usd(account: Any) -> None:
    if account is None or getattr(account, "trade_mode", None) != 0 or getattr(account, "server", None) != "ICMarketsSC-Demo" or getattr(account, "currency", None) != "USD":
        raise DemoSafetyBlocked("REAL_ACCOUNT_EXECUTION_BLOCKED")


@dataclass(frozen=True)
class ShadowState:
    balance: Decimal = STARTING_BALANCE_USD
    peak: Decimal = STARTING_BALANCE_USD
    daily_loss: Decimal = Decimal()
    consecutive_losses: int = 0
    paused: bool = False
    review_required: bool = False


def risk_usd(state: ShadowState, profile: str) -> Decimal:
    return state.balance * RISK_PROFILES[profile]


def guard(state: ShadowState) -> str | None:
    if state.paused: return "PAUSED"
    if state.daily_loss >= state.balance * Decimal(POLICY["daily_loss_limit_pct"]): return "DAILY_LOSS_LIMIT_REACHED"
    if state.consecutive_losses >= POLICY["consecutive_loss_limit"]: return "CONSECUTIVE_LOSS_LIMIT_REACHED"
    drawdown = (state.peak - state.balance) / state.peak if state.peak else Decimal()
    if drawdown >= Decimal(POLICY["drawdown_pause_pct"]): return "DRAWDOWN_HARD_PAUSE"
    return None


def apply_net_r(state: ShadowState, profile: str, net_r: Decimal) -> tuple[ShadowState, dict[str, Any]]:
    """Apply a resolved shadow result; NO_TRADE is represented by ``net_r=None`` upstream and not passed here."""
    before = guard(state)
    if before: raise DemoSafetyBlocked(before)
    risk = risk_usd(state, profile); pnl = net_r * risk; balance = state.balance + pnl; peak = max(state.peak, balance)
    daily_loss = state.daily_loss + (-pnl if pnl < 0 else Decimal()); losses = state.consecutive_losses + 1 if pnl < 0 else 0
    dd = (peak - balance) / peak if peak else Decimal(); pause = losses >= POLICY["consecutive_loss_limit"] or daily_loss >= balance * Decimal(POLICY["daily_loss_limit_pct"]) or dd >= Decimal(POLICY["drawdown_pause_pct"])
    new = ShadowState(balance, peak, daily_loss, losses, pause, dd >= Decimal(POLICY["drawdown_review_pct"]))
    return new, {"risk_usd": risk, "net_r": net_r, "net_pnl_usd": pnl, "balance": balance, "peak": peak, "drawdown_pct": dd * 100, "drawdown_state": "REVIEW" if new.review_required else "PAUSE" if pause else "WARNING" if dd >= Decimal(POLICY["drawdown_warning_pct"]) else "NORMAL", "milestones": {str(m): balance >= m for m in MILESTONES}}
