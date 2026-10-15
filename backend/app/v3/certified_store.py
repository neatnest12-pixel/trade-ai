"""Bounded ingestion and transactional append-only certified outcome journal.

SQLite is the authoritative journal. JSONL is an atomic, reproducible projection;
a crash between commit and export cannot lose or duplicate a contract. Original
observation and legacy outcome files are never opened for writing.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from .certification import POLICY_HASH, eligible, is_v34, project_setup, require_certified_outcome

MAX_ROW_BYTES = 4 * 1024 * 1024


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf8", newline="\n") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=FULL")
    db.execute("PRAGMA cache_size=-8192")
    db.execute("PRAGMA temp_store=FILE")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS observations(
          id TEXT PRIMARY KEY, symbol TEXT, timestamp TEXT, v34 INTEGER,
          byte_offset INTEGER, byte_length INTEGER, digest TEXT,
          UNIQUE(symbol,timestamp));
        CREATE TABLE IF NOT EXISTS setups(
          id TEXT PRIMARY KEY, decision TEXT, side TEXT, timing TEXT, payload TEXT,
          feasibility TEXT NOT NULL DEFAULT 'NOT_CHECKED', reason TEXT,
          candidate TEXT, checked_at TEXT);
        CREATE TABLE IF NOT EXISTS outcomes(
          outcome_id TEXT PRIMARY KEY, observation_id TEXT NOT NULL, rr REAL NOT NULL,
          policy_hash TEXT NOT NULL, payload TEXT NOT NULL,
          UNIQUE(observation_id,rr,policy_hash));
        CREATE TABLE IF NOT EXISTS tick_windows(
          window_key TEXT PRIMARY KEY,payload TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS setup_order ON setups(decision,id);
        CREATE INDEX IF NOT EXISTS contract_setup ON outcomes(observation_id,rr);
    """)
    return db


def _ingest(db: sqlite3.Connection, source: Path, heartbeat=lambda: None) -> dict:
    """Incremental byte checkpoints; at most one full research row in memory."""
    prior = db.execute("SELECT value FROM metadata WHERE key='source'").fetchone()
    meta = json.loads(prior[0]) if prior else {"path": str(source.resolve()), "offset": 0, "total": 0,
                                               "v34": 0, "no_active": 0, "conflicting": 0}
    if meta["path"] != str(source.resolve()) or source.stat().st_size < meta["offset"]:
        raise RuntimeError("OBSERVATION_SOURCE_CHANGED")
    size = source.stat().st_size
    with source.open("rb") as handle:
        handle.seek(meta["offset"])
        while handle.tell() < size:
            start = handle.tell()
            raw = handle.readline(MAX_ROW_BYTES + 1)
            if len(raw) > MAX_ROW_BYTES or not raw.endswith(b"\n"):
                raise RuntimeError(f"PARTIAL_OR_OVERSIZED_OBSERVATION:{start}")
            row = json.loads(raw)
            if not isinstance(row, dict) or not row.get("observation_id"):
                raise RuntimeError("INVALID_OBSERVATION")
            db.execute("INSERT INTO observations VALUES(?,?,?,?,?,?,?)",
                       (row["observation_id"], row.get("symbol"), row["timestamp"], int(is_v34(row)),
                        start, len(raw), hashlib.sha256(raw).hexdigest()))
            meta["total"] += 1
            if is_v34(row):
                meta["v34"] += 1
                sides = {v.get("side") for v in row.get("strategy_families", {}).values()
                         if v.get("setup") and v.get("side") in {"BUY", "SELL"}}
                meta["no_active"] += not sides
                meta["conflicting"] += len(sides) > 1
            setup = project_setup(row)
            if setup:
                db.execute("INSERT INTO setups(id,decision,side,timing,payload,feasibility,reason) VALUES(?,?,?,?,?,?,?)",
                           (setup["observation_id"], setup["decision_timestamp"] or setup["bar_timestamp"],
                            setup["direction"], setup["timing_status"], json.dumps(setup, sort_keys=True),
                            "NOT_CHECKED" if setup["timing_status"] == "CERTIFIED" else "TIMING_UNCERTIFIABLE",
                            setup["timing_reason"]))
            meta["offset"] = handle.tell()
            if meta["total"] % 100 == 0:
                db.execute("INSERT OR REPLACE INTO metadata VALUES('source',?)", (json.dumps(meta),))
                db.commit()
                heartbeat()
    db.execute("INSERT OR REPLACE INTO metadata VALUES('source',?)", (json.dumps(meta),))
    db.commit()
    return meta


def ingest(db: sqlite3.Connection, source: Path, heartbeat=lambda: None) -> dict:
    try:
        return _ingest(db, source, heartbeat)
    except BaseException:
        db.rollback()  # discard only the uncheckpointed bounded batch
        raise


def persist(db: sqlite3.Connection, rows: list[dict]) -> int:
    """One bounded setup transaction. A changed duplicate fails, never overwrites."""
    inserted = 0
    with db:
        for row in rows:
            require_certified_outcome(row)
            encoded = json.dumps(row, sort_keys=True, allow_nan=False)
            existing = db.execute("SELECT payload FROM outcomes WHERE outcome_id=?", (row["outcome_id"],)).fetchone()
            if existing:
                if existing[0] != encoded:
                    raise RuntimeError("CONFLICTING_OUTCOME_REPLAY")
                continue
            db.execute("INSERT INTO outcomes VALUES(?,?,?,?,?)",
                       (row["outcome_id"], row["observation_id"], row["rr"], POLICY_HASH, encoded))
            inserted += 1
    return inserted


def export_jsonl(db: sqlite3.Connection, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    with temp.open("w", encoding="utf8", newline="\n") as handle:
        for row in db.execute("SELECT payload FROM outcomes ORDER BY observation_id,rr"):
            handle.write(row[0] + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def counts(db: sqlite3.Connection) -> dict:
    total = db.execute("SELECT count(*) FROM setups").fetchone()[0]
    resolved = db.execute("SELECT count(*) FROM outcomes WHERE rr=1 AND policy_hash=?", (POLICY_HASH,)).fetchone()[0]
    uncert = db.execute("SELECT count(*) FROM setups WHERE timing!='CERTIFIED'").fetchone()[0]
    failed = db.execute("SELECT count(*) FROM setups s WHERE feasibility='OTHER_FAILURE' AND NOT EXISTS(SELECT 1 FROM outcomes o WHERE o.observation_id=s.id AND rr=1)").fetchone()[0]
    timeout = db.execute("SELECT count(*) FROM outcomes WHERE rr=1 AND json_extract(payload,'$.exit_reason')='TIME_EXIT'").fetchone()[0]
    result = {"eligible_total": total, "certified_resolved": resolved, "pending": total-resolved,
              "uncertifiable": uncert, "failed_resolution": failed, "timeout": timeout,
              "pending_other": total-resolved-uncert-failed,
              "feasibility": dict(db.execute("SELECT feasibility,count(*) FROM setups GROUP BY feasibility"))}
    assert result["pending"] == uncert + failed + result["pending_other"]
    return result
