"""Prospective EURUSD outcome labels and read-only training admission.

Decision rows live in cohort.sqlite3 and are opened read-only. Final labels
live in a distinct append-only SQLite sidecar. No strategy code is evaluated.
"""
from __future__ import annotations

import hashlib
import heapq
import json
import math
import os
import sqlite3
import statistics
import time
import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Iterator

from app.core.mt5_offset import configured_offset_seconds
from app.v3.outcomes import ATR_PERIOD, MAX_HOLD, STOP_MULTIPLIER, OutcomeCandle, atr_stop_contract
from app.v3_5 import storage_health
from app.v3_5.clock_lifecycle import ClockHealthError, log_event, recertify, sleep_until
from app.v3_5.live_adapter import (
    _clock_heartbeat,
    _fresh_tick,
    _pid_exists,
    _verify_cycle_storage,
    clock_evidence,
)
from app.v3_5.provenance import POLICY, STORE, canonical, digest
from app.v3_5.state_io import (
    assert_owner,
    install_trace_heartbeat,
    read_text_shared,
    serialized_acquisition,
    write_json_atomic,
)
from app.v3_5.worker_lifecycle import finalize_worker

ROOT = Path(__file__).resolve().parents[3]
OUTCOME_DB = ROOT / "runtime/v35_provenance/outcomes_evidence_r2.sqlite3"
TICK_CACHE_DB = ROOT / "runtime/v35_provenance/outcome_tick_cache_r2.sqlite3"
LEGACY_OUTCOME_DB = ROOT / "runtime/v35_provenance/outcomes.sqlite3"
STATE_PATH = ROOT / "runtime/v35_provenance/outcome_resolver_r2_state.json"
LOCK_PATH = ROOT / "runtime/v35_provenance/outcome_resolver_r2.lock"
REPORT_PATH = ROOT / "runtime/v35_provenance/training_admission_report.json"
SYMBOL = "EURUSD"
SERVER = "ICMarketsSC-Demo"
WINDOW_SECONDS = 900
MAX_TICKS_PER_WINDOW = 100000
ENTRY_WINDOW = timedelta(seconds=60)
MAX_TICK_GAP = timedelta(seconds=300)
RR_REFERENCE = 1.0
OUTCOME_POLICY_VERSION = "v3.5-outcome-v3-outcome-r1-1R-reference-evidence-r2"
OUTCOME_POLICY_HASH = hashlib.sha256(OUTCOME_POLICY_VERSION.encode()).hexdigest()
SPLIT_PLAN = {
    "status": "PLANNED_NOT_EXECUTED",
    "method": "chronological by decision_time_utc; group overlapping label intervals before split",
    "partitions": {"TRAIN": "earliest 70% of independent temporal groups",
                   "VALIDATION": "next 15% of independent temporal groups",
                   "FINAL_TEST": "latest 15% of independent temporal groups"},
    "purge_embargo": "purge any preceding setup whose [entry,resolution_end] interval overlaps the next partition; embargo at least the maximum six-hour holding horizon plus entry window",
    "random_split": False,
    "fit_model": 0,
}


def _broker_offset_seconds() -> int:
    offset = configured_offset_seconds()
    if offset != int(POLICY["broker_offset_seconds"]):
        raise RuntimeError("V35_BROKER_OFFSET_POLICY_MISMATCH")
    return offset


def utc(value: str | datetime) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if result.tzinfo is None:
        raise ValueError("UTC_TIMESTAMP_REQUIRED")
    return result.astimezone(UTC)


def outcome_store(path: str | Path = OUTCOME_DB) -> sqlite3.Connection:
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    db = storage_health.connect(path, timeout=30)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=FULL")
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA cache_size=-2048")
    db.execute("PRAGMA temp_store=FILE")
    db.executescript("""
      CREATE TABLE IF NOT EXISTS source_observations(
        observation_id TEXT PRIMARY KEY, snapshot_sha256 TEXT NOT NULL,
        decision_time_utc TEXT NOT NULL, direction TEXT NOT NULL,
        admitted_at_utc TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS setups(
        setup_id TEXT PRIMARY KEY, observation_id TEXT NOT NULL UNIQUE REFERENCES source_observations(observation_id),
        snapshot_sha256 TEXT NOT NULL, decision_time_utc TEXT NOT NULL,
        direction TEXT NOT NULL CHECK(direction IN ('BUY_SETUP','SELL_SETUP')),
        policy_version TEXT NOT NULL, policy_hash TEXT NOT NULL,
        payload TEXT NOT NULL, appended_at_utc TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS outcomes(
        outcome_id TEXT PRIMARY KEY, setup_id TEXT NOT NULL UNIQUE REFERENCES setups(setup_id),
        status TEXT NOT NULL CHECK(status IN ('WIN','LOSS','BREAKEVEN','TIMEOUT','INVALID')),
        payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL, appended_at_utc TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS tick_evidence_windows(
        window_start_ms INTEGER NOT NULL, payload_sha256 TEXT NOT NULL,
        payload TEXT NOT NULL, acquired_at_utc TEXT NOT NULL,
        PRIMARY KEY(window_start_ms,payload_sha256));
      CREATE TABLE IF NOT EXISTS outcome_tick_windows(
        outcome_id TEXT NOT NULL REFERENCES outcomes(outcome_id), window_start_ms INTEGER NOT NULL,
        payload_sha256 TEXT NOT NULL, PRIMARY KEY(outcome_id,window_start_ms,payload_sha256),
        FOREIGN KEY(window_start_ms,payload_sha256) REFERENCES tick_evidence_windows(window_start_ms,payload_sha256));
      CREATE TRIGGER IF NOT EXISTS setups_no_update BEFORE UPDATE ON setups BEGIN SELECT RAISE(ABORT,'APPEND_ONLY'); END;
      CREATE TRIGGER IF NOT EXISTS setups_no_delete BEFORE DELETE ON setups BEGIN SELECT RAISE(ABORT,'APPEND_ONLY'); END;
      CREATE TRIGGER IF NOT EXISTS source_observations_no_update BEFORE UPDATE ON source_observations BEGIN SELECT RAISE(ABORT,'APPEND_ONLY'); END;
      CREATE TRIGGER IF NOT EXISTS source_observations_no_delete BEFORE DELETE ON source_observations BEGIN SELECT RAISE(ABORT,'APPEND_ONLY'); END;
      CREATE TRIGGER IF NOT EXISTS outcomes_no_update BEFORE UPDATE ON outcomes BEGIN SELECT RAISE(ABORT,'APPEND_ONLY'); END;
      CREATE TRIGGER IF NOT EXISTS outcomes_no_delete BEFORE DELETE ON outcomes BEGIN SELECT RAISE(ABORT,'APPEND_ONLY'); END;
      CREATE TRIGGER IF NOT EXISTS tick_evidence_windows_no_update BEFORE UPDATE ON tick_evidence_windows BEGIN SELECT RAISE(ABORT,'APPEND_ONLY'); END;
      CREATE TRIGGER IF NOT EXISTS tick_evidence_windows_no_delete BEFORE DELETE ON tick_evidence_windows BEGIN SELECT RAISE(ABORT,'APPEND_ONLY'); END;
      CREATE TRIGGER IF NOT EXISTS outcome_tick_windows_no_update BEFORE UPDATE ON outcome_tick_windows BEGIN SELECT RAISE(ABORT,'APPEND_ONLY'); END;
      CREATE TRIGGER IF NOT EXISTS outcome_tick_windows_no_delete BEFORE DELETE ON outcome_tick_windows BEGIN SELECT RAISE(ABORT,'APPEND_ONLY'); END;
      CREATE TABLE IF NOT EXISTS progress(
        setup_id TEXT PRIMARY KEY REFERENCES setups(setup_id), status TEXT NOT NULL,
        reason TEXT, last_checked_utc TEXT NOT NULL, ticks_examined INTEGER NOT NULL DEFAULT 0);
      CREATE TABLE IF NOT EXISTS resolver_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
    """)
    return db


def cache_store(path: str | Path = TICK_CACHE_DB) -> sqlite3.Connection:
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    db = storage_health.connect(path, timeout=30)
    db.execute("PRAGMA journal_mode=WAL"); db.execute("PRAGMA synchronous=FULL")
    db.execute("PRAGMA cache_size=-1024"); db.execute("PRAGMA temp_store=FILE")
    db.execute("CREATE TABLE IF NOT EXISTS tick_windows(window_start_ms INTEGER PRIMARY KEY, payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL, acquired_at_utc TEXT NOT NULL)")
    return db


def setup_identity(observation_id: str) -> str:
    return hashlib.sha256(f"{OUTCOME_POLICY_VERSION}|{observation_id}".encode()).hexdigest()


def outcome_identity(setup_id: str) -> str:
    return hashlib.sha256(f"{setup_id}|{OUTCOME_POLICY_HASH}|reference-RR-{RR_REFERENCE:g}".encode()).hexdigest()


def observe_setups(source: sqlite3.Connection, labels: sqlite3.Connection, now: datetime) -> int:
    """Copy only verified, immutable, eligible directional setup references."""
    inserted = 0
    cursor = labels.execute("SELECT value FROM resolver_meta WHERE key='observation_scan_cursor'").fetchone()
    watermark = json.loads(cursor[0]) if cursor else None
    if watermark:
        cursor_rows = source.execute("SELECT observation_id,decision_id,decision_time_utc,snapshot_sha256,payload,eligible FROM observations WHERE (decision_time_utc,observation_id)>(?,?) ORDER BY decision_time_utc,observation_id",
                                     (watermark[0], watermark[1]))
    else:
        cursor_rows = source.execute("SELECT observation_id,decision_id,decision_time_utc,snapshot_sha256,payload,eligible FROM observations ORDER BY decision_time_utc,observation_id")
    last_key = watermark
    for observation_id, decision_id, decision_time, snapshot_hash, payload_text, eligible in cursor_rows:
        last_key = [decision_time, observation_id]
        if not eligible:
            continue
        row = json.loads(payload_text)
        direction = row.get("direction")
        if direction not in {"BUY_SETUP", "SELL_SETUP"}:
            continue
        snapshot = row["snapshot"]
        if row.get("training_eligible") is not True or snapshot.get("training_eligible") is not True:
            continue
        if snapshot.get("snapshot_sha256") != snapshot_hash or digest({k: v for k, v in snapshot.items() if k != "snapshot_sha256"}) != snapshot_hash:
            raise ValueError("SOURCE_SNAPSHOT_HASH_INVALID:" + observation_id)
        if snapshot.get("decision_id") != decision_id or utc(decision_time) != utc(snapshot["decision_time_utc"]):
            raise ValueError("SOURCE_DECISION_REFERENCE_INVALID:" + observation_id)
        _verify_cycle_storage(source, observation_id)
        setup_id = setup_identity(observation_id)
        setup = {"setup_id": setup_id, "observation_id": observation_id,
                 "snapshot_sha256": snapshot_hash, "decision_time_utc": utc(decision_time).isoformat(),
                 "direction": direction, "symbol": SYMBOL, "policy_version": OUTCOME_POLICY_VERSION,
                 "policy_hash": OUTCOME_POLICY_HASH, "reference_rr": RR_REFERENCE,
                 "correlated_labels": False, "decision_snapshot_modified": False}
        text = canonical(setup).decode()
        old = labels.execute("SELECT payload FROM setups WHERE setup_id=?", (setup_id,)).fetchone()
        if old:
            if old[0] != text:
                raise ValueError("SETUP_IDENTITY_MUTATED:" + setup_id)
            continue
        with labels:
            labels.execute("INSERT OR IGNORE INTO source_observations VALUES(?,?,?,?,?)",
                           (observation_id,snapshot_hash,setup["decision_time_utc"],direction,now.isoformat()))
            labels.execute("INSERT INTO setups VALUES(?,?,?,?,?,?,?,?,?)",
                           (setup_id, observation_id, snapshot_hash, setup["decision_time_utc"], direction,
                            OUTCOME_POLICY_VERSION, OUTCOME_POLICY_HASH, text, now.isoformat()))
            labels.execute("INSERT OR IGNORE INTO progress(setup_id,status,reason,last_checked_utc,ticks_examined) VALUES(?,?,?,?,0)",
                           (setup_id, "PENDING", "AWAITING_FUTURE_EVIDENCE", now.isoformat()))
        inserted += 1
    if last_key:
        with labels:
            labels.execute("INSERT INTO resolver_meta VALUES('observation_scan_cursor',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                           (json.dumps(last_key),))
    return inserted


def _m15_candles(source: sqlite3.Connection, observation_id: str) -> tuple[list[OutcomeCandle],dict[str,dict[str,Any]]]:
    row = source.execute("SELECT payload FROM observations WHERE observation_id=?", (observation_id,)).fetchone()
    if row is None:
        raise ValueError("SOURCE_OBSERVATION_MISSING")
    obs = json.loads(row[0]); snap = obs["snapshot"]; decision = utc(snap["decision_time_utc"])
    pending = list(snap["feature_provenance_refs"].values()); visited: set[str] = set(); candles: dict[str, OutcomeCandle] = {}; bar_refs:dict[str,dict[str,Any]]={}
    while pending:
        evidence_id = pending.pop()
        if evidence_id in visited:
            continue
        visited.add(evidence_id)
        got = source.execute("SELECT payload_sha256,payload FROM provenance WHERE evidence_id=?", (evidence_id,)).fetchone()
        if not got:
            raise ValueError("SOURCE_ANCESTOR_MISSING")
        stored_hash, text = got; evidence = json.loads(text)
        if stored_hash != digest(evidence) or evidence.get("evidence_id") != evidence_id or evidence.get("provenance_status") != "CERTIFIED":
            raise ValueError("SOURCE_ANCESTOR_UNSAFE")
        if utc(evidence["information_available_at_utc"]) > decision:
            raise ValueError("SOURCE_ANCESTOR_AFTER_DECISION")
        pending.extend(evidence.get("dependencies", []))
        if evidence.get("kind") == "bar" and evidence.get("timeframe") == "M15":
            opened = utc(evidence["normalized_timestamp"]); closed = utc(evidence["bar_close_utc"])
            if closed > decision or utc(evidence["acquired_at_utc"]) > decision:
                raise ValueError("ATR_BAR_NOT_AVAILABLE_AT_DECISION")
            value = evidence["raw_value"]
            candles[evidence_id] = OutcomeCandle(opened, float(value["high"]), float(value["low"]), float(value["close"]))
            bar_refs[evidence_id]={"evidence_id":evidence_id,"payload_sha256":stored_hash,
                "open_utc":opened.isoformat(),"close_utc":closed.isoformat()}
    ordered=sorted(candles.items(),key=lambda pair:pair[1].timestamp)
    return [pair[1] for pair in ordered],{key:bar_refs[key] for key,_ in ordered}


def m15_stop_distance(source: sqlite3.Connection, observation_id: str, decision: datetime) -> tuple[float, dict[str, Any]]:
    candles,bar_refs = _m15_candles(source, observation_id)
    contract = atr_stop_contract(decision, candles)
    if contract is None:
        raise ValueError("ATR_M15_UNAVAILABLE")
    return contract.stop_distance, {"timeframe": contract.atr_timeframe, "period": contract.atr_period,
        "atr_value": contract.atr_value, "atr_timestamp_utc": contract.atr_timestamp.isoformat(),
        "stop_multiplier": contract.stop_multiplier, "stop_distance": contract.stop_distance,
        "source_candle_count": len(candles), "formula_source": "backend/app/v3/outcomes.py::atr_stop_contract",
        "ancestor_bars_used":list(bar_refs.values())[-(ATR_PERIOD+1):]}


def _valid_tick(t: int, bid: float, ask: float) -> bool:
    return t > 0 and math.isfinite(bid) and math.isfinite(ask) and 0 < bid <= ask


def aggregated_bar_resolution(*, direction: str, high: float, low: float, stop: float, target: float) -> dict[str, str]:
    """Never infer first-hit ordering from an OHLC bar without tick chronology."""
    if direction == "BUY_SETUP":
        both = low <= stop and high >= target
    elif direction == "SELL_SETUP":
        both = high >= stop and low <= target
    else:
        raise ValueError("INVALID_DIRECTION")
    if both:
        return {"status":"PENDING","reason":"AMBIGUOUS_INTRABAR_NO_TICK_CHRONOLOGY"}
    return {"status":"PENDING","reason":"BAR_ONLY_EVIDENCE_NOT_EXECUTABLE_TICK_CHRONOLOGY"}


def resolve_quotes(*, setup: dict[str, Any], ticks: Iterable[tuple], stop_distance: float,
                   stop_contract: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Resolve one 1R reference label from strictly post-decision executable quotes."""
    decision = utc(setup["decision_time_utc"]); direction = setup["direction"]
    if direction not in {"BUY_SETUP", "SELL_SETUP"} or stop_distance <= 0:
        raise ValueError("INVALID_SETUP_CONTRACT")
    side = direction.removesuffix("_SETUP"); entry_deadline = decision + ENTRY_WINDOW
    entry = None; prior = int(decision.timestamp() * 1000); trace = hashlib.sha256(); count = 0
    window_refs: dict[tuple[int,str],dict[str,Any]] = {}
    exit = None; invalid = None; horizon = None
    for tick_item in ticks:
        timestamp_ms, bid, ask = tick_item[:3]
        raw_timestamp_ms = tick_item[3] if len(tick_item) > 3 else None
        window_start_ms = tick_item[4] if len(tick_item) > 4 else None
        window_hash = tick_item[5] if len(tick_item) > 5 else None
        if timestamp_ms <= int(decision.timestamp() * 1000):
            continue
        if not _valid_tick(timestamp_ms, bid, ask):
            invalid = "INVALID_FUTURE_BID_ASK"; break
        if window_start_ms is not None and window_hash:
            key=(int(window_start_ms),str(window_hash))
            window_refs[key]={"window_start_ms":key[0],"payload_sha256":key[1]}
        if timestamp_ms < prior:
            invalid = "NON_MONOTONIC_FUTURE_TICKS"; break
        timestamp = datetime.fromtimestamp(timestamp_ms / 1000, UTC)
        if entry is None:
            if timestamp > entry_deadline:
                invalid = "NO_EXECUTABLE_ENTRY_WITHIN_60_SECONDS"; break
            gap = (timestamp_ms - int(decision.timestamp() * 1000)) / 1000
            if gap > ENTRY_WINDOW.total_seconds():
                invalid = "ENTRY_COVERAGE_GAP"; break
            entry_price = ask if side == "BUY" else bid
            entry = {"timestamp_ms": timestamp_ms, "timestamp_utc": timestamp.isoformat(), "price": entry_price,
                     "bid": bid, "ask": ask, "spread": ask - bid,
                     "raw_broker_timestamp_ms": raw_timestamp_ms}
            horizon = timestamp + MAX_HOLD
            sign = 1 if side == "BUY" else -1
            stop = entry_price - sign * stop_distance
            target = entry_price + sign * stop_distance * RR_REFERENCE
            prior = timestamp_ms
            trace.update(canonical([timestamp_ms, bid, ask, raw_timestamp_ms])); count += 1
            continue
        if timestamp_ms == prior:
            # Identical-millisecond provider records cannot establish ordering.
            invalid = "AMBIGUOUS_SAME_MILLISECOND_QUOTES"; break
        if (timestamp_ms - prior) / 1000 > MAX_TICK_GAP.total_seconds():
            invalid = "FUTURE_TICK_COVERAGE_GAP_OVER_300_SECONDS"; break
        prior = timestamp_ms
        trace.update(canonical([timestamp_ms, bid, ask, raw_timestamp_ms])); count += 1
        executable_exit = bid if side == "BUY" else ask
        stop_hit = executable_exit <= stop if side == "BUY" else executable_exit >= stop
        target_hit = executable_exit >= target if side == "BUY" else executable_exit <= target
        if timestamp <= horizon and stop_hit:
            gross_r = ((executable_exit - entry["price"]) / stop_distance) * sign
            exit = ("LOSS", "SL_HIT", timestamp_ms, executable_exit, gross_r, bid, ask, raw_timestamp_ms); break
        if timestamp <= horizon and target_hit:
            gross_r = ((executable_exit - entry["price"]) / stop_distance) * sign
            exit = ("WIN", "TP_HIT", timestamp_ms, executable_exit, gross_r, bid, ask, raw_timestamp_ms); break
        if timestamp >= horizon:
            gross_r = ((executable_exit - entry["price"]) / stop_distance) * sign
            status = "BREAKEVEN" if abs(gross_r) < 1e-12 else "TIMEOUT"
            exit = (status, "TIMEOUT", timestamp_ms, executable_exit, gross_r, bid, ask, raw_timestamp_ms); break
    if entry is None:
        if now <= entry_deadline:
            return {"status": "PENDING", "reason": "ENTRY_WINDOW_NOT_COMPLETE", "ticks_examined": count,"tick_window_refs":list(window_refs.values())}
        invalid = invalid or "NO_EXECUTABLE_ENTRY_WITHIN_60_SECONDS"
    elif exit is None and invalid is None:
        if horizon is not None and now > horizon + timedelta(seconds=60):
            invalid = "TIMEOUT_EXIT_QUOTE_UNAVAILABLE"
        else:
            return {"status": "PENDING", "reason": "SIX_HOUR_OUTCOME_HORIZON_INCOMPLETE",
                    "entry_timestamp_utc": entry["timestamp_utc"], "ticks_examined": count,
                    "resolution_start_utc": entry["timestamp_utc"], "resolution_end_utc": now.isoformat(),"tick_window_refs":list(window_refs.values())}
    if invalid:
        return {"status": "INVALID", "reason": invalid, "entry": entry, "ticks_examined": count,"tick_window_refs":list(window_refs.values())}
    assert entry is not None and exit is not None and horizon is not None
    status, reason, exit_ms, exit_price, gross_r, exit_bid, exit_ask, exit_raw_ms = exit
    exit_time = datetime.fromtimestamp(exit_ms / 1000, UTC)
    if exit_time <= decision:
        raise ValueError("OUTCOME_NOT_AFTER_DECISION")
    return {"status": status, "reason": reason, "entry": entry, "stop_price": stop, "target_price": target,
            "exit_timestamp_ms": exit_ms, "exit_raw_broker_timestamp_ms": exit_raw_ms,
            "exit_timestamp_utc": exit_time.isoformat(), "exit_price": exit_price,
            "exit_bid": exit_bid, "exit_ask": exit_ask,
            "gross_r": gross_r, "ticks_examined": count, "trace_sha256": trace.hexdigest(),
            "tick_window_refs":list(window_refs.values()),
            "max_hold_timestamp_utc": horizon.isoformat(), "stop_contract": stop_contract}


class ReadOnlyMT5Ticks:
    """Narrow read-only market-data facade: exposes no execution methods."""
    def __init__(self, mt5: Any, cache: sqlite3.Connection, evidence_db: sqlite3.Connection, heartbeat=lambda: None):
        self._mt5, self.cache, self.evidence_db, self.heartbeat = mt5, cache, evidence_db, heartbeat
        self.offset_seconds = _broker_offset_seconds()
        self.calls = 0; self.max_window_ticks = 0

    def _window(self, window_ms: int, now: datetime) -> list[list[Any]]:
        self.heartbeat()
        win_end_ms = window_ms + WINDOW_SECONDS * 1000
        if win_end_ms < int(now.timestamp() * 1000):
            row = self.cache.execute("SELECT payload,payload_sha256 FROM tick_windows WHERE window_start_ms=?", (window_ms,)).fetchone()
            if row:
                if hashlib.sha256(row[0].encode()).hexdigest() != row[1]:
                    raise ValueError("TICK_CACHE_HASH_FAILURE")
                rows=json.loads(row[0]); self._persist_window(window_ms,row[0],row[1],now)
                return rows,row[1]
        # MT5 expects broker-server epoch; use the shared validated setting.
        raw_start = datetime.fromtimestamp(window_ms / 1000 + self.offset_seconds, UTC)
        raw_end = datetime.fromtimestamp(win_end_ms / 1000 + self.offset_seconds, UTC)
        raw = self._mt5.copy_ticks_range(SYMBOL, raw_start, raw_end, self._mt5.COPY_TICKS_ALL)
        self.calls += 1; self.heartbeat()
        if raw is None:
            raise RuntimeError(f"MT5_TICKS_UNAVAILABLE:{self._mt5.last_error()}")
        if len(raw) > MAX_TICKS_PER_WINDOW:
            raise RuntimeError("TICK_WINDOW_LIMIT_EXCEEDED")
        rows = []
        for tick in raw:
            raw_ms = int(tick["time_msc"])
            if raw_ms <= 0:
                raise ValueError("MILLISECOND_TIMESTAMP_UNAVAILABLE")
            normalized_ms = raw_ms - self.offset_seconds * 1000
            if window_ms <= normalized_ms < win_end_ms and normalized_ms <= int(now.timestamp()*1000):
                rows.append([normalized_ms, float(tick["bid"]), float(tick["ask"]), raw_ms])
        rows.sort(key=lambda x: x[0]); self.max_window_ticks = max(self.max_window_ticks, len(rows))
        if any(a[0]==b[0] for a,b in zip(rows,rows[1:])):
            raise ValueError("AMBIGUOUS_SAME_MILLISECOND_QUOTES")
        text = json.dumps(rows, separators=(",", ":")); h = hashlib.sha256(text.encode()).hexdigest()
        self._persist_window(window_ms,text,h,now)
        if win_end_ms < int(now.timestamp() * 1000) and rows:
            with self.cache:
                self.cache.execute("INSERT OR IGNORE INTO tick_windows VALUES(?,?,?,?)",
                                   (window_ms, text, h, now.isoformat()))
        return rows,h

    def _persist_window(self,window_ms:int,text:str,payload_hash:str,now:datetime)->None:
        if hashlib.sha256(text.encode()).hexdigest()!=payload_hash:
            raise ValueError("TICK_WINDOW_HASH_FAILURE")
        with self.evidence_db:
            self.evidence_db.execute("INSERT OR IGNORE INTO tick_evidence_windows VALUES(?,?,?,?)",
                                     (window_ms,payload_hash,text,now.isoformat()))

    def ticks(self, start: datetime, end: datetime, now: datetime) -> Iterator[tuple]:
        start_ms = int(start.timestamp() * 1000); end_ms = min(int(end.timestamp() * 1000), int(now.timestamp() * 1000))
        first = start_ms // (WINDOW_SECONDS * 1000) * (WINDOW_SECONDS * 1000)
        last = end_ms // (WINDOW_SECONDS * 1000) * (WINDOW_SECONDS * 1000)
        previous = None
        for window in range(first, last + 1, WINDOW_SECONDS * 1000):
            rows,window_hash = self._window(window, now)
            for stamp, bid, ask, raw_ms in rows:
                if start_ms < stamp <= end_ms:
                    if previous is not None and stamp < previous:
                        raise ValueError("NON_MONOTONIC_FUTURE_TICKS")
                    previous = stamp
                    yield stamp, bid, ask, raw_ms, window, window_hash


def _write_json_atomic(path: Path, value: dict[str, Any], *, preserve_failed_temp: bool = False) -> None:
    write_json_atomic(path, value, preserve_failed_temp=preserve_failed_temp)

@serialized_acquisition
def _acquire_lock(now: datetime) -> dict[str, Any]:
    owner = {"pid": os.getpid(), "worker_id": str(uuid.uuid4()), "acquired_at_utc": now.isoformat(),
             "worker_identity": "v3.5-eurusd-resolver", "startup_at_utc": now.isoformat()}
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    if STATE_PATH.exists():
        prior = json.loads(read_text_shared(STATE_PATH))
        if prior.get("status") in ("STARTING", "RUNNING", "CLOCK_UNCERTIFIED_TRANSIENT") and _pid_exists(int(prior.get("pid", 0))) is not False:
            raise RuntimeError("V35_RESOLVER_STATE_LIVE_OWNER_OR_UNVERIFIED")
    try:
        fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        original = read_text_shared(LOCK_PATH).encode("utf8")
        old = json.loads(original); pid = int(old.get("pid", -1))
        alive = _pid_exists(pid)
        hb = old.get("heartbeat_at_utc")
        stale = False
        if hb:
            try: stale = (now - utc(hb)).total_seconds() > 60
            except ValueError: stale = False
        if alive is not False or not stale:
            raise RuntimeError("OUTCOME_RESOLVER_LOCK_HELD_OR_OWNER_UNVERIFIED")
        archive = LOCK_PATH.with_name(f"{LOCK_PATH.name}.stale.{int(now.timestamp())}")
        if read_text_shared(LOCK_PATH).encode("utf8") != original:
            raise RuntimeError("V35_LOCK_CHANGED_DURING_RECOVERY")
        os.replace(LOCK_PATH, archive)
        fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf8") as handle:
        json.dump(owner, handle); handle.flush(); os.fsync(handle.fileno())
    # Orphaned temp files may contain the only complete shutdown candidate;
    # preserve them for explicit recovery review rather than pruning at startup.
    return owner


def _state(owner: dict[str, Any], *, status: str, started: str, processed: int, resolved: int,
           pending: int, invalid: int, last_success: str | None, last_error: str | None,
           cycle_duration: float | None, memory: dict[str, int | None], reason: str | None = None,
           cleanup_errors: list[dict[str, str]] | None = None,
           preserve_failed_temp: bool = False) -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    footprint=lambda p:sum(q.stat().st_size for q in (p,Path(str(p)+"-wal"),Path(str(p)+"-shm")) if q.exists())
    state = {**owner, "worker_id": owner["worker_id"], "collector_id": "v3.5-eurusd-outcome-resolver-evidence-r2",
        "status": status, "lifecycle_status":"HEALTHY" if status=="RUNNING" else status, "startup_at_utc": started, "heartbeat_at_utc": now,
        "last_successful_cycle_utc": last_success, "last_error": last_error,
        "reason": reason, "cleanup_errors": cleanup_errors or [],
        "processed_count": processed, "resolved_count": resolved, "pending_count": pending,
        "invalid_count": invalid, "cycle_duration_seconds": cycle_duration,
        "working_set_bytes": memory.get("working_set_bytes"), "peak_working_set_bytes": memory.get("peak_working_set_bytes"),
        "outcome_database_bytes": footprint(OUTCOME_DB),
        "tick_cache_database_bytes": footprint(TICK_CACHE_DB),
        "fit_model": 0, "order_send": 0, "demo_execution": False, "real_execution": False,
        "storage_health": storage_health.sample(OUTCOME_DB)}
    assert_owner(LOCK_PATH, owner)
    _write_json_atomic(STATE_PATH, state, preserve_failed_temp=preserve_failed_temp)
    if LOCK_PATH.exists() and not preserve_failed_temp:
        _write_json_atomic(LOCK_PATH, {k: state[k] for k in ("pid", "worker_id", "acquired_at_utc", "heartbeat_at_utc")})
    return state


def _memory() -> dict[str, int | None]:
    try:
        import ctypes
        class Counters(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t), ("PrivateUsage", ctypes.c_size_t)]
        psapi=ctypes.WinDLL("psapi",use_last_error=True); kernel=ctypes.WinDLL("kernel32",use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes=[ctypes.c_void_p,ctypes.POINTER(Counters),ctypes.c_ulong]
        psapi.GetProcessMemoryInfo.restype=ctypes.c_int
        kernel.GetCurrentProcess.restype=ctypes.c_void_p
        value=Counters(); value.cb=ctypes.sizeof(value)
        if psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(),ctypes.byref(value),value.cb):
            return {"working_set_bytes":int(value.WorkingSetSize),"peak_working_set_bytes":int(value.PeakWorkingSetSize)}
    except Exception:
        pass
    return {"working_set_bytes":None,"peak_working_set_bytes":None}


class CollectorClockPaused(ClockHealthError):
    pass


def verify_collector_health(source: sqlite3.Connection, now: datetime | None = None,
                            *, certify_clock: bool = True, deadline_monotonic: float | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    state_path = ROOT / "runtime/v35_provenance/live_adapter_state.json"
    lock_path = ROOT / "runtime/v35_provenance/live_adapter.lock"
    if not state_path.exists() or not lock_path.exists():
        raise RuntimeError("V35_COLLECTOR_STATE_OR_LOCK_MISSING")
    state=json.loads(read_text_shared(state_path)); lock=json.loads(read_text_shared(lock_path))
    if state.get("pid")!=lock.get("pid") or state.get("worker_id")!=lock.get("worker_id"):
        raise RuntimeError("V35_COLLECTOR_LOCK_OWNER_MISMATCH")
    if _pid_exists(int(state.get("pid",-1))) is not True:
        raise RuntimeError("V35_COLLECTOR_PID_NOT_RUNNING")
    heartbeat=utc(state["heartbeat_at_utc"]); age=(now-heartbeat).total_seconds()
    if state.get("status")=="CLOCK_UNCERTIFIED_TRANSIENT":
        raise CollectorClockPaused("V35_COLLECTOR_CLOCK_UNCERTIFIED_TRANSIENT", {"collector_state":state})
    if state.get("status")!="RUNNING" or state.get("last_error"):
        raise RuntimeError("V35_COLLECTOR_NOT_HEALTHY")
    if age<0 or age>30:
        raise CollectorClockPaused("V35_COLLECTOR_HEARTBEAT_STALE", {"collector_pid":state["pid"],"heartbeat_age_seconds":age})
    current=int(source.execute("SELECT count(*) FROM observations").fetchone()[0])
    persisted=int(state.get("observations_count",-1))
    if current<persisted or current-persisted>1:
        raise RuntimeError("V35_COLLECTOR_OBSERVATION_COUNT_DIVERGENCE")
    clock=(clock_evidence() if deadline_monotonic is None else clock_evidence(deadline_monotonic=deadline_monotonic)) if certify_clock else {"clock_safe":True,"check":"DEPENDENCY_ONLY_FULL_CLOCK_REQUIRED_AT_CYCLE_GATE"}
    if not clock.get("clock_safe"):
        raise RuntimeError("V35_RESOLVER_CLOCK_UNCERTIFIED")
    return {"status":"PASS","pid":state["pid"],"heartbeat_at_utc":state["heartbeat_at_utc"],
        "heartbeat_age_seconds":age,"observation_count":current,"clock_evidence":clock}


def resolver_cycle(source: sqlite3.Connection, labels: sqlite3.Connection, tick_source: ReadOnlyMT5Ticks,
                   now: datetime | None = None, *, health_guard=None) -> dict[str, Any]:
    if health_guard is not None: health_guard()
    now = now or datetime.now(UTC); new_setups = observe_setups(source, labels, now)
    rows = labels.execute("SELECT s.setup_id,s.observation_id,s.snapshot_sha256,s.decision_time_utc,s.direction,s.payload FROM setups s LEFT JOIN outcomes o USING(setup_id) WHERE o.outcome_id IS NULL ORDER BY s.decision_time_utc").fetchall()
    processed = resolved = pending = invalid = 0; reasons: Counter[str] = Counter()
    for setup_id, observation_id, snapshot_hash, decision_text, direction, setup_text in rows:
        if health_guard is not None: health_guard()
        processed += 1; decision = utc(decision_text); setup = json.loads(setup_text)
        # Recheck that the cross-database source is still the same immutable row.
        original = source.execute("SELECT snapshot_sha256,eligible,payload FROM observations WHERE observation_id=?", (observation_id,)).fetchone()
        if not original or original[0] != snapshot_hash or not original[1]:
            result = {"status": "INVALID", "reason": "SOURCE_OBSERVATION_REFERENCE_FAILURE"}
        else:
            stop_distance, stop_contract = m15_stop_distance(source, observation_id, decision)
            end = min(decision + ENTRY_WINDOW + MAX_HOLD + timedelta(minutes=1), now)
            try:
                result = resolve_quotes(setup=setup, ticks=tick_source.ticks(decision, end, now),
                                        stop_distance=stop_distance, stop_contract=stop_contract, now=now)
            except ClockHealthError:
                raise
            except (ValueError, RuntimeError) as exc:
                result = {"status": "PENDING" if "UNAVAILABLE" in str(exc) else "INVALID", "reason": str(exc)}
        status = result["status"]
        reasons[result.get("reason", "")] += 1
        if health_guard is not None: health_guard()
        if status == "PENDING":
            pending += 1
            with labels:
                labels.execute("INSERT INTO progress VALUES(?,?,?,?,?) ON CONFLICT(setup_id) DO UPDATE SET status=excluded.status,reason=excluded.reason,last_checked_utc=excluded.last_checked_utc,ticks_examined=excluded.ticks_examined",
                               (setup_id,status,result.get("reason"),now.isoformat(),result.get("ticks_examined",0)))
            continue
        if status == "INVALID":
            invalid += 1
        result_record = {"outcome_id": outcome_identity(setup_id), "setup_id": setup_id,
            "observation_id": observation_id, "observation_snapshot_sha256": snapshot_hash,
            "symbol": SYMBOL, "direction": direction.removesuffix("_SETUP"),
            "decision_time_utc": decision.isoformat(), "entry_rule": "first valid quote strictly after decision, no later than 60 seconds; BUY ask / SELL bid",
            "entry_price": result.get("entry",{}).get("price"), "entry_bid": result.get("entry",{}).get("bid"),
            "entry_ask": result.get("entry",{}).get("ask"), "entry_spread": result.get("entry",{}).get("spread"),
            "stop_price": result.get("stop_price"), "target_price": result.get("target_price"),
            "rr_contract": {"reference_rr":RR_REFERENCE,"correlated_labels":False,"source_policy_version":"v3-outcome-r1",
                "outcome_policy_version":OUTCOME_POLICY_VERSION,"outcome_policy_hash":OUTCOME_POLICY_HASH,
                "atr_timeframe":"M15","atr_period":ATR_PERIOD,"stop_multiplier":STOP_MULTIPLIER,"max_hold_seconds":int(MAX_HOLD.total_seconds())},
            "stop_contract": result.get("stop_contract"), "resolution_start_utc":result.get("entry",{}).get("timestamp_utc"),
            "resolution_end_utc":result.get("exit_timestamp_utc") or result.get("resolution_end_utc"),
            "exit_reason":result.get("reason"), "exit_timestamp_utc":result.get("exit_timestamp_utc"),
            "exit_price":result.get("exit_price"), "exit_bid":result.get("exit_bid"), "exit_ask":result.get("exit_ask"),
            "status":status,"gross_r":result.get("gross_r"),"realized_R":result.get("gross_r"),"net_r":None,
            "spread_cost_r":None,"commission_cost_r":None,"slippage_cost_r":None,"swap_cost_r":None,
            "cost_model_incomplete":True,"cost_status":"ACTUAL_COMMISSION_SLIPPAGE_SWAP_UNAVAILABLE",
            "entry_tick_timestamp_ms":result.get("entry",{}).get("timestamp_ms"),
            "entry_raw_broker_timestamp_ms":result.get("entry",{}).get("raw_broker_timestamp_ms"),
            "exit_tick_timestamp_ms":result.get("exit_timestamp_ms"),
            "exit_raw_broker_timestamp_ms":result.get("exit_raw_broker_timestamp_ms"),"source":"MT5_READ_ONLY_COPY_TICKS_RANGE",
            "broker_server":SERVER,"configured_offset_seconds":_broker_offset_seconds(),"future_tick_trace_sha256":result.get("trace_sha256"),
            "tick_window_refs":result.get("tick_window_refs",[]),
            "outcome_policy_version":OUTCOME_POLICY_VERSION,"outcome_policy_hash":OUTCOME_POLICY_HASH,
            "future_ticks_examined":result.get("ticks_examined",0),"no_lookahead_verified":True,
            "future_data_label_only":True,"decision_snapshot_modified":False,"decision_training_eligibility_modified":False,
            "outcome_status_reason":result.get("reason"),"resolved_at_utc":now.isoformat()}
        # One sidecar transaction stores terminal outcome and progress atomically.
        with labels:
            old=labels.execute("SELECT payload FROM outcomes WHERE setup_id=?",(setup_id,)).fetchone()
            if old:
                if old[0] != canonical(result_record).decode(): raise ValueError("OUTCOME_IDENTITY_MUTATED")
            else:
                for ref in result_record["tick_window_refs"]:
                    present=labels.execute("SELECT 1 FROM tick_evidence_windows WHERE window_start_ms=? AND payload_sha256=?",
                        (ref["window_start_ms"],ref["payload_sha256"])).fetchone()
                    if not present: raise ValueError("TICK_EVIDENCE_WINDOW_NOT_DURABLE")
                encoded=canonical(result_record).decode()
                labels.execute("INSERT INTO outcomes VALUES(?,?,?,?,?,?)",(result_record["outcome_id"],setup_id,status,encoded,hashlib.sha256(encoded.encode()).hexdigest(),now.isoformat()))
                labels.executemany("INSERT INTO outcome_tick_windows VALUES(?,?,?)",
                    [(result_record["outcome_id"],ref["window_start_ms"],ref["payload_sha256"]) for ref in result_record["tick_window_refs"]])
            labels.execute("INSERT INTO progress VALUES(?,?,?,?,?) ON CONFLICT(setup_id) DO UPDATE SET status=excluded.status,reason=excluded.reason,last_checked_utc=excluded.last_checked_utc,ticks_examined=excluded.ticks_examined",
                           (setup_id,status,result.get("reason"),now.isoformat(),result.get("ticks_examined",0)))
        if status != "INVALID": resolved += 1
    counts = dict(labels.execute("SELECT status,count(*) FROM progress GROUP BY status").fetchall())
    return {"new_setups":new_setups,"processed":processed,"resolved_this_cycle":resolved,
        "pending":counts.get("PENDING",0),"invalid":counts.get("INVALID",0),"reasons":dict(reasons),
        "tick_calls":tick_source.calls,"max_ticks_per_window":tick_source.max_window_ticks}


def training_admission_report(source: sqlite3.Connection, labels: sqlite3.Connection,
                              now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    obs_counts = source.execute("SELECT count(*),coalesce(sum(eligible),0) FROM observations").fetchone()
    directions = dict(source.execute("SELECT json_extract(payload,'$.direction'),count(*) FROM observations GROUP BY 1").fetchall())
    resolutions=[]
    for p,s,payload_hash in labels.execute("SELECT payload,status,payload_sha256 FROM outcomes"):
        if hashlib.sha256(p.encode()).hexdigest()!=payload_hash:
            raise ValueError("OUTCOME_PAYLOAD_HASH_FAILURE")
        payload=json.loads(p)
        if payload.get("decision_snapshot_modified") is not False or payload.get("no_lookahead_verified") is not True:
            raise ValueError("OUTCOME_ISOLATION_CONTRACT_FAILURE")
        if payload.get("outcome_policy_version")!=OUTCOME_POLICY_VERSION or payload.get("outcome_policy_hash")!=OUTCOME_POLICY_HASH:
            raise ValueError("OUTCOME_POLICY_VERSION_MISMATCH")
        if s!="INVALID":
            _verify_label_trace(source,labels,payload)
        resolutions.append((payload,s))
    progress = dict(labels.execute("SELECT status,count(*) FROM progress GROUP BY status").fetchall())
    outcome_by_setup = {payload["setup_id"]:(payload,status) for payload,status in resolutions}
    setup_rows = labels.execute("SELECT setup_id,observation_id,direction,decision_time_utc,payload FROM setups").fetchall()
    side_total=Counter(); side_status=Counter(); values=[]; dates=set(); per_date=Counter(); sessions=Counter(); regimes=Counter(); families=Counter(); holding=[]; clusters=Counter()
    decision_times=[]; directed_times=[]
    for sid,oid,direction,decision_text,setup_payload in setup_rows:
        side=direction.removesuffix("_SETUP"); side_total[side]+=1
        decision=utc(decision_text); decision_times.append(decision)
        directed_times.append((decision,side))
        side_status[(side, outcome_by_setup.get(sid,(None,"PENDING"))[1])]+=1
        if sid not in outcome_by_setup: continue
        payload,status=outcome_by_setup[sid]
        if status=="INVALID": continue
        dates.add(decision.date().isoformat());per_date[decision.date().isoformat()]+=1
        source_row=source.execute("SELECT payload FROM observations WHERE observation_id=?",(oid,)).fetchone()
        if not source_row: continue
        snapshot=json.loads(source_row[0])["snapshot"]; features=snapshot["feature_values"]; outputs=snapshot["decision_outputs"].get("families",{})
        sessions[str(features.get("session","UNKNOWN"))]+=1;regimes[str(features.get("regime","UNKNOWN"))]+=1
        for family,f in outputs.items():
            if f.get("setup") and f.get("side")==side: families[family]+=1
        if payload.get("exit_timestamp_utc"):
            holding.append((decision,utc(payload["entry_price"] if False else payload["resolution_start_utc"] or decision),utc(payload["exit_timestamp_utc"])))
        if payload.get("realized_R") is not None: values.append((side,status,float(payload["realized_R"])))
    sorted_dec=sorted(decision_times)
    window_stats={}
    for period,label in ((60,"M1"),(300,"M5"),(3600,"H1")):
        buckets=Counter(int(dt.timestamp())//period for dt in sorted_dec)
        load=Counter(buckets.values())
        window_stats[label]={"nonempty_windows":len(buckets),"max_setups_in_window":max(buckets.values(),default=0),
            "windows_by_setup_count":dict(sorted(load.items()))}
        per_side=defaultdict(list)
        for dt,side in directed_times:per_side[side].append(dt)
        clusters[f"same_direction_adjacent_pairs_within_{label}"]=sum(
            1 for side_times in per_side.values() for a,b in zip(sorted(side_times),sorted(side_times)[1:])
            if (b-a).total_seconds()<=period)
    overlap_pairs=0; active=[]
    for entry_at,exit_at in sorted(((entry,exit_) for _,entry,exit_ in holding),key=lambda x:x[0]):
        while active and active[0]<=entry_at:heapq.heappop(active)
        overlap_pairs+=len(active);heapq.heappush(active,exit_at)
    wins=[v for _,_,v in values if v>0]; losses=[v for _,_,v in values if v<0]
    gross=sum(v for _,_,v in values); gross_profit=sum(wins); gross_loss=abs(sum(losses))
    n=len(values); expectancy=gross/n if n else None
    outcomes_n=len(resolutions); resolved=len([x for _,x in resolutions if x!="INVALID"])
    total_directional=len(setup_rows)
    legacy_count=0; legacy_statuses={}
    if LEGACY_OUTCOME_DB.exists():
        legacy=sqlite3.connect(f"file:{LEGACY_OUTCOME_DB}?mode=ro",uri=True)
        try:
            legacy_count=legacy.execute("SELECT count(*) FROM outcomes").fetchone()[0]
            legacy_statuses=dict(legacy.execute("SELECT status,count(*) FROM outcomes GROUP BY status").fetchall())
        finally:legacy.close()
    return {"as_of_utc":now.isoformat(),"target":"EURUSD","total_observations":obs_counts[0],
        "eligible_observations":obs_counts[1],"no_setup":directions.get("NO_SETUP",0),"excluded":directions.get("EXCLUDED",0),
        "buy_setups":side_total["BUY"],"sell_setups":side_total["SELL"],"total_directional_setups":total_directional,
        "resolved":resolved,"pending":progress.get("PENDING",0),"invalid":progress.get("INVALID",0),
        "resolved_buy":sum(side_status[("BUY",x)] for x in ("WIN","LOSS","BREAKEVEN","TIMEOUT")),
        "resolved_sell":sum(side_status[("SELL",x)] for x in ("WIN","LOSS","BREAKEVEN","TIMEOUT")),
        "pending_buy":side_status[("BUY","PENDING")],"pending_sell":side_status[("SELL","PENDING")],
        "buy_wins":side_status[("BUY","WIN")],"buy_losses":side_status[("BUY","LOSS")],
        "sell_wins":side_status[("SELL","WIN")],"sell_losses":side_status[("SELL","LOSS")],
        "breakeven":side_status[("BUY","BREAKEVEN")]+side_status[("SELL","BREAKEVEN")],
        "timeouts":side_status[("BUY","TIMEOUT")]+side_status[("SELL","TIMEOUT")],
        "win_rate":(len(wins)/(len(wins)+len(losses))) if wins or losses else None,
        "average_win_r":statistics.mean(wins) if wins else None,"average_loss_r":statistics.mean(losses) if losses else None,
        "expectancy_gross_r":expectancy,"profit_factor_gross":(gross_profit/gross_loss if gross_loss else (None if not gross_profit else "INF")),
        "gross_r_total":gross,"cost_model_incomplete":True,"net_expectancy_r":None,
        "distinct_resolved_dates":len(dates),"setups_per_date":dict(per_date),"setups_per_time_window":window_stats,
        "direction_balance":{"buy":side_total["BUY"],"sell":side_total["SELL"]},
        "family_distribution":dict(families),"session_distribution":dict(sessions),"regime_distribution":dict(regimes),
        "temporal_dependence":{"pairwise_overlapping_holding_periods":overlap_pairs,"clustering_adjacent_setup_pairs":dict(clusters),
            "distinct_decision_times":len(set(decision_times)),"effective_sample_size":"NOT_DEFENSIBLY_ESTIMATED"},
        "feature_provenance":"verified before setup admission; source snapshots unchanged",
        "no_lookahead":"PASS; labels consume strictly post-decision ticks and never feed snapshots",
        "outcome_policy":{"version":OUTCOME_POLICY_VERSION,"hash":OUTCOME_POLICY_HASH,"reference_rr":RR_REFERENCE,
            "correlated_labels":False,"atr_stop":"M15 ATR(14) * 1.5","max_hold_seconds":int(MAX_HOLD.total_seconds())},
        "legacy_outcomes_quarantined":{"store":str(LEGACY_OUTCOME_DB),"count":legacy_count,"status_counts":legacy_statuses,
            "certified_for_performance_or_training":False,"reason":"LEGACY_R1_OUTCOMES_LACK_DURABLE_TICK_WINDOW_REFERENCES"},
        "future_chronological_split_plan":SPLIT_PLAN,"safe_to_train":False,
        "training_admission_blockers":["NO_RESOLVED_OUTCOMES_YET" if resolved==0 else "INSUFFICIENT_COHORT_COVERAGE",
            "ACTUAL_COMMISSION_SLIPPAGE_SWAP_UNAVAILABLE","TEMPORAL_EFFECTIVE_SAMPLE_SIZE_NOT_ESTIMATED",
            "CHRONOLOGICAL_PURGED_SPLIT_NOT_YET_FEASIBLE","TRAINING_PROHIBITED_BY_CAMPAIGN"],
        "fit_model":0,"order_send":0,"demo_execution":False,"real_execution":False}


def _verify_label_trace(source:sqlite3.Connection,labels:sqlite3.Connection,payload:dict[str,Any])->None:
    source_row=source.execute("SELECT snapshot_sha256,eligible,payload FROM observations WHERE observation_id=?",(payload["observation_id"],)).fetchone()
    if not source_row or not source_row[1] or source_row[0]!=payload["observation_snapshot_sha256"]:
        raise ValueError("OUTCOME_SOURCE_OBSERVATION_REFERENCE_FAILURE")
    source_observation=json.loads(source_row[2]);source_snapshot=source_observation["snapshot"]
    if source_observation.get("direction") not in {"BUY_SETUP","SELL_SETUP"} or source_snapshot.get("training_eligible") is not True:
        raise ValueError("OUTCOME_SOURCE_SETUP_NOT_ELIGIBLE_DIRECTIONAL")
    if source_snapshot.get("snapshot_sha256")!=source_row[0] or digest({k:v for k,v in source_snapshot.items() if k!="snapshot_sha256"})!=source_row[0]:
        raise ValueError("OUTCOME_SOURCE_SNAPSHOT_MUTATED")
    _verify_cycle_storage(source,payload["observation_id"])
    refs=payload.get("tick_window_refs") or []
    if not refs:raise ValueError("OUTCOME_TICK_TRACE_REFERENCE_MISSING")
    decision_ms=int(utc(payload["decision_time_utc"]).timestamp()*1000)
    exit_ms=int(payload["exit_tick_timestamp_ms"])
    collected=[];ref_keys=set()
    for ref in refs:
        key=(int(ref["window_start_ms"]),str(ref["payload_sha256"]));ref_keys.add(key)
        row=labels.execute("SELECT payload,payload_sha256 FROM tick_evidence_windows WHERE window_start_ms=? AND payload_sha256=?",key).fetchone()
        if not row or hashlib.sha256(row[0].encode()).hexdigest()!=row[1]:raise ValueError("OUTCOME_TICK_WINDOW_HASH_FAILURE")
        for item in json.loads(row[0]):
            stamp,bid,ask,raw_ms=item
            if int(raw_ms)-_broker_offset_seconds()*1000!=int(stamp):raise ValueError("OUTCOME_TICK_NORMALIZATION_FAILURE")
            if decision_ms<int(stamp)<=exit_ms:collected.append((int(stamp),float(bid),float(ask),int(raw_ms)))
    linked={(int(a),str(b)) for a,b in labels.execute("SELECT window_start_ms,payload_sha256 FROM outcome_tick_windows WHERE outcome_id=?",(payload["outcome_id"],))}
    if linked!=ref_keys:raise ValueError("OUTCOME_TICK_WINDOW_LINKAGE_FAILURE")
    collected.sort(key=lambda x:x[0]);trace=hashlib.sha256()
    for item in collected:trace.update(canonical(list(item)))
    if trace.hexdigest()!=payload.get("future_tick_trace_sha256") or len(collected)!=payload.get("future_ticks_examined"):
        raise ValueError("OUTCOME_FIRST_HIT_TRACE_MISMATCH")
    entry_ms=int(payload["entry_tick_timestamp_ms"]);exit_ms=int(payload["exit_tick_timestamp_ms"])
    if entry_ms<=decision_ms or exit_ms<=decision_ms:raise ValueError("OUTCOME_NOT_STRICTLY_POST_DECISION")
    entry=next((q for q in collected if q[0]==entry_ms),None);exitq=next((q for q in collected if q[0]==exit_ms),None)
    if entry is None or exitq is None:raise ValueError("OUTCOME_ENTRY_EXIT_TICK_MISSING")
    side=payload["direction"]
    if payload["entry_price"]!=(entry[2] if side=="BUY" else entry[1]):raise ValueError("OUTCOME_ENTRY_SIDE_FAILURE")
    if payload["exit_price"]!=(exitq[1] if side=="BUY" else exitq[2]):raise ValueError("OUTCOME_EXIT_SIDE_FAILURE")
    stop_contract=payload.get("stop_contract",{});bar_refs=stop_contract.get("ancestor_bars_used",[])
    if len(bar_refs)!=ATR_PERIOD+1:
        raise ValueError("OUTCOME_STOP_LINEAGE_INCOMPLETE")
    for ref in bar_refs:
        bar=source.execute("SELECT payload_sha256,payload FROM provenance WHERE evidence_id=?",(ref["evidence_id"],)).fetchone()
        if not bar or bar[0]!=ref["payload_sha256"] or hashlib.sha256(bar[1].encode()).hexdigest()!=bar[0]:
            raise ValueError("OUTCOME_STOP_BAR_HASH_FAILURE")
        evidence=json.loads(bar[1])
        if evidence.get("timeframe")!="M15" or utc(evidence["bar_close_utc"])>utc(payload["decision_time_utc"]) or utc(evidence["acquired_at_utc"])>utc(payload["decision_time_utc"]):
            raise ValueError("OUTCOME_STOP_BAR_NOT_POINT_IN_TIME")
    stop_distance,expected_stop=m15_stop_distance(source,payload["observation_id"],utc(payload["decision_time_utc"]))
    if not math.isclose(stop_distance,float(stop_contract["stop_distance"]),rel_tol=0,abs_tol=1e-12):
        raise ValueError("OUTCOME_STOP_DISTANCE_RECOMPUTE_FAILURE")


def run_resolver(*, max_cycles_for_test: int | None = None, poll_seconds: float = 30.0) -> None:
    os.environ.update({"APP_ENV":"paper","MT5_READ_ONLY":"true","TRADING_EXECUTION_ENABLED":"false",
        "DEMO_EXECUTION_ENABLED":"false","REAL_EXECUTION_ENABLED":"false","FIT_MODEL":"0","ORDER_SEND":"0"})
    _broker_offset_seconds()
    owner=_acquire_lock(datetime.now(UTC)); started=datetime.now(UTC).isoformat(); source=labels=cache=mt5=None
    processed=resolved=pending=invalid=0;last_success=None;last_error=None;cycle=0;duration=None
    heartbeat_token=None
    heartbeat_faults=[]
    terminal_status="FAILED"
    terminal_reason=None
    terminal_error=None
    primary_error=None
    completed_for_test=False
    finalization_errors=[]
    def publish_state(status="RUNNING", error=None):
        return _state(owner,status=status,started=started,processed=processed,resolved=resolved,pending=pending,
                      invalid=invalid,last_success=last_success,last_error=None if error is None else str(error),
                      cycle_duration=duration,memory=_memory())
    def refresh_existing_state():
        assert_owner(LOCK_PATH,owner)
        state=json.loads(read_text_shared(STATE_PATH));stamp=datetime.now(UTC).isoformat()
        _write_json_atomic(LOCK_PATH,{**owner,"heartbeat_at_utc":stamp})
        _write_json_atomic(STATE_PATH,{**state,"heartbeat_at_utc":stamp})
    def recover_dependency(failure):
        def publish(phase,error,attempt,deadline):
            publish_state("RUNNING" if phase=="HEALTHY" else phase,error)
            log_event(ROOT,{"event":"RESOLVER_DEPENDENCY_STATE","lifecycle_status":phase,
                           "diagnostics":None if error is None else error.diagnostics,"attempt":attempt})
        return recertify(lambda deadline: verify_collector_health(source,deadline_monotonic=deadline),failure,publish,
                         monotonic=time.monotonic,sleep=time.sleep)
    def guarded_heartbeat():
        dependency_guard()
        publish_state()
    def dependency_guard():
        storage_health.require(OUTCOME_DB)
        for faults in heartbeat_faults:
            if faults:raise faults[0]
        health=verify_collector_health(source,certify_clock=False)
        for faults in heartbeat_faults:
            if faults:raise faults[0]
        return health
    try:
        _state(owner,status="STARTING",started=started,processed=0,resolved=0,pending=0,invalid=0,last_success=None,last_error=None,cycle_duration=None,memory=_memory())
        storage_health.require(OUTCOME_DB)
        import MetaTrader5

        from app.mt5_connection import initialize_checked
        mt5=MetaTrader5; connection=initialize_checked(mt5)
        if connection.get("server")!=SERVER: raise RuntimeError("UNEXPECTED_MT5_SERVER")
        # Collector remains sole owner of its lock/state; resolver has independent files.
        source=sqlite3.connect(f"file:{STORE}?mode=ro",uri=True,timeout=30);source.execute("PRAGMA query_only=ON");source.execute("PRAGMA cache_size=-2048")
        heartbeat_faults.append(install_trace_heartbeat(source,refresh_existing_state,monotonic=time.monotonic))
        heartbeat_token=_clock_heartbeat.set(refresh_existing_state)
        try:
            collector_health=verify_collector_health(source)
        except ClockHealthError as exc:
            collector_health=recover_dependency(exc)
        tick,acquired,normalized=_fresh_tick(mt5)
        tick_age=(acquired-normalized).total_seconds()
        if not (0<=tick_age<=5 and 0<float(tick.bid)<=float(tick.ask)):
            raise RuntimeError("V35_RESOLVER_LIVE_TICK_UNHEALTHY")
        labels=outcome_store();cache=cache_store()
        heartbeat_faults.append(install_trace_heartbeat(labels,refresh_existing_state,monotonic=time.monotonic))
        tick_source=ReadOnlyMT5Ticks(mt5,cache,labels,guarded_heartbeat)
        while True:
            try:
                storage_health.require(OUTCOME_DB)
                collector_health=verify_collector_health(source)
                cycle_clock=clock_evidence()
                if not cycle_clock.get("clock_safe"):
                    raise RuntimeError("V35_RESOLVER_CLOCK_UNCERTIFIED_DURING_RUN")
                begin=time.perf_counter(); labels.execute("PRAGMA quick_check");
                if labels.execute("PRAGMA quick_check").fetchone()[0]!="ok" or labels.execute("PRAGMA foreign_key_check").fetchall(): raise RuntimeError("OUTCOME_STORE_INTEGRITY_FAILURE")
                report=resolver_cycle(source,labels,tick_source,health_guard=dependency_guard)
            except Exception as exc:
                if storage_health.is_storage_error(exc):
                    for db in (labels, cache):
                        if db is not None: db.rollback()
                    storage_health.wait_until_healthy(OUTCOME_DB, lambda metrics: publish_state("STORAGE_UNCERTIFIED", metrics["reason"]))
                    if labels.execute("PRAGMA quick_check").fetchone()[0] != "ok" or labels.execute("PRAGMA foreign_key_check").fetchall():
                        raise RuntimeError("STORAGE_RECOVERY_DATABASE_PREFLIGHT_FAILED")
                    verify_collector_health(source)
                    last_error=None
                    continue
                if not isinstance(exc, ClockHealthError): raise
                recover_dependency(exc)
                last_error=None
                continue
            processed+=report["processed"];resolved=int(labels.execute("SELECT count(*) FROM outcomes WHERE status!='INVALID'").fetchone()[0]);pending=report["pending"];invalid=int(labels.execute("SELECT count(*) FROM outcomes WHERE status='INVALID'").fetchone()[0]);cycle+=1
            last_success=datetime.now(UTC).isoformat();duration=time.perf_counter()-begin;last_error=None
            admission=training_admission_report(source,labels)
            for faults in heartbeat_faults:
                if faults:raise faults[0]
            admission["collector_health"]={"status":collector_health["status"],"pid":collector_health["pid"],
                "heartbeat_age_seconds":collector_health["heartbeat_age_seconds"],"live_tick_age_seconds":tick_age,
                "clock_evidence":cycle_clock}
            _write_json_atomic(REPORT_PATH,admission)
            _state(owner,status="RUNNING",started=started,processed=processed,resolved=resolved,pending=pending,invalid=invalid,last_success=last_success,last_error=None,cycle_duration=duration,memory=_memory())
            if max_cycles_for_test is not None and cycle>=max_cycles_for_test:
                terminal_status="STOPPED"; terminal_reason="TEST_CYCLE_LIMIT"; completed_for_test=True
                break
            until=time.monotonic()+poll_seconds
            while time.monotonic()<until:
                if not sleep_until(until, max_sleep=5.0):
                    break
                refresh_existing_state()
    except Exception as exc:
        last_error=f"{type(exc).__name__}:{exc}"
        primary_error=exc
        terminal_error=last_error
        terminal_status="STORAGE_UNCERTIFIED" if storage_health.is_storage_error(exc) else "FAILED_PERSISTENT" if isinstance(exc,ClockHealthError) else "FAILED"
        terminal_reason="WORKER_EXCEPTION"
        raise
    except KeyboardInterrupt:
        terminal_status="INTERRUPTED"
        terminal_reason="OPERATOR_INTERRUPT"
        completed_for_test=True
    finally:
        cleanup=[]
        if heartbeat_token is not None:cleanup.append(("reset_clock_heartbeat",lambda:_clock_heartbeat.reset(heartbeat_token)))
        for db in (cache,labels,source):
            if db:cleanup.append((f"close_database_{getattr(db,'database','unknown')}",db.close))
        if mt5:
            def close_mt5():
                from app.mt5_connection import shutdown
                shutdown(mt5)
            cleanup.append(("shutdown_mt5",close_mt5))

        def publish_terminal(cleanup_errors):
            status=terminal_status
            reason=terminal_reason
            error=terminal_error
            if cleanup_errors and status=="STOPPED":
                status="FAILED"; reason="WORKER_CLEANUP_FAILED"
                error="WORKER_CLEANUP_FAILED"
            _state(owner,status=status,started=started,processed=processed,resolved=resolved,pending=pending,
                invalid=invalid,last_success=last_success,last_error=error,cycle_duration=duration,memory=_memory(),
                reason=reason,cleanup_errors=cleanup_errors,preserve_failed_temp=True)
            if cleanup_errors:
                log_event(ROOT,{"event":"RESOLVER_SHUTDOWN_CLEANUP_ERRORS","worker_id":owner["worker_id"],
                                "errors":cleanup_errors,"primary_error":terminal_error,"reason":reason})

        finalization_errors=finalize_worker(owner=owner,lock_path=LOCK_PATH,cleanup=cleanup,
            publish_terminal=publish_terminal,record_event=lambda event:log_event(ROOT,event))
    if finalization_errors and (completed_for_test or terminal_status=="INTERRUPTED") and primary_error is None:
        raise RuntimeError(f"V35_RESOLVER_FINALIZATION_FAILED:{finalization_errors}")
