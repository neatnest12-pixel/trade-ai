from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.v1_1.contract import CAMPAIGN_ID, CONFIG_HASH, SNAPSHOT_ID

REQUIRED_NEW = {"campaign_id", "snapshot_id", "config_hash", "symbol", "timeframe",
                "prediction_timestamp", "candle_timestamp", "decision"}


def _validate_new(record: dict[str, Any]) -> None:
    missing = REQUIRED_NEW - record.keys()
    if missing:
        raise ValueError(f"missing schema fields: {sorted(missing)}")
    if record["campaign_id"] != CAMPAIGN_ID:
        raise ValueError("campaign_id mismatch")
    if record["snapshot_id"] != SNAPSHOT_ID or record["config_hash"] != CONFIG_HASH:
        raise ValueError("V1.1 identity mismatch")
    if record["decision"] not in {"BUY", "SELL", "NO_TRADE"}:
        raise ValueError("invalid decision")
    if record["decision"] == "NO_TRADE":
        for field in ("entry_price", "stop_loss", "target_price", "risk_rupees", "position_size_simulated"):
            if record.get(field) not in (None, "", "N/A"):
                raise ValueError("NO_TRADE economics must be empty")


def append_v11(path: Path, record: dict[str, Any]) -> str:
    if path.name == "FORWARD_PAPER_JOURNAL.jsonl":
        raise ValueError("V1 journal is immutable")
    if not record.get("prediction_hash"):
        raise ValueError("prediction_hash required")
    _validate_new(record)
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()] if path.exists() else []
    key = tuple(record.get(field) for field in ("campaign_id", "snapshot_id", "config_hash", "symbol", "timeframe", "candle_timestamp"))
    for row in rows:
        old_key = tuple(row.get(field) for field in ("campaign_id", "snapshot_id", "config_hash", "symbol", "timeframe", "candle_timestamp"))
        if old_key == key or row.get("prediction_hash") == record["prediction_hash"]:
            raise ValueError("duplicate prediction identity")
    payload = {**record, "previous_hash": rows[-1]["record_hash"] if rows else "GENESIS"}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    payload["record_hash"] = digest
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True, default=str) + "\n")
    return digest

