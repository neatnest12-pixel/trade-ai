"""Certified demo-only MT5 execution layer; disabled unless explicitly enabled."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import Any

DEMO_SERVER = "ICMarketsSC-Demo"
DEMO_MODE = 0
SYMBOL = "EURUSD"
MAGIC = 26081901
JOURNAL_PATH = Path("certified_data/DEMO_EXECUTION_JOURNAL.jsonl")
STATE_PATH = Path("runtime/demo_execution_state.json")
POLICY = {
    "version": "controlled-demo-execution-v1",
    "risk_pct": "0.001",
    "daily_loss_pct": "0.01",
    "max_losses": 5,
    "drawdown_warning_pct": "0.03",
    "drawdown_pause_pct": "0.05",
    "max_positions": 1,
    "rr": "2",
    "currency": "USD",
}
POLICY_HASH = hashlib.sha256(
    json.dumps(POLICY, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()


class ExecutionBlocked(RuntimeError):
    """Fail-closed execution policy decision."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _d(value: Any) -> Decimal:
    return Decimal(str(value))


def truth(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() == "true"


def account_guard(account: Any) -> None:
    """The non-bypassable pre-order demo account gate."""
    if (
        account is None
        or getattr(account, "trade_mode", None) != DEMO_MODE
        or getattr(account, "server", None) != DEMO_SERVER
        or getattr(account, "currency", None) != "USD"
    ):
        raise ExecutionBlocked("REAL_OR_UNAPPROVED_ACCOUNT_BLOCKED")


def execution_enabled() -> bool:
    return (
        truth("DEMO_EXECUTION_ENABLED")
        and truth("TRADING_EXECUTION_ENABLED")
        and not truth("REAL_EXECUTION_ENABLED")
    )


@dataclass(frozen=True)
class Plan:
    parent_prediction_id: str
    side: str
    entry: Decimal
    stop: Decimal
    target: Decimal
    volume: Decimal
    allowed_risk_usd: Decimal
    actual_risk_usd: Decimal
    equity: Decimal
    balance: Decimal
    free_margin: Decimal
    spread: Decimal


def plan(
    parent_prediction_id: str, side: str, tick: Any, atr: Decimal, info: Any, account: Any
) -> Plan:
    """Calculation-only 1:2 plan using ATR(14) supplied from completed candles."""
    account_guard(account)
    if side not in {"BUY", "SELL"} or atr <= 0 or tick is None or info is None:
        raise ExecutionBlocked("INVALID_SIGNAL_OR_ATR")
    entry = _d(tick.ask if side == "BUY" else tick.bid)
    distance = atr * Decimal("1.5")
    equity = _d(account.equity)
    allowed = equity * Decimal(POLICY["risk_pct"])
    tick_size, tick_value, minimum, step, maximum = (
        _d(info.trade_tick_size),
        _d(info.trade_tick_value),
        _d(info.volume_min),
        _d(info.volume_step),
        _d(info.volume_max),
    )
    loss_per_lot = distance / tick_size * tick_value
    if entry <= 0 or min(tick_size, tick_value, minimum, step, maximum, loss_per_lot, allowed) <= 0:
        raise ExecutionBlocked("INVALID_LOT_METADATA")
    volume = (min(allowed / loss_per_lot, maximum) / step).to_integral_value(
        rounding=ROUND_DOWN
    ) * step
    actual = volume * loss_per_lot
    if volume < minimum or actual > allowed:
        raise ExecutionBlocked("RISK_TOO_SMALL_FOR_MIN_LOT")
    stop = entry - distance if side == "BUY" else entry + distance
    target = entry + distance * 2 if side == "BUY" else entry - distance * 2
    return Plan(
        parent_prediction_id,
        side,
        entry,
        stop,
        target,
        volume,
        allowed,
        actual,
        equity,
        _d(account.balance),
        _d(account.margin_free),
        _d(tick.ask) - _d(tick.bid),
    )


class Journal:
    """Append-only execution evidence; parent+policy identity survives restarts."""

    def __init__(self, path: Path = JOURNAL_PATH):
        self.path = path

    def rows(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            return [
                json.loads(line)
                for line in self.path.read_text(encoding="utf8").splitlines()
                if line.strip()
            ]
        except (OSError, json.JSONDecodeError) as error:
            raise ExecutionBlocked("MALFORMED_EXECUTION_JOURNAL") from error

    def attempted(self, parent: str) -> bool:
        return any(
            row.get("parent_prediction_id") == parent
            and row.get("execution_policy_hash") == POLICY_HASH
            and row.get("record_type")
            in {"ORDER_INTENT", "ORDER_SUBMITTED", "ORDER_FILLED", "ORDER_REJECTED"}
            for row in self.rows()
        )

    def append(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = {
            **row,
            "timestamp": row.get("timestamp", _now()),
            "execution_policy_hash": POLICY_HASH,
        }
        payload["record_hash"] = hashlib.sha256(
            json.dumps(payload, default=str, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf8", newline="\n") as handle:
            handle.write(
                json.dumps(payload, default=str, sort_keys=True, separators=(",", ":")) + "\n"
            )
        return payload


def guard(account: Any, journal: Journal, mt5: Any) -> None:
    account_guard(account)
    rows = journal.rows()
    today = datetime.now(UTC).date().isoformat()
    closed = [
        r
        for r in rows
        if r.get("record_type") == "POSITION_CLOSED"
        and str(r.get("close_timestamp", "")).startswith(today)
    ]
    equity = _d(account.equity)
    if sum((_d(r.get("net_pnl_usd", 0)) for r in closed), Decimal()) <= -equity * Decimal(
        POLICY["daily_loss_pct"]
    ):
        raise ExecutionBlocked("DAILY_LOSS_LIMIT_REACHED")
    consecutive = 0
    for row in reversed(closed):
        if _d(row.get("net_pnl_usd", 0)) >= 0:
            break
        consecutive += 1
    if consecutive >= POLICY["max_losses"]:
        raise ExecutionBlocked("CONSECUTIVE_LOSS_LIMIT_REACHED")
    start_balance = next(
        (r["balance_before"] for r in rows if r.get("balance_before") is not None), account.balance
    )
    if (_d(start_balance) - equity) / _d(start_balance) >= Decimal(POLICY["drawdown_pause_pct"]):
        raise ExecutionBlocked("HARD_DRAWDOWN_PAUSE")
    if len(mt5.positions_get() or []) >= POLICY["max_positions"]:
        raise ExecutionBlocked("MAX_POSITIONS_REACHED")


def calculation_only(
    parent_prediction_id: str, side: str, mt5: Any, journal: Journal, atr: Decimal
) -> Plan:
    account = mt5.account_info()
    guard(account, journal, mt5)
    if journal.attempted(parent_prediction_id):
        raise ExecutionBlocked("EXECUTION_ALREADY_ATTEMPTED")
    return plan(
        parent_prediction_id,
        side,
        mt5.symbol_info_tick(SYMBOL),
        atr,
        mt5.symbol_info(SYMBOL),
        account,
    )


class DemoExecutionAdapter:
    """Isolated submission adapter.  It records intent before the sole broker call."""

    def __init__(self, mt5: Any, journal: Journal = Journal(), state_path: Path = STATE_PATH):
        self.mt5, self.journal, self.state_path = mt5, journal, state_path

    def write_state(
        self, status: str, last_error: str | None = None, plan_value: Plan | None = None
    ) -> None:
        account = self.mt5.account_info()
        account_guard(account)
        state = {
            "status": status,
            "server": account.server,
            "demo_verification": True,
            "current_equity": str(account.equity),
            "current_balance": str(account.balance),
            "open_positions": len(self.mt5.positions_get() or []),
            "last_heartbeat": _now(),
            "last_error": last_error,
            "execution_policy_hash": POLICY_HASH,
        }
        if plan_value is not None:
            state["last_parent"] = plan_value.parent_prediction_id
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf8"
        )

    def record_intent(self, plan_value: Plan) -> dict[str, Any]:
        execution_id = hashlib.sha256(
            f"{plan_value.parent_prediction_id}:{POLICY_HASH}".encode()
        ).hexdigest()
        return self.journal.append(
            {
                "record_type": "ORDER_INTENT",
                "execution_id": execution_id,
                **asdict(plan_value),
                "risk_pct": plan_value.actual_risk_usd / plan_value.equity,
                "symbol": SYMBOL,
                "magic": MAGIC,
            }
        )

    def submit(self, plan_value: Plan) -> dict[str, Any]:
        if not execution_enabled():
            raise ExecutionBlocked("DEMO_EXECUTION_DISABLED")
        account = self.mt5.account_info()
        guard(account, self.journal, self.mt5)
        account_guard(account)  # Required immediate pre-send hard account gate.
        if self.journal.attempted(plan_value.parent_prediction_id):
            raise ExecutionBlocked("EXECUTION_ALREADY_ATTEMPTED")
        order_type = (
            self.mt5.ORDER_TYPE_BUY if plan_value.side == "BUY" else self.mt5.ORDER_TYPE_SELL
        )
        request = {
            "action": self.mt5.TRADE_ACTION_DEAL,
            "symbol": SYMBOL,
            "volume": float(plan_value.volume),
            "type": order_type,
            "price": float(plan_value.entry),
            "sl": float(plan_value.stop),
            "tp": float(plan_value.target),
            "magic": MAGIC,
            "comment": f"navora:{plan_value.parent_prediction_id[:18]}",
            "type_time": self.mt5.ORDER_TIME_GTC,
            "type_filling": self.mt5.ORDER_FILLING_IOC,
        }
        self.record_intent(plan_value)
        self.journal.append(
            {
                "record_type": "ORDER_SUBMITTED",
                **asdict(plan_value),
                "symbol": SYMBOL,
                "requested_price": plan_value.entry,
            }
        )
        result = self.mt5.order_send(request)
        kind = (
            "ORDER_FILLED"
            if getattr(result, "retcode", None) == self.mt5.TRADE_RETCODE_DONE
            else "ORDER_REJECTED"
        )
        return self.journal.append(
            {
                "record_type": kind,
                **asdict(plan_value),
                "symbol": SYMBOL,
                "broker_retcode": getattr(result, "retcode", None),
                "broker_ticket": getattr(result, "order", None),
                "requested_price": plan_value.entry,
                "fill_price": getattr(result, "price", None),
                "slippage": (
                    _d(getattr(result, "price", plan_value.entry)) - plan_value.entry
                    if getattr(result, "price", None) is not None
                    else None
                ),
            }
        )
