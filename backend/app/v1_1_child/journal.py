"""Immutable append-only journal for V1.1 paper-trade evidence.

This module writes only ``certified_data/PAPER_TRADE_JOURNAL.jsonl`` by
default. It deliberately has no execution, signal-generation, or broker
integration responsibilities.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

JOURNAL_PATH = Path("certified_data/PAPER_TRADE_JOURNAL.jsonl")
RECORD_TYPES = frozenset({"TRADE_CONTRACT", "RR_OUTCOME", "TRADE_SKIPPED", "SYSTEM_EVENT"})
REQUIRED_FIELDS = frozenset({"record_type", "created_at", "record_hash"})


class PaperTradeJournalError(RuntimeError):
    """Raised when journal data cannot be safely trusted."""


def _json_default(value: Any) -> str:
    """Encode decimal values deterministically without lossy float conversion."""
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def _canonical_json(record: dict[str, Any]) -> str:
    """Return the single canonical representation used for hashing and storage."""
    return json.dumps(
        record,
        default=_json_default,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def record_digest(record: dict[str, Any]) -> str:
    """Calculate a SHA-256 digest without allowing ``record_hash`` to affect it."""
    if not isinstance(record, dict):
        raise ValueError("RECORD_MUST_BE_OBJECT")
    payload = {key: value for key, value in record.items() if key != "record_hash"}
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PaperTradeJournalError("MALFORMED_PAPER_TRADE_JOURNAL")
        result[key] = value
    return result


def _validate_record(record: Any, *, verify_hash: bool) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise PaperTradeJournalError("MALFORMED_PAPER_TRADE_JOURNAL")
    if REQUIRED_FIELDS - record.keys() or record.get("record_type") not in RECORD_TYPES:
        raise PaperTradeJournalError("MALFORMED_PAPER_TRADE_JOURNAL")
    if not isinstance(record["created_at"], str) or not record["created_at"]:
        raise PaperTradeJournalError("MALFORMED_PAPER_TRADE_JOURNAL")
    if verify_hash and (
        not isinstance(record["record_hash"], str) or record["record_hash"] != record_digest(record)
    ):
        raise PaperTradeJournalError("MALFORMED_PAPER_TRADE_JOURNAL")
    return record


def _read_path(path: Path) -> list[dict[str, Any]]:
    """Read and validate every journal row, failing closed at the first fault."""
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as handle:
            lines = list(handle)
    except (OSError, UnicodeDecodeError) as error:
        raise PaperTradeJournalError("MALFORMED_PAPER_TRADE_JOURNAL") from error

    records: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            parsed = json.loads(line, object_pairs_hook=_reject_duplicate_keys)
        except (json.JSONDecodeError, PaperTradeJournalError) as error:
            raise PaperTradeJournalError("MALFORMED_PAPER_TRADE_JOURNAL") from error
        records.append(_validate_record(parsed, verify_hash=True))
    return records


def read_records() -> list[dict[str, Any]]:
    """Safely read the default paper-trade journal."""
    return _read_path(JOURNAL_PATH)


def _append_path(path: Path, record: dict[str, Any]) -> str:
    if not isinstance(record, dict):
        raise ValueError("RECORD_MUST_BE_OBJECT")
    if record.get("record_type") not in RECORD_TYPES:
        raise ValueError("INVALID_RECORD_TYPE")
    if not isinstance(record.get("created_at"), str) or not record["created_at"]:
        raise ValueError("MISSING_CREATED_AT")

    _read_path(path)
    payload = {key: value for key, value in record.items() if key != "record_hash"}
    payload["record_hash"] = record_digest(payload)
    serialized = _canonical_json(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(serialized)
        handle.write("\n")
        handle.flush()
    return payload["record_hash"]


def append_record(record: dict[str, Any]) -> str:
    """Append exactly one immutable, canonically encoded journal record."""
    return _append_path(JOURNAL_PATH, record)


def contract_exists(parent_prediction_id: Any, trade_contract_hash: Any) -> bool:
    """Return whether an immutable trade contract identity already exists."""
    return any(
        record["record_type"] == "TRADE_CONTRACT"
        and record.get("parent_prediction_id") == parent_prediction_id
        and record.get("trade_contract_hash") == trade_contract_hash
        for record in read_records()
    )


def rr_outcome_exists(paper_trade_id: Any, rr_profile: Any, cost_profile: Any) -> bool:
    """Return whether an immutable RR outcome identity already exists."""
    return any(
        record["record_type"] == "RR_OUTCOME"
        and (record.get("paper_trade_id"), record.get("rr_profile"), record.get("cost_profile"))
        == (paper_trade_id, rr_profile, cost_profile)
        for record in read_records()
    )


# Compatibility aliases for the existing, intentionally untouched processor.
def rows(path: Path) -> list[dict[str, Any]]:
    return _read_path(path)


def append(path: Path, record: dict[str, Any]) -> bool:
    """Append to an explicitly supplied journal path, returning False for duplicates."""
    if record.get("record_type") == "TRADE_CONTRACT" and any(
        row["record_type"] == "TRADE_CONTRACT"
        and row.get("parent_prediction_id") == record.get("parent_prediction_id")
        and row.get("trade_contract_hash") == record.get("trade_contract_hash")
        for row in _read_path(path)
    ):
        return False
    if record.get("record_type") == "RR_OUTCOME" and any(
        row["record_type"] == "RR_OUTCOME"
        and (row.get("paper_trade_id"), row.get("rr_profile"), row.get("cost_profile"))
        == (record.get("paper_trade_id"), record.get("rr_profile"), record.get("cost_profile"))
        for row in _read_path(path)
    ):
        return False
    _append_path(path, record)
    return True
