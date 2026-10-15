"""Demo-only execution layer for frozen Navora Quant V1.

This module intentionally has no dependency on the paper collector.  It is
safe by default: a caller must opt in to every demo guard before an MT5
``order_send`` call is reachable.  Real-account execution is impossible at
the code level.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

V1_SNAPSHOT_ID = "v1-paper-r4-0d891a2356e35464"
V1_CONFIG_HASH = "0d891a2356e35464dbe3ba9f51c50fe240e40feff84fe4008d8b1564b55f9eac"
DEMO_SERVER = "ICMarketsSC-Demo"
DEMO_BROKER = "Raw Trading Ltd"
DEMO_MODE = 0  # MetaTrader5.ACCOUNT_TRADE_MODE_DEMO
REAL_MODE = 2  # MetaTrader5.ACCOUNT_TRADE_MODE_REAL
MAGIC = 26081801
COMMENT_PREFIX = "NAVORA_QUANT_V1_DEMO"


class ExecutionBlocked(RuntimeError):
    """A required safety condition was not met."""


@dataclass(frozen=True)
class ExecutionConfig:
    execution_mode: str = "PAPER"
    trading_execution_enabled: bool = False
    demo_execution_enabled: bool = False
    real_execution_enabled: bool = False
    kill_switch: bool = True
    symbol: str = "EURUSD"
    reference_risk_inr: Decimal = Decimal("25")
    inr_per_account_currency: Decimal = Decimal("84")
    max_spread_points: int = 20
    max_new_positions: int = 1
    max_daily_loss_r: Decimal = Decimal("3")
    max_consecutive_losses: int = 3

    @classmethod
    def from_env(cls) -> ExecutionConfig:
        def truth(name: str, default: bool) -> bool:
            return os.getenv(name, str(default)).lower() == "true"

        return cls(
            execution_mode=os.getenv("EXECUTION_MODE", "PAPER"),
            trading_execution_enabled=truth("TRADING_EXECUTION_ENABLED", False),
            demo_execution_enabled=truth("DEMO_EXECUTION_ENABLED", False),
            real_execution_enabled=truth("REAL_EXECUTION_ENABLED", False),
            kill_switch=truth("DEMO_EXECUTION_KILL_SWITCH", True),
        )


@dataclass(frozen=True)
class Signal:
    prediction_id: str
    v1_snapshot_id: str
    v1_config_hash: str
    symbol: str
    timeframe: str
    candle_timestamp: str
    decision: str
    confidence: float | None
    entry: Decimal
    stop: Decimal
    target: Decimal

    @property
    def execution_key(self) -> tuple[str, str, str, str, str]:
        return (self.prediction_id, self.v1_snapshot_id, self.v1_config_hash, self.symbol, self.candle_timestamp)


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _json_default(value: Any) -> str:
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"not JSON serializable: {type(value)!r}")


def _record_hash(record: dict[str, Any]) -> str:
    body = {key: value for key, value in record.items() if key != "record_hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=_json_default, separators=(",", ":")).encode()).hexdigest()


def account_guard(account: Any) -> None:
    """Hard block REAL and every account not pinned to the authorized demo."""
    if account is None:
        raise ExecutionBlocked("ACCOUNT_INFO_UNAVAILABLE")
    if getattr(account, "trade_mode", None) == REAL_MODE:
        raise ExecutionBlocked("REAL_ACCOUNT_EXECUTION_BLOCKED")
    if getattr(account, "trade_mode", None) != DEMO_MODE:
        raise ExecutionBlocked("DEMO_ACCOUNT_REQUIRED")
    if getattr(account, "server", None) != DEMO_SERVER:
        raise ExecutionBlocked("DEMO_SERVER_MISMATCH")
    if getattr(account, "company", None) != DEMO_BROKER:
        raise ExecutionBlocked("DEMO_BROKER_MISMATCH")
    if getattr(account, "trade_allowed", True) is False:
        raise ExecutionBlocked("ACCOUNT_TRADING_NOT_ALLOWED")


def execution_guard(config: ExecutionConfig, *, worker_owns_lease: bool) -> None:
    if config.real_execution_enabled:
        raise ExecutionBlocked("REAL_EXECUTION_MUST_REMAIN_DISABLED")
    if config.kill_switch:
        raise ExecutionBlocked("DEMO_EXECUTION_KILL_SWITCH")
    if config.execution_mode != "DEMO_EXECUTION":
        raise ExecutionBlocked("EXECUTION_MODE_NOT_DEMO")
    if not config.trading_execution_enabled or not config.demo_execution_enabled:
        raise ExecutionBlocked("DEMO_EXECUTION_NOT_EXPLICITLY_ENABLED")
    if not worker_owns_lease:
        raise ExecutionBlocked("EXECUTION_WORKER_LEASE_LOST")


def validate_signal(signal: Signal, config: ExecutionConfig) -> None:
    if signal.decision == "NO_TRADE":
        raise ExecutionBlocked("NO_TRADE_NEVER_EXECUTES")
    if signal.decision not in {"BUY", "SELL"}:
        raise ExecutionBlocked("INVALID_DECISION")
    if signal.v1_snapshot_id != V1_SNAPSHOT_ID:
        raise ExecutionBlocked("SNAPSHOT_MISMATCH")
    if signal.v1_config_hash != V1_CONFIG_HASH:
        raise ExecutionBlocked("CONFIG_HASH_MISMATCH")
    if signal.symbol != config.symbol:
        raise ExecutionBlocked("SYMBOL_MISMATCH")
    if not (signal.entry > 0 and signal.stop > 0 and signal.target > 0):
        raise ExecutionBlocked("INVALID_ORDER_PRICES")
    if signal.decision == "BUY" and not (signal.stop < signal.entry < signal.target):
        raise ExecutionBlocked("INVALID_STOPS")
    if signal.decision == "SELL" and not (signal.target < signal.entry < signal.stop):
        raise ExecutionBlocked("INVALID_STOPS")


def _round_down(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).quantize(Decimal("1"), rounding=ROUND_DOWN) * step


def position_volume(signal: Signal, symbol_info: Any, reference_risk_inr: Decimal,
                    inr_per_account_currency: Decimal) -> Decimal:
    """Return a broker-valid lot size without ever rounding risk upward."""
    point = Decimal(str(getattr(symbol_info, "point", 0)))
    tick_value = Decimal(str(getattr(symbol_info, "trade_tick_value", 0)))
    tick_size = Decimal(str(getattr(symbol_info, "trade_tick_size", 0)))
    minimum = Decimal(str(getattr(symbol_info, "volume_min", 0)))
    step = Decimal(str(getattr(symbol_info, "volume_step", 0)))
    maximum = Decimal(str(getattr(symbol_info, "volume_max", 0)))
    distance = abs(signal.entry - signal.stop)
    if min(point, tick_value, tick_size, minimum, step, maximum, distance, inr_per_account_currency) <= 0:
        raise ExecutionBlocked("INVALID_LOT_METADATA")
    loss_per_lot = (distance / tick_size) * tick_value
    raw = (reference_risk_inr / inr_per_account_currency) / loss_per_lot
    volume = _round_down(min(raw, maximum), step)
    if volume < minimum or volume <= 0:
        raise ExecutionBlocked("EXECUTION_SKIPPED_RISK_TOO_SMALL")
    return volume


class ExecutionJournal:
    """Append-only JSONL audit log; duplicate keys are rejected before sending."""

    def __init__(self, path: Path):
        self.path = path

    def records(self) -> Iterable[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with self.path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    rows.append(json.loads(line))
        return rows

    def contains(self, signal: Signal) -> bool:
        key = signal.execution_key
        return any(tuple(row.get(field) for field in ("prediction_id", "v1_snapshot_id", "v1_config_hash", "symbol", "candle_timestamp")) == key for row in self.records())

    def append(self, record: dict[str, Any]) -> dict[str, Any]:
        row = {**record, "record_hash": _record_hash(record)}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(row, default=_json_default, sort_keys=True) + "\n")
        return row


def _result_value(result: Any, name: str, default: Any = None) -> Any:
    return getattr(result, name, default) if result is not None else default


class DemoExecutionEngine:
    """The only component that may reach ``mt5.order_send`` after all guards."""

    def __init__(self, mt5: Any, journal: ExecutionJournal, config: ExecutionConfig):
        self.mt5, self.journal, self.config = mt5, journal, config
        self._account_login: Any = None

    def _safety_guard(self) -> None:
        today = datetime.now(UTC).date().isoformat()
        outcomes = [row for row in self.journal.records() if row.get("record_type") == "outcome"
                    and str(row.get("closed_at", ""))[:10] == today]
        daily_r = sum((Decimal(str(row.get("r_multiple", 0))) for row in outcomes), Decimal("0"))
        if daily_r <= -self.config.max_daily_loss_r:
            raise ExecutionBlocked("DAILY_LOSS_LIMIT_REACHED")
        losses = 0
        for row in reversed(outcomes):
            if row.get("result") != "LOSS":
                break
            losses += 1
        if losses >= self.config.max_consecutive_losses:
            raise ExecutionBlocked("CONSECUTIVE_LOSS_LIMIT_REACHED")
        positions_get = getattr(self.mt5, "positions_get", None)
        if positions_get:
            owned = [p for p in (positions_get() or []) if getattr(p, "magic", None) == MAGIC]
            if len(owned) >= self.config.max_new_positions:
                raise ExecutionBlocked("MAX_NAVORA_DEMO_POSITIONS_REACHED")

    def _pin_account(self, account: Any) -> None:
        login = getattr(account, "login", None)
        if self._account_login is None:
            self._account_login = login
        elif login != self._account_login:
            raise ExecutionBlocked("ACCOUNT_CHANGED_DURING_RUNTIME")

    def _audit(self, signal: Signal, status: str, **details: Any) -> dict[str, Any]:
        return self.journal.append({
            "execution_record_id": str(uuid4()), "record_type": "execution", "timestamp": _utcnow(),
            "prediction_id": signal.prediction_id, "v1_snapshot_id": signal.v1_snapshot_id,
            "v1_config_hash": signal.v1_config_hash, "symbol": signal.symbol, "timeframe": signal.timeframe,
            "candle_timestamp": signal.candle_timestamp, "decision": signal.decision,
            "confidence": signal.confidence, "server": DEMO_SERVER, "account_mode": "DEMO",
            "execution_mode": "DEMO_EXECUTION", "status": status, **details,
        })

    def execute(self, signal: Signal, *, worker_owns_lease: bool, market_fresh: bool = True) -> dict[str, Any]:
        try:
            execution_guard(self.config, worker_owns_lease=worker_owns_lease)
            validate_signal(signal, self.config)
            if not market_fresh:
                raise ExecutionBlocked("STALE_MARKET_DATA")
            if self.journal.contains(signal):
                raise ExecutionBlocked("EXECUTION_ALREADY_EXISTS")
            account = self.mt5.account_info()
            account_guard(account)
            self._pin_account(account)
            self._safety_guard()
            symbol = self.mt5.symbol_info(signal.symbol)
            tick = self.mt5.symbol_info_tick(signal.symbol)
            if symbol is None or tick is None:
                raise ExecutionBlocked("MARKET_DATA_UNAVAILABLE")
            point = Decimal(str(symbol.point))
            ask, bid = Decimal(str(tick.ask)), Decimal(str(tick.bid))
            spread_points = (ask - bid) / point
            if spread_points > self.config.max_spread_points:
                raise ExecutionBlocked("SPREAD_LIMIT_EXCEEDED")
            volume = position_volume(signal, symbol, self.config.reference_risk_inr,
                                     self.config.inr_per_account_currency)
            price = ask if signal.decision == "BUY" else bid
            request = {
                "action": self.mt5.TRADE_ACTION_DEAL,
                "symbol": signal.symbol,
                "volume": float(volume),
                "type": self.mt5.ORDER_TYPE_BUY if signal.decision == "BUY" else self.mt5.ORDER_TYPE_SELL,
                "price": float(price), "sl": float(signal.stop), "tp": float(signal.target),
                "deviation": self.config.max_spread_points, "magic": MAGIC,
                "comment": f"{COMMENT_PREFIX}:{signal.prediction_id}", "type_time": self.mt5.ORDER_TIME_GTC,
                "type_filling": self.mt5.ORDER_FILLING_IOC,
            }
            checked = self.mt5.order_check(request)
            if checked is None or _result_value(checked, "retcode") not in {0, getattr(self.mt5, "TRADE_RETCODE_DONE", 10009)}:
                return self._audit(signal, "ORDER_CHECK_FAILED", requested_price=price, requested_stop=signal.stop,
                                   requested_target=signal.target, requested_volume=volume,
                                   order_check_result=str(checked), actual_spread=spread_points)
            # Hard guards are intentionally repeated immediately before the sole send path.
            execution_guard(self.config, worker_owns_lease=worker_owns_lease)
            account = self.mt5.account_info()
            account_guard(account)
            self._pin_account(account)
            result = self.mt5.order_send(request)
            retcode = _result_value(result, "retcode")
            done = getattr(self.mt5, "TRADE_RETCODE_DONE", 10009)
            partial = getattr(self.mt5, "TRADE_RETCODE_DONE_PARTIAL", 10010)
            status = "FILLED" if retcode == done else "PARTIAL" if retcode == partial else "REJECTED"
            filled = Decimal(str(_result_value(result, "price", 0))) if result is not None else None
            return self._audit(signal, status, requested_price=price, requested_stop=signal.stop,
                               requested_target=signal.target, requested_volume=volume,
                               order_check_result=str(checked), order_send_timestamp=_utcnow(),
                               broker_retcode=retcode, broker_message=_result_value(result, "comment"),
                               order_id=_result_value(result, "order"), deal_id=_result_value(result, "deal"),
                               filled_price=filled, filled_volume=_result_value(result, "volume"),
                               actual_spread=spread_points, slippage=(filled - price if filled is not None else None))
        except ExecutionBlocked as exc:
            # NO_TRADE and invalid signals are intentionally not treated as executions.
            return {"status": "BLOCKED", "reason": str(exc), "prediction_id": signal.prediction_id}


def append_outcome(journal: ExecutionJournal, execution: dict[str, Any], *, exit_price: Decimal,
                   gross_pnl: Decimal, commission: Decimal = Decimal("0"), swap: Decimal = Decimal("0"),
                   exit_reason: str = "OTHER", closed_at: str | None = None) -> dict[str, Any]:
    """Append, never update, an outcome for an already-recorded demo execution."""
    entry = Decimal(str(execution["filled_price"]))
    stop = Decimal(str(execution["requested_stop"]))
    risk = abs(entry - stop)
    net = gross_pnl - commission + swap
    result = "WIN" if net > 0 else "LOSS" if net < 0 else "BREAKEVEN"
    return journal.append({"record_type": "outcome", "timestamp": _utcnow(), "execution_record_id": execution["execution_record_id"],
        "prediction_id": execution["prediction_id"], "entry": entry, "exit": exit_price, "gross_pnl": gross_pnl,
        "commission": commission, "swap": swap, "net_pnl": net, "r_multiple": (net / risk if risk else None),
        "result": result, "exit_reason": exit_reason, "closed_at": closed_at or _utcnow()})


def learning_rows(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Produce a read-only V2 research view; it never writes or trains a model."""
    executions = {r.get("execution_record_id"): r for r in records if r.get("record_type") == "execution"}
    return [{"prediction_id": e["prediction_id"], "market_timestamp": e["candle_timestamp"], "decision": e["decision"],
             "confidence": e.get("confidence"), "requested_entry": e.get("requested_price"),
             "filled_entry": e.get("filled_price"), "spread": e.get("actual_spread"), "slippage": e.get("slippage"),
             "volume": e.get("filled_volume"), "stop": e.get("requested_stop"), "target": e.get("requested_target"),
             "outcome": r.get("result"), "r_result": r.get("r_multiple"), "net_result": r.get("net_pnl")}
            for r in records if r.get("record_type") == "outcome" for e in [executions.get(r.get("execution_record_id"))] if e]


def demo_metrics(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Execution-only metrics.  Paper predictions are deliberately excluded."""
    rows = list(records)
    executions = [r for r in rows if r.get("record_type") == "execution"]
    outcomes = [r for r in rows if r.get("record_type") == "outcome"]
    wins = [Decimal(str(r["r_multiple"])) for r in outcomes if r.get("result") == "WIN"]
    losses = [Decimal(str(r["r_multiple"])) for r in outcomes if r.get("result") == "LOSS"]
    net_r = sum((Decimal(str(r.get("r_multiple", 0))) for r in outcomes), Decimal("0"))
    gross_profit = sum(wins, Decimal("0")); gross_loss = -sum(losses, Decimal("0"))
    return {"total_demo_orders": len(executions), "filled_orders": sum(r.get("status") == "FILLED" for r in executions),
            "rejected_orders": sum(r.get("status") in {"REJECTED", "ORDER_CHECK_FAILED"} for r in executions),
            "wins": len(wins), "losses": len(losses), "net_r": net_r,
            "average_win_r": (gross_profit / len(wins) if wins else None),
            "average_loss_r": (sum(losses, Decimal("0")) / len(losses) if losses else None),
            "profit_factor": (gross_profit / gross_loss if gross_loss else None),
            "win_rate": (len(wins) / len(outcomes) if outcomes else None)}
