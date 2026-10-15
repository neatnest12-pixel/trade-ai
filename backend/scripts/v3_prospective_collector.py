"""Isolated read-only V3 collector with real strategy-input payloads."""
from __future__ import annotations
from app.mt5_connection import initialize_checked, shutdown as shutdown_mt5
from app.core.mt5_offset import configured_offset_seconds

import hashlib
import json
import os
import sqlite3
import time
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NamedTuple

from app.v3_4.mt5_time import (
    TIME_NORMALIZATION_VERSION,
    ClockSkewError,
    CompletedCandleError,
    MarketDataStaleError,
    normalize_mt5_epoch,
    require_completed_candle,
    require_not_future,
    require_recent,
)

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "runtime"
DATA = ROOT / "certified_data" / "v3"
LOCK = RUNTIME / "v3_prospective_collector.lock"
STATE = RUNTIME / "v3_prospective_collector_state.json"
OBS = DATA / "V3_PROSPECTIVE_DATASET.jsonl"
OUTCOMES = DATA / "V3_AUTOMATIC_OUTCOMES_v3_outcome_r1.jsonl"
DIAG = DATA / "V3_STRATEGY_INPUT_DIAGNOSTIC.jsonl"
MANIFEST = DATA / "V3_3_MIGRATION_MANIFEST.json"
INDEX = RUNTIME / "v3_prospective_observations.sqlite3"

FEATURE_POLICY_VERSION = "v3-mtf-context-r1"
FEATURE_POLICY_HASH = hashlib.sha256(FEATURE_POLICY_VERSION.encode()).hexdigest()
ACTIVE_COLLECTOR_POLICY_VERSION = "v3.4-complete-fx-intelligence-r1"
ACTIVE_COLLECTOR_POLICY_HASH = "c056fac9dca4cf331dc94c52ea3e46dc7e6257e9fd0009506fbf808ce795ee58"
MIGRATION_BOUNDARY = datetime.fromisoformat("2026-08-19T15:09:06+00:00")
TIMEFRAME_NAMES = ("M1", "M5", "M15", "H1", "H6", "H12")


class CollectorTick(NamedTuple):
    timestamp: datetime
    raw_timestamp: datetime
    raw_epoch_seconds: float
    raw_time_msc: int | None
    bid: float
    ask: float
    source: str


def now() -> datetime:
    return datetime.now(UTC)


def write_state(state: dict[str, Any]) -> None:
    from app.v3.worker_audit import event
    if state.get('status')=='RUNNING' and not state.get('last_error'):
        state['last_successful_cycle']=state.get('last_heartbeat')
    temporary = STATE.with_suffix(STATE.suffix + ".tmp")
    with temporary.open("w", encoding="utf8", newline="\n") as handle:
        handle.write(json.dumps(state, indent=2))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, STATE)
    event('collector','heartbeat',**{k:v for k,v in state.items() if k in ['status','worker_id','observations_count','certified_resolved','pending','last_completed_m1','last_successful_cycle']})


def write_manifest(collector_id: str, migration_timestamp: datetime) -> None:
    with _connect_index() as connection:
        summary = {'count': connection.execute('SELECT count(*) FROM observations').fetchone()[0]}
    MANIFEST.write_text(
        json.dumps(
            {
                "collector_id": collector_id,
                "migration_timestamp": migration_timestamp.isoformat(),
                "active_policy_version": ACTIVE_COLLECTOR_POLICY_VERSION,
                "active_policy_hash": ACTIVE_COLLECTOR_POLICY_HASH,
                "base_feature_policy_version": FEATURE_POLICY_VERSION,
                "base_feature_policy_hash": FEATURE_POLICY_HASH,
                "preserved_observations_before_activation": summary["count"],
                "preservation_rule": "append-only; no historical rewrite/backfill",
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf8",
    )


def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    """Stream JSONL with bounded memory and fail closed on every malformed row."""
    if not path.exists():
        return
    size = path.stat().st_size
    with path.open("rb") as handle:
        line_number = 0
        while True:
            raw = handle.readline()
            if not raw:
                break
            line_number += 1
            if not raw.endswith(b"\n") and handle.tell() == size:
                raise RuntimeError(f"PARTIAL_FINAL_JSONL_LINE:{path}:{line_number}")
            if not raw.strip():
                raise RuntimeError(f"BLANK_JSONL_LINE:{path}:{line_number}")
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise RuntimeError(f"MALFORMED_JSONL:{path}:{line_number}:{type(error).__name__}") from error
            if not isinstance(payload, dict):
                raise RuntimeError(f"NON_OBJECT_JSONL:{path}:{line_number}")
            yield payload


def _connect_index() -> sqlite3.Connection:
    connection = sqlite3.connect(INDEX)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS observations ("
        "observation_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, timestamp TEXT NOT NULL, "
        "normalized INTEGER NOT NULL, v34 INTEGER NOT NULL, UNIQUE(symbol, timestamp, normalized))"
    )
    connection.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    return connection


def scan_observations(*, rebuild_index: bool = True, heartbeat=None) -> dict[str, Any]:
    """Reconcile immutable JSONL in one streaming pass; row objects never accumulate."""
    summary: dict[str, Any] = {
        "count": 0, "family_counts": {name: 0 for name in ("trend", "breakout", "pullback", "mean_reversion", "volatility")},
        "last_row": None,
        "eligible_total": 0, "uncertifiable": 0,
    }
    connection = _connect_index() if rebuild_index else None
    if connection is not None:
        connection.execute("DELETE FROM observations")
    try:
        for row in _iter_jsonl(OBS):
            summary["count"] += 1
            from app.v3.certification import project_setup
            projected = project_setup(row)
            if projected:
                summary['eligible_total'] += 1
                summary['uncertifiable'] += projected['timing_status'] != 'CERTIFIED'
            if heartbeat is not None and summary['count'] % 100 == 0:
                heartbeat(summary['count'])
            for family in summary["family_counts"]:
                summary["family_counts"][family] += bool(row.get("setup_states", {}).get(family.upper()))
            current = summary["last_row"]
            if current is None or row.get("timestamp", "") > current.get("timestamp", ""):
                summary["last_row"] = row
            if connection is not None:
                observation_id = row.get("observation_id")
                timestamp = row.get("timestamp")
                if not observation_id or not timestamp:
                    raise RuntimeError("OBSERVATION_IDENTITY_MISSING")
                normalized = int(row.get("time_normalization_version") == TIME_NORMALIZATION_VERSION)
                v34 = int(row.get("collector_policy_version") == ACTIVE_COLLECTOR_POLICY_VERSION and row.get("collector_policy_hash") == ACTIVE_COLLECTOR_POLICY_HASH)
                try:
                    connection.execute("INSERT INTO observations VALUES (?, ?, ?, ?, ?)", (observation_id, row.get("symbol", "EURUSD"), timestamp, normalized, v34))
                except sqlite3.IntegrityError as error:
                    raise RuntimeError(f"DUPLICATE_OBSERVATION:{observation_id}") from error
        if connection is not None:
            connection.execute("INSERT OR REPLACE INTO metadata VALUES ('source_size', ?)", (str(OBS.stat().st_size if OBS.exists() else 0),))
            connection.commit()
    finally:
        if connection is not None:
            connection.close()
    return summary


def existing_observation_identities(rows: Iterable[dict[str, Any]]) -> set[tuple[str, str | None]]:
    return {(row.get("symbol", "EURUSD"), row.get("timestamp")) for row in rows}


def _index_has_identity(connection: sqlite3.Connection, symbol: str, timestamp: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM observations WHERE symbol=? AND timestamp=? AND normalized=1",
        (symbol, timestamp),
    ).fetchone() is not None


def _index_has_id(connection: sqlite3.Connection, observation_id: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM observations WHERE observation_id=?", (observation_id,)
    ).fetchone() is not None


def _durable_append(row: dict[str, Any], connection: sqlite3.Connection) -> None:
    """Append one complete row durably, then publish its exact identity to the index."""
    encoded = (json.dumps(row, sort_keys=True) + "\n").encode("utf-8")
    with OBS.open("ab", buffering=0) as handle:
        handle.write(encoded)
        os.fsync(handle.fileno())
    connection.execute(
        "INSERT INTO observations VALUES (?, ?, ?, ?, ?)",
        (
            row["observation_id"], row.get("symbol", "EURUSD"), row["timestamp"],
            int(row.get("time_normalization_version") == TIME_NORMALIZATION_VERSION),
            int(row.get("collector_policy_version") == ACTIVE_COLLECTOR_POLICY_VERSION and row.get("collector_policy_hash") == ACTIVE_COLLECTOR_POLICY_HASH),
        ),
    )
    connection.execute("INSERT OR REPLACE INTO metadata VALUES ('source_size', ?)", (str(OBS.stat().st_size),))
    connection.commit()


def _pid_is_live(pid: int) -> bool:
    from app.v3.worker_lifecycle import pid_alive
    return pid_alive(pid)


def acquire_lock(collector_id: str) -> None:
    payload = json.dumps({"collector_id": collector_id, "worker_id": collector_id,
                          "pid": os.getpid(), "acquired_at": now().isoformat()})
    try:
        descriptor = os.open(LOCK, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError:
        raise RuntimeError("COLLECTOR_LOCK_REQUIRES_OWNERSHIP_AUDIT") from None
    with os.fdopen(descriptor, "w", encoding="utf8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def release_lock(collector_id: str) -> None:
    try:
        owner = json.loads(LOCK.read_text(encoding="utf8"))
        if owner.get("collector_id") == collector_id and int(owner.get("pid", -1)) == os.getpid():
            LOCK.unlink()
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
        return


def reconcile(state: dict[str, Any], heartbeat=None) -> dict[str, Any]:
    summary = scan_observations(rebuild_index=True, heartbeat=heartbeat)
    state["observations_count"] = summary["count"]
    for family in ("trend", "breakout", "pullback", "mean_reversion", "volatility"):
        state[f"{family}_setups"] = summary["family_counts"][family]
    all_resolved_ids: set[str] = set()
    v34_resolved_ids: set[str] = set()
    persisted_pending_ids: set[str] = set()
    with _connect_index() as connection:
        for row in _iter_jsonl(OUTCOMES):
            observation_id = row.get("observation_id")
            if not observation_id:
                continue
            is_v34 = connection.execute("SELECT 1 FROM observations WHERE observation_id=? AND v34=1", (observation_id,)).fetchone() is not None
            if row.get("status") == "RESOLVED":
                all_resolved_ids.add(observation_id)
                if is_v34: v34_resolved_ids.add(observation_id)
            elif row.get("status") in {"PENDING", "UNRESOLVED", "IMMATURE"} and is_v34:
                persisted_pending_ids.add(observation_id)
    state["resolved_outcomes"] = len(v34_resolved_ids)
    state["unresolved_outcomes"] = len(persisted_pending_ids) # replaced below by eligible-minus-certified
    state["resolved_outcomes_persisted"] = len(v34_resolved_ids)
    state["pending_outcomes_persisted"] = len(persisted_pending_ids)
    state["all_v3_resolved_outcomes"] = len(all_resolved_ids)
    state["v3_4_resolved_outcomes"] = len(v34_resolved_ids)
    state["outcome_counter_source"] = str(OUTCOMES)
    state["outcome_counter_scope"] = "v3.4 prospective observations by active policy version/hash"
    from app.v3.certification import project_setup
    from app.v3.certified_store import counts as certified_counts
    certification_db = RUNTIME / "v34_certified_outcomes.sqlite3"
    eligible_count, uncertifiable = summary['eligible_total'], summary['uncertifiable']
    certified = 0
    failed = timeout = 0
    if certification_db.exists():
        with sqlite3.connect(f"file:{certification_db.as_posix()}?mode=ro", uri=True) as ledger:
            measured = certified_counts(ledger)
            certified = measured["certified_resolved"]
            failed, timeout = measured["failed_resolution"], measured["timeout"]
    state.update(eligible_total=eligible_count, certified_resolved=certified,
                 pending=eligible_count-certified, uncertifiable=uncertifiable,
                 failed_resolution=failed, timeout=timeout,
                 resolved_outcomes=certified, unresolved_outcomes=eligible_count-certified,
                 legacy_resolved_outcomes=len(v34_resolved_ids),
                 outcome_counter_scope="eligible V3.4 minus certified 1R; uncertifiable is a pending subset")
    state.update(resolved_outcomes_persisted=certified, v3_4_resolved_outcomes=certified,
                 pending_outcomes_persisted=eligible_count-certified,
                 outcome_counter_source=str(certification_db))
    if summary["last_row"]:
        row = summary["last_row"]
        state["last_completed_m1"] = row["timestamp"]
        state["last_observation_id"] = row["observation_id"]
    return state


def refresh_certified_counts(state: dict[str, Any]) -> None:
    """Refresh resolver progress without scanning observation JSONL each cycle."""
    from app.v3.certified_store import counts
    path = RUNTIME / "v34_certified_outcomes.sqlite3"
    if path.exists():
        with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as db:
            measured = counts(db)
        state.update(certified_resolved=measured["certified_resolved"],
                     resolved_outcomes=measured["certified_resolved"],
                     failed_resolution=measured["failed_resolution"], timeout=measured["timeout"])
    state["pending"] = state.get("eligible_total", 0) - state.get("certified_resolved", 0)
    state["unresolved_outcomes"] = state["pending"]
    state.update(resolved_outcomes_persisted=state.get('certified_resolved',0),
                 v3_4_resolved_outcomes=state.get('certified_resolved',0),
                 pending_outcomes_persisted=state['pending'])


def _rate_time(rate: Any) -> int:
    return int(rate["time"])


def _rate_float(rate: Any, key: str) -> float:
    return float(rate[key])


def _rates_to_candles(
    rates: Iterable[Any], timeframe: str, offset_seconds: int
) -> list[Any]:
    from app.v3.strategy_inputs import StrategyCandle

    return [
        StrategyCandle(
            timeframe=timeframe,
            timestamp=normalize_mt5_epoch(
                _rate_time(rate),
                offset_seconds=offset_seconds,
                source=f"EURUSD_{timeframe}_BAR",
            ).timestamp,
            open=_rate_float(rate, "open"),
            high=_rate_float(rate, "high"),
            low=_rate_float(rate, "low"),
            close=_rate_float(rate, "close"),
            provenance=f"DIRECT_MT5:{TIME_NORMALIZATION_VERSION}",
        )
        for rate in rates
    ]


def _family_payload(result: Any, input_version: str, input_hash: str) -> dict[str, Any]:
    payload = dict(result.__dict__)
    payload["reason_codes"] = list(payload.get("reason_codes", ()))
    payload["conditions_passed"] = list(payload.get("conditions_passed", ()))
    payload["conditions_failed"] = list(payload.get("conditions_failed", ()))
    payload["strategy_input_policy_version"] = input_version
    payload["strategy_input_policy_hash"] = input_hash
    return payload


def _to_v32_candles(candles_by_tf: dict[str, list[Any]]) -> dict[str, list[Any]]:
    from app.v3_2.confluence import Candle

    return {
        timeframe: [
            Candle(
                timestamp=candle.timestamp,
                open=candle.open,
                high=candle.high,
                low=candle.low,
                close=candle.close,
                tick_volume=0,
                spread=None,
                real_volume=0,
            )
            for candle in candles
        ]
        for timeframe, candles in candles_by_tf.items()
    }


def _tick_history_payload(tick: CollectorTick, decision_ts: datetime) -> list[Any]:
    from app.v3_3.system_intelligence import Tick

    if tick.timestamp > decision_ts:
        raise CompletedCandleError("FUTURE_DECISION_TICK")
    return [
        Tick(
            timestamp=tick.timestamp,
            bid=tick.bid,
            ask=tick.ask,
            provenance=tick.source,
        )
    ]


def _provider_snapshot() -> dict[str, Any]:
    from app.v3_4.macro_feature_tiering import readiness_summary
    from app.v3_4.providers.adapters.bea import BeaApiAdapter
    from app.v3_4.providers.adapters.bls import BlsApiAdapter
    from app.v3_4.providers.adapters.bundesbank import BundesbankYieldAdapter
    from app.v3_4.providers.adapters.ecb import EcbApiAdapter
    from app.v3_4.providers.adapters.fred import FredApiAdapter

    def probe(call: Any) -> dict[str, Any]:
        try:
            record = call()
            quality = (
                record.get("quality") or record.get("data_quality")
                if isinstance(record, dict)
                else None
            )
            return {
                "state": "PASS" if quality and quality != "UNAVAILABLE" else "FAIL",
                "record": record if isinstance(record, dict) else None,
            }
        except Exception as error:
            return {"state": "FAIL", "error": type(error).__name__}

    fred = FredApiAdapter()
    bls = BlsApiAdapter()
    bea = BeaApiAdapter()
    ecb = EcbApiAdapter()
    bundesbank = BundesbankYieldAdapter()
    return {
        "macro_tiering": readiness_summary(),
        "FRED": probe(lambda: fred.normalized_rate("US_10Y")),
        "BLS": probe(lambda: bls.fetch_latest("CPI")),
        "BEA": probe(lambda: bea.fetch_latest("GDP")),
        "ECB": probe(lambda: ecb.fetch_latest("ECB_RATE")),
        "RATES_YIELDS": {
            "US_2Y": probe(lambda: fred.normalized_rate("US_2Y")),
            "US_5Y": probe(lambda: fred.normalized_rate("US_5Y")),
            "US_10Y": probe(lambda: fred.normalized_rate("US_10Y")),
            "DE_2Y": probe(lambda: bundesbank.normalized_yield("DE_2Y")),
            "DE_5Y": probe(lambda: bundesbank.normalized_yield("DE_5Y")),
            "DE_10Y": probe(lambda: fred.normalized_rate("DE_10Y")),
        },
        "provider_provenance": {
            "required_fields": [
                "provider",
                "source_timestamp",
                "retrieved_at",
                "quality",
                "staleness",
                "availability_tier",
            ],
            "fail_closed": True,
        },
    }


def make_row(
    m1_rate: Any,
    tick: CollectorTick,
    candles_by_tf: dict[str, list[Any]],
    offset_seconds: int,
) -> dict[str, Any]:
    from app.v3.strategy_families import STRATEGY_POLICY_HASH, STRATEGY_POLICY_VERSION, evaluate_all
    from app.v3.strategy_inputs import (
        STRATEGY_INPUT_POLICY_HASH,
        STRATEGY_INPUT_POLICY_VERSION,
        build_strategy_input,
    )
    from app.v3_1.strategy_library import POLICY_HASH as V31_POLICY_HASH
    from app.v3_1.strategy_library import POLICY_VERSION as V31_POLICY_VERSION
    from app.v3_1.strategy_library import build_research_snapshot
    from app.v3_2.confluence import POLICY_HASH as V32_POLICY_HASH
    from app.v3_2.confluence import POLICY_VERSION as V32_POLICY_VERSION
    from app.v3_2.confluence import TickQuote, build_confluence_vector
    from app.v3_3.system_intelligence import POLICY_HASH as V33_POLICY_HASH
    from app.v3_3.system_intelligence import POLICY_VERSION as V33_POLICY_VERSION
    from app.v3_3.system_intelligence import build_system_snapshot
    from app.v3_4.fx_intelligence import POLICY_HASH as V34_POLICY_HASH
    from app.v3_4.fx_intelligence import POLICY_VERSION as V34_POLICY_VERSION
    from app.v3_4.fx_intelligence import build_fx_intelligence_snapshot

    normalized_m1 = normalize_mt5_epoch(
        _rate_time(m1_rate),
        offset_seconds=offset_seconds,
        source="EURUSD_M1_BAR",
    )
    ts = normalized_m1.timestamp
    decision_ts = ts + timedelta(minutes=1)
    oid = hashlib.sha256(
        (
            f"EURUSD|{ts.isoformat()}|{normalized_m1.raw_epoch_seconds}|"
            f"{TIME_NORMALIZATION_VERSION}|{ACTIVE_COLLECTOR_POLICY_HASH}"
        ).encode()
    ).hexdigest()
    candles = [candle for tf in TIMEFRAME_NAMES for candle in candles_by_tf.get(tf, [])]
    strategy_input = build_strategy_input(candles, ts)
    results = evaluate_all(strategy_input)
    families = {
        family: _family_payload(result, STRATEGY_INPUT_POLICY_VERSION, STRATEGY_INPUT_POLICY_HASH)
        for family, result in results.items()
    }
    v32_candles_by_tf = _to_v32_candles(candles_by_tf)
    tick_quote = TickQuote(
        timestamp=tick.timestamp,
        bid=tick.bid,
        ask=tick.ask,
        provenance=tick.source,
    )
    v31_snapshot = build_research_snapshot(v32_candles_by_tf, decision_ts, tick_quote)
    v32_vector = build_confluence_vector(v32_candles_by_tf, decision_ts, tick_quote)
    v33_snapshot = build_system_snapshot(
        v32_candles_by_tf,
        _tick_history_payload(tick, decision_ts),
        decision_ts,
        strategy_signals=v31_snapshot["strategy_signals"],
    )
    v34_snapshot = build_fx_intelligence_snapshot(
        [candle for rows in v32_candles_by_tf.values() for candle in rows],
        _tick_history_payload(tick, decision_ts),
        decision_ts,
    )
    v34_snapshot["providers"] = _provider_snapshot()
    v34_snapshot["commission_status"] = "UNAVAILABLE"
    v34_snapshot["swap_status"] = "UNAVAILABLE"
    v34_snapshot["cost_provenance_status"] = "PRE_COMMISSION_QUOTE_BASED"
    return {
        "observation_id": oid,
        "timestamp": ts.isoformat(),
        "decision_timestamp": decision_ts.isoformat(),
        "completed_m1_timestamp": ts.isoformat(),
        "raw_m1_epoch_seconds": normalized_m1.raw_epoch_seconds,
        "raw_m1_timestamp": normalized_m1.raw_timestamp.isoformat(),
        "time_normalization_version": TIME_NORMALIZATION_VERSION,
        "mt5_server_utc_offset_seconds": offset_seconds,
        "symbol": "EURUSD",
        "collector_policy_version": ACTIVE_COLLECTOR_POLICY_VERSION,
        "collector_policy_hash": ACTIVE_COLLECTOR_POLICY_HASH,
        "v3_1_policy_version": V31_POLICY_VERSION,
        "v3_1_policy_hash": V31_POLICY_HASH,
        "v3_2_policy_version": V32_POLICY_VERSION,
        "v3_2_policy_hash": V32_POLICY_HASH,
        "v3_3_policy_version": V33_POLICY_VERSION,
        "v3_3_policy_hash": V33_POLICY_HASH,
        "v3_4_policy_version": V34_POLICY_VERSION,
        "v3_4_policy_hash": V34_POLICY_HASH,
        "bid": tick.bid,
        "ask": tick.ask,
        "spread": tick.ask - tick.bid,
        "spread_pips": (tick.ask - tick.bid) * 100000,
        "tick_timestamp": tick.timestamp.isoformat(),
        "raw_tick_timestamp": tick.raw_timestamp.isoformat(),
        "raw_tick_epoch_seconds": tick.raw_epoch_seconds,
        "raw_tick_time_msc": tick.raw_time_msc,
        "tick_timestamp_source": tick.source,
        "tick_age_ms": max(0, (decision_ts - tick.timestamp).total_seconds() * 1000),
        "policy_hash": FEATURE_POLICY_HASH,
        "feature_policy_version": FEATURE_POLICY_VERSION,
        "feature_policy_hash": FEATURE_POLICY_HASH,
        "strategy_input_policy_version": STRATEGY_INPUT_POLICY_VERSION,
        "strategy_input_policy_hash": STRATEGY_INPUT_POLICY_HASH,
        "strategy_input_payload": strategy_input,
        "strategy_policy_version": STRATEGY_POLICY_VERSION,
        "strategy_policy_hash": STRATEGY_POLICY_HASH,
        "strategy_families": families,
        "feature_provenance": strategy_input["feature_provenance"],
        "completed_context": {
            tf: {
                "available": bool(strategy_input["contexts"][tf].get("available")),
                "completion_at_or_before": strategy_input["contexts"][tf].get(
                    "source_candle_timestamp"
                ),
            }
            for tf in TIMEFRAME_NAMES
        },
        "origin": "PROSPECTIVE_OBSERVED",
        "setup_states": {family: result.setup for family, result in results.items()},
        "v3_1_strategy_research": v31_snapshot,
        "v3_2_confluence": v32_vector,
        "v3_3_system_intelligence": v33_snapshot,
        "v3_4_forex_intelligence": v34_snapshot,
    }


def _fetch_rates(mt5: Any, offset_seconds: int) -> dict[str, list[Any]]:
    mapping = {
        "M1": mt5.TIMEFRAME_M1,
        "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "H1": mt5.TIMEFRAME_H1,
        "H6": mt5.TIMEFRAME_H6,
        "H12": mt5.TIMEFRAME_H12,
    }
    fetched: dict[str, list[Any]] = {}
    for timeframe, constant in mapping.items():
        rates = mt5.copy_rates_from_pos("EURUSD", constant, 1, 2000)
        fetched[timeframe] = _rates_to_candles(
            [] if rates is None else rates, timeframe, offset_seconds
        )
    return fetched


def _fetch_m1_rates(mt5: Any) -> list[Any]:
    rates = mt5.copy_rates_from_pos("EURUSD", mt5.TIMEFRAME_M1, 1, 2000)
    return [] if rates is None else list(rates)


def _normalized_tick(raw: Any, offset_seconds: int, source: str) -> CollectorTick:
    raw_time_msc = getattr(raw, "time_msc", None)
    epoch = float(raw_time_msc) / 1000 if raw_time_msc else float(raw.time)
    normalized = normalize_mt5_epoch(
        epoch,
        offset_seconds=offset_seconds,
        source=source,
    )
    return CollectorTick(
        timestamp=normalized.timestamp,
        raw_timestamp=normalized.raw_timestamp,
        raw_epoch_seconds=normalized.raw_epoch_seconds,
        raw_time_msc=None if raw_time_msc is None else int(raw_time_msc),
        bid=float(raw.bid),
        ask=float(raw.ask),
        source=source,
    )


def _fetch_decision_tick(
    mt5: Any, decision_timestamp: datetime, offset_seconds: int
) -> CollectorTick | None:
    raw_start = decision_timestamp + timedelta(seconds=offset_seconds - 120)
    raw_end = decision_timestamp + timedelta(seconds=offset_seconds)
    rows = mt5.copy_ticks_range("EURUSD", raw_start, raw_end, mt5.COPY_TICKS_ALL)
    if rows is None:
        return None
    candidates: list[CollectorTick] = []
    for raw in rows:
        bid = float(raw["bid"])
        ask = float(raw["ask"])
        if bid <= 0 or ask <= 0 or ask < bid:
            continue
        raw_time_msc = int(raw["time_msc"]) if "time_msc" in raw.dtype.names else None
        epoch = float(raw_time_msc) / 1000 if raw_time_msc else float(raw["time"])
        normalized = normalize_mt5_epoch(
            epoch,
            offset_seconds=offset_seconds,
            source="MT5_COPY_TICKS_RANGE_AT_DECISION",
        )
        if normalized.timestamp <= decision_timestamp:
            candidates.append(
                CollectorTick(
                    timestamp=normalized.timestamp,
                    raw_timestamp=normalized.raw_timestamp,
                    raw_epoch_seconds=normalized.raw_epoch_seconds,
                    raw_time_msc=raw_time_msc,
                    bid=bid,
                    ask=ask,
                    source="MT5_COPY_TICKS_RANGE_AT_DECISION",
                )
            )
    return max(candidates, key=lambda item: item.timestamp, default=None)


def validate_observation_time_safety(
    row: dict[str, Any], *, trusted_utc: datetime
) -> None:
    decision_timestamp = datetime.fromisoformat(row["decision_timestamp"])
    completed_m1 = datetime.fromisoformat(row["completed_m1_timestamp"])
    require_not_future(
        decision_timestamp,
        system_utc=trusted_utc,
        source="EURUSD_M1_COMPLETION",
    )
    require_completed_candle(
        completed_m1,
        timeframe="M1",
        decision_timestamp=decision_timestamp,
    )
    tick_timestamp = datetime.fromisoformat(row["tick_timestamp"])
    if tick_timestamp > decision_timestamp:
        raise CompletedCandleError("FUTURE_DECISION_TICK")
    for timeframe, context in (row.get("completed_context") or {}).items():
        if not context.get("available"):
            continue
        source_timestamp = context.get("completion_at_or_before")
        if not source_timestamp:
            raise CompletedCandleError(f"MISSING_CONTEXT_TIMESTAMP:{timeframe}")
        require_completed_candle(
            datetime.fromisoformat(source_timestamp),
            timeframe=timeframe,
            decision_timestamp=decision_timestamp,
        )


def _state_template(collector_id: str) -> dict[str, Any]:
    return {
        "status": "PREFLIGHT_FAILED",
        "collector_id": collector_id,
        "worker_id": collector_id,
        "pid": os.getpid(),
        "startup_timestamp": now().isoformat(),
        "last_heartbeat": None,
        "last_completed_m1": None,
        "last_observation_id": None,
        "observations_count": 0,
        "trend_setups": 0,
        "breakout_setups": 0,
        "pullback_setups": 0,
        "mean_reversion_setups": 0,
        "volatility_setups": 0,
        "resolved_outcomes": 0,
        "unresolved_outcomes": 0,
        "active_policy_version": ACTIVE_COLLECTOR_POLICY_VERSION,
        "active_policy_hash": ACTIVE_COLLECTOR_POLICY_HASH,
        "last_cycle_status": "INIT",
        "last_error": None,
    }


def _safety_flags_pass() -> bool:
    from app.v3.certification import require_safety
    try:
        require_safety()
        return True
    except RuntimeError:
        return False


def main() -> int:
    from app.v3.worker_audit import event,exception
    event('collector','startup')
    DATA.mkdir(parents=True, exist_ok=True)
    RUNTIME.mkdir(exist_ok=True)
    collector_id = str(uuid.uuid4())
    state = _state_template(collector_id)
    if not _safety_flags_pass():
        state["last_error"] = "safety flags failed"
        write_state(state)
        return 2
    try:
        offset_seconds = configured_offset_seconds()
    except RuntimeError as error:
        state["last_error"] = str(error)
        write_state(state)
        return 2
    try:
        acquire_lock(collector_id)
        event('collector','lock_acquired',worker_id=collector_id,lock_owned=True)
    except RuntimeError as error:
        state["last_error"] = str(error)
        write_state(state)
        return 3
    try:
        def startup_heartbeat(scanned=0):
            state.update(status='STARTING', last_heartbeat=now().isoformat(),
                         startup_rows_scanned=scanned, last_cycle_status='RECONCILING_PERSISTED_DATA',
                         fit_model=0, order_send=0)
            write_state(state)
        startup_heartbeat()
        from app.v3.prospective import MANIFEST as CAMPAIGN_MANIFEST, campaign_manifest, verify_policy_files, sync_members, load_contract
        if CAMPAIGN_MANIFEST.exists():
            verify_policy_files(campaign_manifest())
            load_contract()
            sync_members()
        state = reconcile(state, heartbeat=startup_heartbeat)
        write_manifest(collector_id, now())
        import MetaTrader5 as mt5

        while True:
            event('collector','cycle_start',worker_id=collector_id)
            cycle_passed = False
            try:
                if not initialize_checked(mt5):
                    raise RuntimeError("MT5 initialize failed")
                info = mt5.account_info()
                symbol = mt5.symbol_info("EURUSD")
                if not info or info.server != "ICMarketsSC-Demo" or info.currency != "USD" or info.trade_mode != 0 or not symbol:
                    raise RuntimeError("preflight failed")
                raw_tick = mt5.symbol_info_tick("EURUSD")
                m1_rates = _fetch_m1_rates(mt5)
                candles_by_tf = _fetch_rates(mt5, offset_seconds)
                if not m1_rates or not raw_tick:
                    state.update(status="DATA_UNAVAILABLE", last_error="no M1 data", last_heartbeat=now().isoformat())
                    write_state(state)
                else:
                    current_tick = _normalized_tick(
                        raw_tick, offset_seconds, "EURUSD_SYMBOL_INFO_TICK"
                    )
                    require_not_future(
                        current_tick.timestamp,
                        system_utc=now(),
                        source=current_tick.source,
                    )
                    require_recent(
                        current_tick.timestamp,
                        system_utc=now(),
                        source=current_tick.source,
                    )
                    added = 0

                    def normalized_rate_time(rate: Any) -> datetime:
                        return normalize_mt5_epoch(
                            _rate_time(rate),
                            offset_seconds=offset_seconds,
                            source="EURUSD_M1_BAR",
                        ).timestamp

                    eligible = [
                        rate
                        for rate in sorted(m1_rates, key=_rate_time)
                        if normalized_rate_time(rate) >= MIGRATION_BOUNDARY
                    ]
                    with _connect_index() as index:
                        latest_row = index.execute("SELECT timestamp FROM observations WHERE symbol='EURUSD' AND normalized=1 ORDER BY timestamp DESC LIMIT 1").fetchone()
                        latest_existing_timestamp = datetime.fromisoformat(latest_row[0]) if latest_row else MIGRATION_BOUNDARY
                        missing_eligible = [rate for rate in eligible if not _index_has_identity(index, "EURUSD", normalized_rate_time(rate).isoformat())]
                        if os.getenv("V3_COLLECTOR_CATCH_UP", "false").lower() != "true":
                            missing_eligible = [rate for rate in missing_eligible if normalized_rate_time(rate) > latest_existing_timestamp][-1:]
                        if os.getenv("V3_COLLECTOR_ONCE", "false").lower() == "true":
                            missing_eligible = missing_eligible[:1]
                        for rate in missing_eligible:
                            decision_tick = _fetch_decision_tick(mt5, normalized_rate_time(rate) + timedelta(minutes=1), offset_seconds)
                            if decision_tick is None:
                                raise RuntimeError("DECISION_TICK_UNAVAILABLE")
                            row = make_row(rate, decision_tick, candles_by_tf, offset_seconds)
                            row['collection_ingestion_timestamp'] = now().isoformat()
                            validate_observation_time_safety(row, trusted_utc=now())
                            if not _index_has_id(index, row["observation_id"]):
                                _durable_append(row, index)
                                state["observations_count"] += 1
                                from app.v3.certification import project_setup
                                projected = project_setup(row)
                                if projected:
                                    state["eligible_total"] += 1
                                    state["pending"] += 1
                                    state["unresolved_outcomes"] = state["pending"]
                                    state["uncertifiable"] += projected["timing_status"] != "CERTIFIED"
                                for family in ("trend", "breakout", "pullback", "mean_reversion", "volatility"):
                                    state[f"{family}_setups"] += bool(row.get("setup_states", {}).get(family.upper()))
                                state["last_completed_m1"] = row["timestamp"]
                                state["last_observation_id"] = row["observation_id"]
                                added += 1
                    refresh_certified_counts(state)
                    sync_members()
                    state.update(
                        status="RUNNING",
                        last_heartbeat=now().isoformat(),
                        last_cycle_status=(
                            "OBSERVATIONS_WRITTEN"
                            if added
                            else "NO_NEW_COMPLETED_M1"
                        ),
                        last_error=None,
                        time_normalization_version=TIME_NORMALIZATION_VERSION,
                        mt5_server_utc_offset_seconds=offset_seconds,
                        clock_skew=None,
                    )
                    write_state(state)
                    cycle_passed = True
            except ClockSkewError as error:
                state.update(
                    status="CLOCK_SKEW_DETECTED",
                    last_heartbeat=now().isoformat(),
                    last_cycle_status="CLOCK_SKEW_DETECTED",
                    last_error="CLOCK_SKEW_DETECTED",
                    clock_skew=error.as_dict(),
                )
                write_state(state)
            except CompletedCandleError as error:
                state.update(
                    status="FUTURE_CONTEXT_REJECTED",
                    last_heartbeat=now().isoformat(),
                    last_cycle_status="FUTURE_CONTEXT_REJECTED",
                    last_error=str(error),
                )
                write_state(state)
            except MarketDataStaleError as error:
                if not state.get("observations_count"):
                    state = reconcile(state)
                state.update(
                    status="MARKET_DATA_STALE",
                    last_heartbeat=now().isoformat(),
                    last_cycle_status="MARKET_DATA_STALE",
                    last_error=str(error),
                )
                write_state(state)
            except Exception as error:
                exception('collector',error)
                state.update(
                    status="DATA_UNAVAILABLE",
                    last_heartbeat=now().isoformat(),
                    last_cycle_status="DATA_UNAVAILABLE",
                    last_error=str(error),
                )
                write_state(state)
            event('collector','cycle_finish',status=state.get('status'))
            if os.getenv("V3_COLLECTOR_ONCE", "false").lower() == "true":
                if cycle_passed:
                    state.update(status="STOPPED", last_cycle_status="ONCE_COMPLETE")
                    write_state(state)
                else:
                    return 1
                break
            time.sleep(30)
    except BaseException as error:
        exception('collector',error)
        state.update(
            status="FAILED",
            last_heartbeat=now().isoformat(),
            last_cycle_status="FATAL_ERROR",
            last_error=f"{type(error).__name__}:{error}",
        )
        write_state(state)
        raise
    finally:
        try:
            shutdown_mt5(mt5)
        except Exception:
            pass
        if state.get("status") == "RUNNING":
            state.update(status="STOPPED", last_heartbeat=now().isoformat())
            write_state(state)
        release_lock(collector_id)
        state.update(exit_reason=state.get('last_cycle_status','NORMAL_RETURN'),exit_code=0 if state.get('status')=='STOPPED' else 1)
        if state.get('status')!='STOPPED':state['status']='FAILED'
        write_state(state)
        event('collector','lock_released',worker_id=collector_id,lock_owned=True)
        event('collector','exit',exit_reason=state['exit_reason'],exit_code=state['exit_code'])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
