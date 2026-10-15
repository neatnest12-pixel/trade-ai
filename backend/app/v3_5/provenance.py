"""Point-in-time evidence contracts and append-only V3.5 cohort storage.

V3.4 remains an immutable diagnostic source. This package intentionally does
not collect market data or call a strategy; callers must supply contemporaneous
raw evidence before freezing a decision snapshot.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
from app.v3_5.storage_health import connect as storage_connect
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from app.v3_4.mt5_time import TIMEFRAME_SECONDS, normalize_mt5_epoch

COHORT_VERSION = "v3.5-provenance-certified-r1"
SCHEMA_VERSION = "v3.5-provenance-schema-r1"
COLLECTOR_VERSION = "v3.5-provenance-collector-r1"
POLICY_VERSION = "v3.5-point-in-time-r1"
POLICY = {
    "version": POLICY_VERSION,
    "broker_offset_seconds": 10800,
    "tick_max_age_seconds": 5,
    "clock_skew_guard_seconds": 5,
    "timeframes": TIMEFRAME_SECONDS,
    "completed_bars_only": True,
    "macro_date_only_safe": False,
    "training_allowed_in_bootstrap": False,
}
POLICY_HASH = hashlib.sha256(json.dumps(POLICY, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
ROOT = Path(__file__).resolve().parents[3]
STORE = ROOT / "runtime/v35_provenance/cohort.sqlite3"
OBSERVATIONS = ROOT / "certified_data/v3_5/V3_5_PROSPECTIVE_COHORT.jsonl"
FAIL_CLOSED = {"FUTURE_INFORMATION", "ACQUIRED_AFTER_DECISION", "FUTURE_TICK", "STALE_TICK", "INCOMPLETE_BAR",
               "MACRO_DATE_ONLY", "MACRO_RELEASE_UNKNOWN", "MACRO_VINTAGE_UNKNOWN", "CLOCK_SKEW", "MISSING_ANCESTOR",
               "UNSAFE_ANCESTOR", "CROSS_ASSET_TIMESTAMP_INVALID", "MISSING_REQUIRED_PROVENANCE"}


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def as_utc(value: str | datetime) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if dt.tzinfo is None or dt.utcoffset() != timedelta(0):
        raise ValueError("EXPLICIT_UTC_REQUIRED")
    return dt.astimezone(UTC)


def _stamp(dt: str | datetime) -> str:
    return as_utc(dt).isoformat()


def validate_clock(*, sampled_utc: str | datetime, system_utc: str | datetime, max_skew_seconds: float = POLICY["clock_skew_guard_seconds"]) -> dict[str, Any]:
    sampled, system = as_utc(sampled_utc), as_utc(system_utc)
    skew = abs((system - sampled).total_seconds())
    return {"sampled_utc": sampled.isoformat(), "system_utc": system.isoformat(), "skew_seconds": skew,
            "guard_seconds": max_skew_seconds, "clock_safe": skew <= max_skew_seconds,
            "evidence_hash": digest([sampled.isoformat(), system.isoformat(), skew, max_skew_seconds])}


def acquisition_evidence(*, source: str, instrument: str, raw_value: Any, raw_source_timestamp: str | datetime,
                         normalized_timestamp: str | datetime, acquired_at_utc: str | datetime,
                         information_available_at_utc: str | datetime, decision_time_utc: str | datetime,
                         source_timezone: str, normalization_rule: str, provider_request_id: str,
                         clock_evidence: dict[str, Any], kind: str = "raw", timeframe: str | None = None,
                         bar_close_utc: str | datetime | None = None, max_staleness_seconds: float | None = None,
                         raw_payload: Any | None = None, dependencies: Iterable[dict[str, Any]] = (),
                         extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create immutable value-level evidence; status is conservative and explicit."""
    raw_ts, norm_ts = _stamp(raw_source_timestamp), _stamp(normalized_timestamp)
    acquired, available, decision = map(_stamp, (acquired_at_utc, information_available_at_utc, decision_time_utc))
    value_hash = digest(raw_value)
    payload_hash = digest(raw_payload) if raw_payload is not None else value_hash
    record = {"source": source, "instrument_series": instrument, "raw_value": raw_value,
              "raw_source_timestamp": raw_ts, "normalized_timestamp": norm_ts, "acquired_at_utc": acquired,
              "information_available_at_utc": available, "decision_time_utc": decision,
              "source_timezone": source_timezone, "normalization_rule": normalization_rule,
              "provider_request_identity": provider_request_id, "value_sha256": value_hash,
              "payload_sha256": payload_hash, "provenance_status": "UNCERTIFIED", "kind": kind,
              "timeframe": timeframe, "bar_close_utc": _stamp(bar_close_utc) if bar_close_utc else None,
              "max_staleness_seconds": max_staleness_seconds, "clock_evidence": clock_evidence,
              "dependencies": [d.get("evidence_id") or d.get("payload_sha256") for d in dependencies],
              "extra": extra or {}}
    reasons: list[str] = []
    if not all((source,instrument,provider_request_id,normalization_rule)):
        reasons.append("MISSING_REQUIRED_PROVENANCE")
    try:
        d, src, norm, acq, av = map(as_utc, (decision, raw_ts, norm_ts, acquired, available))
        if av > d: reasons.append("FUTURE_INFORMATION")
        if acq > d: reasons.append("ACQUIRED_AFTER_DECISION")
        # Broker raw wall time is deliberately preserved and can be +10,800s
        # ahead of UTC. Compare its normalized timestamp to decision time.
        if norm > d or (kind != "tick" and src > d): reasons.append("FUTURE_INFORMATION")
        if not clock_evidence.get("clock_safe"): reasons.append("CLOCK_SKEW")
        if kind == "tick":
            age = (d - norm).total_seconds()
            if age < 0: reasons.append("FUTURE_TICK")
            elif max_staleness_seconds is None or age > max_staleness_seconds: reasons.append("STALE_TICK")
            if not isinstance(raw_value, dict) or not (0 < float(raw_value.get("bid", 0)) <= float(raw_value.get("ask", 0))):
                reasons.append("INVALID_BID_ASK")
            if (extra or {}).get("configured_utc_offset_seconds") != POLICY["broker_offset_seconds"]:
                reasons.append("OFFSET_POLICY_MISMATCH")
        if kind == "bar":
            if timeframe not in TIMEFRAME_SECONDS or not bar_close_utc:
                reasons.append("MISSING_REQUIRED_PROVENANCE")
            elif as_utc(bar_close_utc) > d or av < as_utc(bar_close_utc):
                reasons.append("INCOMPLETE_BAR")
        if kind == "macro":
            cls = (extra or {}).get("availability_classification")
            if cls == "DATE_ONLY": reasons.append("MACRO_DATE_ONLY")
            elif cls != "EXACT_RELEASE_TIMESTAMP": reasons.append("MACRO_RELEASE_UNKNOWN")
            if not (extra or {}).get("vintage_id") or not (extra or {}).get("vintage_timestamp_utc"):
                reasons.append("MACRO_VINTAGE_UNKNOWN")
            rel = (extra or {}).get("release_timestamp_utc")
            if rel and as_utc(rel) > d: reasons.append("FUTURE_INFORMATION")
            vintage_ts=(extra or {}).get("vintage_timestamp_utc")
            if vintage_ts and as_utc(vintage_ts)>d: reasons.append("FUTURE_INFORMATION")
        if kind == "derived":
            if not dependencies: reasons.append("MISSING_ANCESTOR")
            if any(d.get("provenance_status") != "CERTIFIED" for d in dependencies): reasons.append("UNSAFE_ANCESTOR")
            ids=(extra or {}).get("source_observation_ids",[])
            if len(ids)!=len(dependencies): reasons.append("MISSING_ANCESTOR")
            if not re.fullmatch(r"[0-9a-f]{64}",(extra or {}).get("calculation_hash","")):
                reasons.append("MISSING_REQUIRED_PROVENANCE")
        if kind == "cross_asset" and (src > d or norm > d or acq > d): reasons.append("CROSS_ASSET_TIMESTAMP_INVALID")
        if kind == "cross_asset":
            if not (extra or {}).get("normalization_verified") or not (extra or {}).get("market_calendar"):
                reasons.append("CROSS_ASSET_TIMESTAMP_INVALID")
            age=(d-norm).total_seconds()
            if age<0 or max_staleness_seconds is None or age>max_staleness_seconds: reasons.append("CROSS_ASSET_TIMESTAMP_INVALID")
            if isinstance(raw_value,dict) and "bid" in raw_value and "ask" in raw_value:
                if not (0<float(raw_value["bid"])<=float(raw_value["ask"])): reasons.append("CROSS_ASSET_TIMESTAMP_INVALID")
            bar_close=(extra or {}).get("completed_bar_close_utc")
            if bar_close and (as_utc(bar_close)>d or as_utc(bar_close)>av): reasons.append("INCOMPLETE_BAR")
    except (ValueError, TypeError, KeyError, OverflowError):
        reasons.append("MISSING_REQUIRED_PROVENANCE")
    reasons = sorted(set(reasons))
    record["provenance_status"] = "CERTIFIED" if not reasons else "FAIL_CLOSED"
    record["training_safe"] = not reasons
    record["exclusion_reasons"] = reasons
    record["evidence_id"] = digest(record)
    return record


def make_tick_evidence(*, raw_epoch_seconds: float, configured_offset_seconds: int, decision_time_utc: str | datetime,
                       acquired_at_utc: str | datetime, bid: float, ask: float, symbol: str, broker_server: str,
                       request_id: str, clock_evidence: dict[str, Any], max_age_seconds: float = 5.0,
                       last: float | None = None, volume: float | None = None) -> dict[str, Any]:
    normalized = normalize_mt5_epoch(raw_epoch_seconds, offset_seconds=configured_offset_seconds, source=symbol)
    raw = {"bid": bid, "ask": ask, "last": last, "volume": volume, "raw_epoch_seconds": raw_epoch_seconds,
           "raw_broker_timestamp": normalized.raw_timestamp.isoformat(), "broker_server": broker_server,
           "symbol": symbol, "configured_utc_offset_seconds": configured_offset_seconds}
    return acquisition_evidence(source="MT5", instrument=symbol, raw_value=raw,
        raw_source_timestamp=normalized.raw_timestamp, normalized_timestamp=normalized.timestamp,
        acquired_at_utc=acquired_at_utc, information_available_at_utc=acquired_at_utc,
        decision_time_utc=decision_time_utc, source_timezone="BROKER_SERVER", normalization_rule="raw_epoch - configured_offset_seconds",
        provider_request_id=request_id, clock_evidence=clock_evidence, kind="tick", max_staleness_seconds=max_age_seconds,
        raw_payload=raw,extra={"configured_utc_offset_seconds":configured_offset_seconds,
                              "time_normalization_version":"v3.5-mt5-explicit-offset-r1"})


def make_bar_evidence(*, source: str, symbol: str, timeframe: str, raw_open_utc: str | datetime,
                      acquired_at_utc: str | datetime, decision_time_utc: str | datetime, ohlcv: dict[str, Any],
                      request_id: str, clock_evidence: dict[str, Any], source_bar_ids: Iterable[str] = (),
                      source_bar_hashes: Iterable[str] = (), locally_aggregated: bool = False,
                      broker_boundary_offset_seconds: int = POLICY["broker_offset_seconds"]) -> dict[str, Any]:
    if timeframe not in TIMEFRAME_SECONDS: raise ValueError("UNKNOWN_TIMEFRAME")
    close = as_utc(raw_open_utc) + timedelta(seconds=TIMEFRAME_SECONDS[timeframe])
    source_ids=list(source_bar_ids); source_hashes=list(source_bar_hashes)
    if (as_utc(raw_open_utc).timestamp()+broker_boundary_offset_seconds)%TIMEFRAME_SECONDS[timeframe] != 0:
        raise ValueError("BAR_BOUNDARY_MISALIGNED")
    try:
        prices=[float(ohlcv[k]) for k in ("open","high","low","close")]
        if not all(math.isfinite(x) and x>0 for x in prices) or max(prices[0],prices[3])>prices[1] or min(prices[0],prices[3])<prices[2]:
            raise ValueError("INVALID_OHLC")
    except (KeyError,TypeError,ValueError) as exc:
        if str(exc)=="INVALID_OHLC":raise
        raise ValueError("INVALID_OHLC") from exc
    if locally_aggregated and (not source_ids or len(source_ids)!=len(source_hashes) or any(not re.fullmatch(r"[0-9a-f]{64}",h) for h in source_hashes)):
        raise ValueError("AGGREGATED_BAR_SOURCE_LINEAGE_REQUIRED")
    value = {**ohlcv, "source_bar_ids": source_ids,"source_bar_hashes":source_hashes}
    return acquisition_evidence(source=source, instrument=symbol, raw_value=value, raw_source_timestamp=raw_open_utc,
        normalized_timestamp=raw_open_utc, acquired_at_utc=acquired_at_utc, information_available_at_utc=acquired_at_utc,
        decision_time_utc=decision_time_utc, source_timezone="UTC", normalization_rule="source UTC bar open; fixed timeframe close",
        provider_request_id=request_id, clock_evidence=clock_evidence, kind="bar", timeframe=timeframe,
        bar_close_utc=close, max_staleness_seconds=TIMEFRAME_SECONDS[timeframe]*2,
        extra={"source_bar_ids":source_ids,"source_bar_hashes":source_hashes,
               "locally_aggregated":locally_aggregated,"bar_close_utc":close.isoformat()})


def make_macro_evidence(*, provider: str, series_id: str, observation_period: str, value: Any,
                        release_timestamp_utc: str | datetime | None, publication_timestamp_utc: str | datetime | None,
                        vintage_id: str | None, vintage_timestamp_utc: str | datetime | None = None,
                        retrieved_at_utc: str | datetime, decision_time_utc: str | datetime,
                        request_id: str, response_payload: Any, clock_evidence: dict[str, Any]) -> dict[str, Any]:
    exact = release_timestamp_utc or publication_timestamp_utc
    cls = "EXACT_RELEASE_TIMESTAMP" if exact else "DATE_ONLY" if observation_period else "UNKNOWN"
    rawts = exact or retrieved_at_utc
    info_available=exact or retrieved_at_utc
    if exact and vintage_timestamp_utc and as_utc(vintage_timestamp_utc)>as_utc(info_available):info_available=vintage_timestamp_utc
    return acquisition_evidence(source=provider, instrument=series_id, raw_value=value, raw_source_timestamp=rawts,
        normalized_timestamp=rawts, acquired_at_utc=retrieved_at_utc, information_available_at_utc=info_available,
        decision_time_utc=decision_time_utc, source_timezone="UTC" if exact else "UNKNOWN",
        normalization_rule="provider release timestamp; no date-to-midnight inference",provider_request_id=request_id,
        clock_evidence=clock_evidence,kind="macro",max_staleness_seconds=10**12,raw_payload=response_payload,
        extra={"observation_period":observation_period,"release_timestamp_utc":_stamp(exact) if exact else None,
               "publication_timestamp_utc":_stamp(publication_timestamp_utc) if publication_timestamp_utc else None,
               "vintage_id":vintage_id,"vintage_timestamp_utc":_stamp(vintage_timestamp_utc) if vintage_timestamp_utc else None,
               "availability_classification":cls})


def make_cross_asset_evidence(*, provider: str, symbol: str, raw_value: dict[str, Any],
                              raw_timestamp: str | datetime, normalized_timestamp: str | datetime,
                              acquired_at_utc: str | datetime, decision_time_utc: str | datetime,
                              request_id: str, source_timezone: str, normalization_rule: str,
                              max_staleness_seconds: float, market_calendar: str,
                              normalization_verified: bool, clock_evidence: dict[str, Any],
                              completed_bar_close_utc: str | datetime | None = None,
                              dependencies: Iterable[dict[str, Any]] = ()) -> dict[str, Any]:
    info=acquired_at_utc
    kind="cross_asset"
    return acquisition_evidence(source=provider,instrument=symbol,raw_value=raw_value,
       raw_source_timestamp=raw_timestamp,normalized_timestamp=normalized_timestamp,acquired_at_utc=acquired_at_utc,
       information_available_at_utc=info,decision_time_utc=decision_time_utc,source_timezone=source_timezone,
       normalization_rule=normalization_rule,provider_request_id=request_id,clock_evidence=clock_evidence,kind=kind,
       timeframe=None,max_staleness_seconds=max_staleness_seconds,dependencies=dependencies,
       bar_close_utc=completed_bar_close_utc,raw_payload=raw_value,
       extra={"normalization_verified":normalization_verified,"market_calendar":market_calendar,
              "completed_bar_close_utc":_stamp(completed_bar_close_utc) if completed_bar_close_utc else None})


def make_derived_evidence(*, feature_name: str, feature_version: str, calculation_hash: str, value: Any,
                          dependencies: list[dict[str, Any]], window_start_utc: str | datetime,
                          window_end_utc: str | datetime, computed_at_utc: str | datetime,
                          decision_time_utc: str | datetime, request_id: str, clock_evidence: dict[str, Any],
                          source_observation_ids: list[str] | None = None) -> dict[str, Any]:
    ancestors = list(dependencies)
    evidence = acquisition_evidence(source="DERIVED", instrument=feature_name, raw_value=value,
        raw_source_timestamp=window_end_utc, normalized_timestamp=window_end_utc, acquired_at_utc=computed_at_utc,
        information_available_at_utc=computed_at_utc, decision_time_utc=decision_time_utc, source_timezone="UTC",
        normalization_rule="deterministic calculation; see calculation hash",provider_request_id=request_id,
        clock_evidence=clock_evidence,kind="derived",max_staleness_seconds=10**12,dependencies=ancestors,
        raw_payload={"value":value,"feature_version":feature_version,"calculation_hash":calculation_hash,
                     "window_start_utc":_stamp(window_start_utc),"window_end_utc":_stamp(window_end_utc)},
        extra={"feature_name":feature_name,"feature_version":feature_version,"calculation_hash":calculation_hash,
               "window_start_utc":_stamp(window_start_utc),"window_end_utc":_stamp(window_end_utc),
               "computed_at_utc":_stamp(computed_at_utc),"source_evidence_ids":[d["evidence_id"] for d in ancestors],
               "source_observation_ids":source_observation_ids or []})
    return evidence


def connect(path: str | Path = STORE) -> sqlite3.Connection:
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    db=storage_connect(path,timeout=30); db.execute("PRAGMA journal_mode=WAL");db.execute("PRAGMA synchronous=FULL")
    db.execute("PRAGMA foreign_keys=ON");db.execute("PRAGMA cache_size=-4096");db.execute("PRAGMA temp_store=FILE")
    db.executescript('''
      CREATE TABLE IF NOT EXISTS provenance(evidence_id TEXT PRIMARY KEY, payload_sha256 TEXT NOT NULL, payload TEXT NOT NULL, appended_at_utc TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS observations(observation_id TEXT PRIMARY KEY, decision_id TEXT UNIQUE NOT NULL, cohort_version TEXT NOT NULL, policy_version TEXT NOT NULL, policy_hash TEXT NOT NULL, schema_version TEXT NOT NULL, collector_version TEXT NOT NULL, decision_time_utc TEXT NOT NULL, snapshot_sha256 TEXT NOT NULL, payload TEXT NOT NULL, eligible INTEGER NOT NULL, reasons TEXT NOT NULL, appended_at_utc TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS outcomes(outcome_id TEXT PRIMARY KEY, observation_id TEXT NOT NULL REFERENCES observations(observation_id), payload TEXT NOT NULL, appended_at_utc TEXT NOT NULL);
      CREATE TRIGGER IF NOT EXISTS provenance_no_update BEFORE UPDATE ON provenance BEGIN SELECT RAISE(ABORT,'APPEND_ONLY'); END;
      CREATE TRIGGER IF NOT EXISTS provenance_no_delete BEFORE DELETE ON provenance BEGIN SELECT RAISE(ABORT,'APPEND_ONLY'); END;
      CREATE TRIGGER IF NOT EXISTS observations_no_update BEFORE UPDATE ON observations BEGIN SELECT RAISE(ABORT,'APPEND_ONLY'); END;
      CREATE TRIGGER IF NOT EXISTS observations_no_delete BEFORE DELETE ON observations BEGIN SELECT RAISE(ABORT,'APPEND_ONLY'); END;
      CREATE TRIGGER IF NOT EXISTS outcomes_no_update BEFORE UPDATE ON outcomes BEGIN SELECT RAISE(ABORT,'APPEND_ONLY'); END;
      CREATE TRIGGER IF NOT EXISTS outcomes_no_delete BEFORE DELETE ON outcomes BEGIN SELECT RAISE(ABORT,'APPEND_ONLY'); END;
    ''')
    return db


def append_only_jsonl(path: str | Path, record: dict[str, Any]) -> str:
    """Crash-safe duplicate-safe journal append; partial tail is rejected on replay."""
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    payload=canonical(record);record_hash=hashlib.sha256(payload).hexdigest()
    line=canonical({"record_sha256":record_hash,"record":record})+b"\n"
    if path.exists():
        with path.open("rb") as f:
            for raw in f:
                if not raw.endswith(b"\n"): raise ValueError("PARTIAL_PROVENANCE_JOURNAL_TAIL")
                old=json.loads(raw)
                if old.get("record_sha256")==record_hash:
                    if old.get("record")!=record: raise ValueError("PROVENANCE_IDENTITY_MUTATED")
                    return record_hash
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
    try:
        with os.fdopen(fd,"ab",buffering=0) as f:
            f.write(line);os.fsync(f.fileno())
    except BaseException:
        raise
    return record_hash


def append_evidence(db: sqlite3.Connection, record: dict[str, Any], *, appended_at_utc: str | datetime) -> str:
    body={k:v for k,v in record.items() if k!="evidence_id"}
    evidence_id=digest(body)
    if record.get("evidence_id") not in (None,evidence_id): raise ValueError("PROVENANCE_EVIDENCE_HASH_MISMATCH")
    for dep_id in record.get("dependencies",[]):
        dep=db.execute("SELECT payload FROM provenance WHERE evidence_id=?",(dep_id,)).fetchone()
        if not dep: raise ValueError("MISSING_ANCESTOR_PROVENANCE")
        ancestor=json.loads(dep[0])
        if ancestor.get("provenance_status")!="CERTIFIED": raise ValueError("UNSAFE_ANCESTOR_PROVENANCE")
    payload={**record,"evidence_id":evidence_id}; text=canonical(payload).decode()
    with db:
        old=db.execute("SELECT payload_sha256,payload FROM provenance WHERE evidence_id=?",(evidence_id,)).fetchone()
        if old:
            if old[0]!=digest(payload) or old[1]!=text: raise ValueError("PROVENANCE_IDENTITY_MUTATED")
            return evidence_id
        db.execute("INSERT INTO provenance VALUES(?,?,?,?)",(evidence_id,digest(payload),text,_stamp(appended_at_utc)))
    return evidence_id


def freeze_decision(*, decision_id: str, decision_time_utc: str | datetime, feature_set_version: str,
                    feature_values: dict[str, Any], feature_evidence: dict[str, dict[str, Any]],
                    required_features: Iterable[str] | None = None,
                    policy_version: str = POLICY_VERSION, policy_hash: str = POLICY_HASH,
                    schema_version: str = SCHEMA_VERSION, collector_version: str = COLLECTOR_VERSION,
                    clock_evidence: dict[str, Any], decision_outputs: dict[str, Any] | None = None) -> dict[str, Any]:
    decision=_stamp(decision_time_utc); reasons=[]; refs={}; normalized={}
    if required_features is None: reasons.append("FEATURE_SET_MANIFEST_MISSING")
    elif set(required_features)!=set(feature_values): reasons.append("FEATURE_SET_MEMBERSHIP_MISMATCH")
    for name,value in feature_values.items():
        evidence=feature_evidence.get(name)
        if not evidence: reasons.append("MISSING_REQUIRED_PROVENANCE:"+name);continue
        if evidence.get("decision_time_utc") != decision:
            reasons.append("PROVENANCE_DECISION_TIME_MISMATCH:"+name)
        if evidence.get("provenance_status")!="CERTIFIED": reasons.extend(evidence.get("exclusion_reasons",[]) or ["UNSAFE_FEATURE:"+name])
        if as_utc(evidence["information_available_at_utc"])>as_utc(decision): reasons.append("FUTURE_FEATURE:"+name)
        if digest(value)!=evidence.get("value_sha256"): reasons.append("FEATURE_VALUE_HASH_MISMATCH:"+name)
        normalized[name]=value;refs[name]=evidence["evidence_id"]
    if not clock_evidence.get("clock_safe"): reasons.append("CLOCK_SKEW")
    reasons=sorted(set(reasons)); body={"decision_id":decision_id,"decision_time_utc":decision,"feature_set_version":feature_set_version,
        "feature_values":normalized,"feature_provenance_refs":refs,"policy_version":policy_version,"policy_hash":policy_hash,
        "schema_version":schema_version,"collector_version":collector_version,"cohort_version":COHORT_VERSION,
        "clock_evidence":clock_evidence,"decision_outputs":decision_outputs or {},
        "provenance_complete":not any(x.startswith("MISSING") for x in reasons),
        "timestamp_safe":not any(x.startswith("FUTURE") or x=="CLOCK_SKEW" or x.startswith("ACQUIRED_AFTER_DECISION") for x in reasons),
        "bar_completion_safe":all(feature_evidence[n].get("kind")!="bar" or feature_evidence[n].get("provenance_status")=="CERTIFIED" for n in refs),
        "macro_safe":all(feature_evidence[n].get("kind")!="macro" or feature_evidence[n].get("provenance_status")=="CERTIFIED" for n in refs),
        "cross_asset_safe":all(feature_evidence[n].get("kind")!="cross_asset" or feature_evidence[n].get("provenance_status")=="CERTIFIED" for n in refs),
        "clock_safe":bool(clock_evidence.get("clock_safe")),"training_eligible":False,
        "eligibility_semantics_version":"v3.5-eligibility-semantics-r2",
        "training_execution_authorized":False,"eligibility_reasons":reasons}
    # Row eligibility describes point-in-time data quality only. Training is a
    # separate campaign decision and remains disabled during collection.
    body["training_eligible"] = not reasons and bool(body["provenance_complete"] and body["timestamp_safe"] and body["bar_completion_safe"] and body["macro_safe"] and body["cross_asset_safe"] and body["clock_safe"])
    body["data_training_eligible"] = body["training_eligible"]
    body["snapshot_sha256"]=digest(body)
    return body


def append_observation(db: sqlite3.Connection, snapshot: dict[str, Any], *, direction: str,
                       appended_at_utc: str | datetime, setup_id: str | None = None) -> str:
    if snapshot.get("cohort_version")!=COHORT_VERSION or snapshot.get("policy_hash")!=POLICY_HASH: raise ValueError("COHORT_POLICY_MISMATCH")
    if snapshot.get("snapshot_sha256")!=digest({k:v for k,v in snapshot.items() if k!="snapshot_sha256"}):
        raise ValueError("DECISION_SNAPSHOT_HASH_MISMATCH")
    for name,ref in snapshot.get("feature_provenance_refs",{}).items():
        found=db.execute("SELECT payload FROM provenance WHERE evidence_id=?",(ref,)).fetchone()
        if not found: raise ValueError("UNRESOLVED_PROVENANCE_REFERENCE")
        evidence=json.loads(found[0]); body={k:v for k,v in evidence.items() if k!="evidence_id"}
        if evidence.get("evidence_id")!=digest(body) or evidence.get("provenance_status")!="CERTIFIED":
            raise ValueError("SNAPSHOT_PROVENANCE_BINDING_INVALID")
        if digest(snapshot.get("feature_values",{}).get(name))!=evidence.get("value_sha256"):
            raise ValueError("SNAPSHOT_PROVENANCE_VALUE_MISMATCH")
    oid=hashlib.sha256((COHORT_VERSION+"|"+snapshot["decision_id"]+"|"+snapshot["snapshot_sha256"]).encode()).hexdigest()
    row={"observation_id":oid,"setup_id":setup_id,"cohort_version":COHORT_VERSION,"schema_version":SCHEMA_VERSION,
         "collector_version":COLLECTOR_VERSION,"policy_version":snapshot["policy_version"],"policy_hash":snapshot["policy_hash"],
         "decision_id":snapshot["decision_id"],"decision_time_utc":snapshot["decision_time_utc"],"direction":direction,
         "snapshot":snapshot,"snapshot_sha256":snapshot["snapshot_sha256"],"training_eligible":snapshot["training_eligible"],
         "data_training_eligible":snapshot.get("data_training_eligible",snapshot["training_eligible"]),
         "training_execution_authorized":False,
         "eligibility_semantics_version":snapshot.get("eligibility_semantics_version","legacy-r1"),
         "eligibility_reasons":snapshot["eligibility_reasons"],"outcome_ref":None,
         "outcome_status":"UNKNOWN_AT_DECISION"}
    payload=canonical(row).decode()
    with db:
        old=db.execute("SELECT snapshot_sha256,payload FROM observations WHERE observation_id=?",(oid,)).fetchone()
        if old:
            if old!=(row["snapshot_sha256"],payload): raise ValueError("OBSERVATION_IDENTITY_MUTATED")
            return oid
        db.execute("INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",(oid,snapshot["decision_id"],COHORT_VERSION,
            snapshot["policy_version"],snapshot["policy_hash"],SCHEMA_VERSION,COLLECTOR_VERSION,snapshot["decision_time_utc"],
            snapshot["snapshot_sha256"],payload,int(row["training_eligible"]),json.dumps(row["eligibility_reasons"]),_stamp(appended_at_utc)))
    return oid


def append_outcome(db: sqlite3.Connection, observation_id: str, *, outcome_id: str, payload: dict[str, Any],
                   appended_at_utc: str | datetime) -> None:
    """Label ledger is isolated and never edits the frozen observation."""
    text=canonical(payload).decode()
    with db:
        old=db.execute("SELECT payload FROM outcomes WHERE outcome_id=?",(outcome_id,)).fetchone()
        if old:
            if old[0]!=text: raise ValueError("OUTCOME_IDENTITY_MUTATED")
            return
        db.execute("INSERT INTO outcomes VALUES(?,?,?,?)",(outcome_id,observation_id,text,_stamp(appended_at_utc)))


def monitor(db: sqlite3.Connection) -> dict[str, Any]:
    total,eligible=db.execute("SELECT count(*),coalesce(sum(eligible),0) FROM observations").fetchone()
    resolved=db.execute("SELECT count(*) FROM outcomes o JOIN observations i USING(observation_id) WHERE i.eligible=1").fetchone()[0]
    reasons={}; source_failures={}; sides={"BUY":0,"SELL":0,"NO_TRADE":0}; dates=set(); sessions={}; regimes={}
    for (evidence_payload,) in db.execute("SELECT payload FROM provenance"):
        evidence=json.loads(evidence_payload)
        if evidence.get("provenance_status")!="CERTIFIED":
            source=evidence.get("source","UNKNOWN");source_failures[source]=source_failures.get(source,0)+1
    # Stream one JSON payload at a time to bound resident memory.
    for eligible_flag,blob in db.execute("SELECT eligible,payload FROM observations"):
        row=json.loads(blob); direction=row.get("direction","NO_TRADE")
        sides[direction if direction in sides else "NO_TRADE"]+=int(bool(eligible_flag))
        dates.add(row.get("decision_time_utc","")[:10])
        values=row.get("snapshot",{}).get("feature_values",{})
        session=values.get("session",values.get("session.session","UNKNOWN"));regime=values.get("regime",values.get("market_regime","UNKNOWN"))
        sessions[session]=sessions.get(session,0)+int(bool(eligible_flag));regimes[regime]=regimes.get(regime,0)+int(bool(eligible_flag))
        if not eligible_flag:
            for reason in row.get("eligibility_reasons",[]): reasons[reason]=reasons.get(reason,0)+1
    return {"cohort_version":COHORT_VERSION,"total_observations":total,"training_eligible_observations":eligible,
            "ineligible_observations":total-eligible,"resolved_training_eligible_setups":resolved,
            "unresolved_training_eligible_setups":eligible-resolved,"eligible_buy":sides["BUY"],
            "eligible_sell":sides["SELL"],"eligible_no_trade":sides["NO_TRADE"],"date_coverage":sorted(dates),
            "session_coverage":sessions,"regime_coverage":regimes,"provenance_failure_counts":reasons,
            "provenance_failures_by_source":source_failures,
            "safe_to_train":False,"continuous_collection_authorized":False}


def evaluate_smoke(cycles: list[dict[str, Any]]) -> dict[str, Any]:
    """Authorize only after two real, adjacent completed-M1 cycles pass provenance gates."""
    failures=[]
    if len(cycles)!=2: failures.append("EXACTLY_TWO_CYCLES_REQUIRED")
    if len(cycles)==2:
        try:
            if as_utc(cycles[1]["completed_m1_utc"])<=as_utc(cycles[0]["completed_m1_utc"]): failures.append("SECOND_COMPLETED_M1_NOT_ADVANCED")
        except (KeyError,ValueError,TypeError): failures.append("COMPLETED_M1_EVIDENCE_MISSING")
        for family in ("raw_tick","bar","derived_lineage","cross_asset","clock","decision_snapshot","outcome_isolation","no_lookahead"):
            if cycles[0].get(family)!="PASS" or cycles[1].get(family)!="PASS": failures.append(f"{family.upper()}_NOT_PASS_BOTH_CYCLES")
        macro_ok=lambda c:c.get("macro") in {"PASS","EXPLICIT_FAIL_CLOSED"}
        if not all(macro_ok(c) for c in cycles): failures.append("MACRO_NOT_CERTIFIED_OR_EXPLICITLY_FAIL_CLOSED")
        if not all(int(c.get("training_eligible",0))>0 for c in cycles): failures.append("NO_TRAINING_ELIGIBLE_ROWS_IN_BOTH_CYCLES")
        if any(c.get("duplicates") is not False or c.get("future_features")!=0 or c.get("stale_decision_ticks")!=0 for c in cycles): failures.append("DUPLICATE_OR_TIME_QUALITY_FAILURE")
        if any(c.get("v34_modified") is not False for c in cycles): failures.append("V34_MUTATION_DETECTED_OR_UNVERIFIED")
    return {"passed":not failures,"failures":sorted(set(failures)),"continuous_collection_authorized":not failures,
            "safe_to_train":False,"fit_model":0,"order_send":0,"demo_execution":False,"real_execution":False}
