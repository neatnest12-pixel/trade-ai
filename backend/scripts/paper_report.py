"""Read-only reporting for Navora's frozen forward-paper journal.

This module deliberately has no dependency on the forward collector, MT5, the
database, or the worker lock.  It only opens the supplied JSONL journal and
worker-state files for reading.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


EXPECTED_SNAPSHOT = "v1-paper-r4-0d891a2356e35464"
EXPECTED_CONFIG_HASH = "0d891a2356e35464dbe3ba9f51c50fe240e40feff84fe4008d8b1564b55f9eac"
NOT_ENOUGH_DATA = "NOT_ENOUGH_DATA"


def _first(row: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in row:
            return row[name]
    return None


def _record_type(row: dict[str, Any]) -> str:
    return str(_first(row, "record_type", "type", "event", "kind") or "").lower()


def _is_prediction(row: dict[str, Any]) -> bool:
    return _record_type(row) in {"prediction", "signal", "forward_prediction"} or "signal" in row


def _is_evaluation(row: dict[str, Any]) -> bool:
    return _record_type(row) in {"evaluation", "evaluated", "trade_evaluation"} or any(
        field in row for field in ("outcome", "result_r", "realized_r", "pnl_r")
    )


def _identity(row: dict[str, Any]) -> tuple[str, str, str, str, str] | None:
    values = (
        _first(row, "v1_snapshot_id", "snapshot", "snapshot_id"),
        _first(row, "v1_config_hash", "config_hash"),
        _first(row, "symbol"),
        _first(row, "timeframe", "tf"),
        _first(row, "candle_timestamp", "candle_time", "timestamp"),
    )
    if any(value is None or value == "" for value in values):
        return None
    return tuple(str(value) for value in values)  # type: ignore[return-value]


def _is_active(row: dict[str, Any], snapshot: str, config_hash: str) -> bool:
    return (
        str(_first(row, "v1_snapshot_id", "snapshot", "snapshot_id") or "") == snapshot
        and str(_first(row, "v1_config_hash", "config_hash") or "") == config_hash
    )


def _r_value(row: dict[str, Any]) -> float | None:
    value = _first(row, "result_r", "realized_r", "pnl_r", "r_multiple", "r")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _outcome(row: dict[str, Any]) -> str:
    return str(_first(row, "outcome", "result", "status") or "").upper()


def read_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Read JSONL without mutation; malformed nonblank lines are reported, not ignored."""
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError("record is not an object")
                records.append(value)
            except (json.JSONDecodeError, ValueError) as exc:
                errors.append(f"line {line_number}: {exc}")
    return records, errors


def _metric(value: float | int | None, minimum: int, sample_size: int) -> float | int | str:
    return value if sample_size >= minimum and value is not None else NOT_ENOUGH_DATA


def analyze_records(
    records: list[dict[str, Any]],
    *,
    snapshot: str = EXPECTED_SNAPSHOT,
    config_hash: str = EXPECTED_CONFIG_HASH,
    starting_balance: float = 2500.0,
) -> dict[str, Any]:
    predictions = [row for row in records if _is_prediction(row)]
    evaluations = [row for row in records if _is_evaluation(row)]
    active_predictions = [row for row in predictions if _is_active(row, snapshot, config_hash)]
    legacy = [row for row in predictions if _identity(row) is None]
    official: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for row in active_predictions:
        key = _identity(row)
        if key is None:
            continue
        if key in seen:
            duplicates.append(row)
        else:
            seen.add(key)
            official.append(row)

    signals = [str(_first(row, "signal", "action", "prediction") or "").upper() for row in official]
    directional = [row for row, signal in zip(official, signals) if signal in {"BUY", "SELL"}]
    evaluation_keys = {
        key for row in evaluations if _is_active(row, snapshot, config_hash) for key in [_identity(row)] if key
    }
    completed = [row for row in directional if _identity(row) in evaluation_keys]
    unresolved = [row for row in directional if _identity(row) not in evaluation_keys]
    active_evaluations = [row for row in evaluations if _is_active(row, snapshot, config_hash)]
    rs = [value for row in active_evaluations for value in [_r_value(row)] if value is not None]
    wins = sum(1 for row, value in zip(active_evaluations, rs) if value > 0) if len(rs) == len(active_evaluations) else sum(value > 0 for value in rs)
    losses = sum(value < 0 for value in rs)
    completed_count = len(completed)
    expectancy = sum(rs) / len(rs) if rs else None
    gross_win, gross_loss = sum(value for value in rs if value > 0), -sum(value for value in rs if value < 0)
    profit_factor = (gross_win / gross_loss) if gross_loss else None
    running, peak, max_drawdown = 0.0, 0.0, 0.0
    for value in rs:
        running += value
        peak = max(peak, running)
        max_drawdown = max(max_drawdown, peak - running)

    return {
        "total_journal_records": len(records), "raw_prediction_records": len(predictions),
        "legacy_pre_freeze_records": len(legacy), "active_snapshot_predictions": len(active_predictions),
        "unique_official_prediction_keys": len(official), "duplicate_restart_records": len(duplicates),
        "buy": signals.count("BUY"), "sell": signals.count("SELL"), "no_trade": signals.count("NO_TRADE"),
        "evaluations": len(active_evaluations), "completed_trades": completed_count, "unresolved": len(unresolved),
        "wins": wins, "losses": losses,
        "win_rate": _metric(wins / len(rs) if rs else None, 1, len(rs)),
        "expectancy": _metric(expectancy, 1, len(rs)), "profit_factor": _metric(profit_factor, 1, len(rs)),
        "max_drawdown": _metric(max_drawdown, 1, len(rs)),
        "paper_balance": _metric(starting_balance + sum(rs) * 25, 1, len(rs)),
    }


def read_worker_state(path: Path, *, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"worker_health": "UNKNOWN", "worker_state_error": str(exc)}
    heartbeat = _first(state, "last_heartbeat", "heartbeat", "heartbeat_at")
    try:
        heartbeat_time = datetime.fromisoformat(str(heartbeat).replace("Z", "+00:00"))
        if heartbeat_time.tzinfo is None:
            heartbeat_time = heartbeat_time.replace(tzinfo=UTC)
        age = max(0.0, (now - heartbeat_time.astimezone(UTC)).total_seconds())
        lease = float(_first(state, "lease_seconds", "lease") or 30)
        health = "HEALTHY" if age <= lease * 2 else "STALE"
    except (TypeError, ValueError):
        age, health = None, "UNKNOWN"
    return {"worker_status": _first(state, "status", "worker_status"), "last_heartbeat": heartbeat,
            "heartbeat_age_seconds": age, "lease_seconds": _first(state, "lease_seconds", "lease"),
            "last_completed_m1": _first(state, "last_completed_m1", "last_completed_candle"),
            "active_snapshot": _first(state, "v1_snapshot_id", "snapshot"),
            "active_config_hash": _first(state, "v1_config_hash", "config_hash"), "worker_health": health}


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Navora paper-validation reporter")
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--worker-state", type=Path)
    args = parser.parse_args()
    records, errors = read_jsonl(args.journal)
    report = analyze_records(records)
    report["malformed_journal_records"] = errors
    if args.worker_state:
        report["worker"] = read_worker_state(args.worker_state)
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 2 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
