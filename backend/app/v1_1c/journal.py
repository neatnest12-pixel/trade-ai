"""V1.1C journal guard. The campaign journal remains empty until approved."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.v1_1c.contract import CAMPAIGN_ID, CONFIG_HASH, SNAPSHOT_ID

PREDICTION_REQUIRED = {"campaign_id", "snapshot_id", "config_hash", "prediction_hash", "symbol",
                       "timeframe", "prediction_timestamp", "candle_timestamp", "decision"}
OUTCOME_REQUIRED = {"campaign_id", "snapshot_id", "config_hash", "prediction_hash", "status",
                    "exit_reason"}


def append_v11c(path: Path, record: dict[str, Any]) -> str:
    """Append a validated isolated record; callers must provide prior approval."""
    required = OUTCOME_REQUIRED if record.get("type") == "outcome" else PREDICTION_REQUIRED
    missing = required - record.keys()
    if missing:
        raise ValueError(f"missing schema fields: {sorted(missing)}")
    if record.get("campaign_id") != CAMPAIGN_ID:
        raise ValueError("campaign_id mismatch")
    if record.get("snapshot_id") != SNAPSHOT_ID or record.get("config_hash") != CONFIG_HASH:
        raise ValueError("V1.1C identity mismatch")
    if record.get("type") == "outcome":
        return _append(path, record, duplicate_key=("prediction_hash", "status", "exit_timestamp"))
    if record["decision"] not in {"BUY", "SELL", "NO_TRADE"}:
        raise ValueError("invalid decision")
    if record["decision"] == "NO_TRADE":
        for field in ("entry_price", "stop_loss", "target_price", "risk_rupees", "position_size_simulated"):
            if record.get(field) not in (None, "", "N/A"):
                raise ValueError("NO_TRADE economics must be empty")
    return _append(path, record, duplicate_key=("campaign_id", "snapshot_id", "config_hash", "symbol", "timeframe", "candle_timestamp"))


def _append(path: Path, record: dict[str, Any], duplicate_key: tuple[str, ...]) -> str:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []
    key = tuple(record.get(field) for field in duplicate_key)
    if any(tuple(row.get(field) for field in duplicate_key) == key for row in rows):
        raise ValueError("duplicate prediction identity")
    payload = {**record, "previous_hash": rows[-1]["record_hash"] if rows else "GENESIS"}
    payload["record_hash"] = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True, default=str) + "\n")
    return payload["record_hash"]
