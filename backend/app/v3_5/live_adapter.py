"""Bounded, read-only EURUSD live adapter for V3.5 point-in-time smoke runs."""
from __future__ import annotations

import ctypes
import gc
import hashlib
import json
import logging
import os
import re
import statistics
import subprocess
import time
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.core.mt5_offset import configured_offset_seconds
from app.mt5_connection import initialize_checked, shutdown
from app.v3_4.mt5_time import TIMEFRAME_SECONDS, normalize_mt5_epoch
from app.v3_5 import storage_health
from app.v3_5.clock_lifecycle import (
    ClockHealthError,
    ClockQueryError,
    PersistentClockFailure,
    certify,
    log_event,
    recertify,
)
from app.v3_5.health_dimensions import (
    CollectorHealth,
    MarketDataHealthSnapshot,
    RejectionReason,
    TerminalHealth,
    assess_quote,
)
from app.v3_5.provenance import (
    COHORT_VERSION,
    POLICY,
    POLICY_HASH,
    STORE,
    append_evidence,
    append_observation,
    connect,
    digest,
    freeze_decision,
    make_bar_evidence,
    make_derived_evidence,
    make_tick_evidence,
    validate_clock,
)
from app.v3_5.state_io import (
    assert_owner,
    read_text_shared,
    serialized_acquisition,
    write_json_atomic,
)
from app.v3_5.worker_lifecycle import finalize_worker

ROOT = Path(__file__).resolve().parents[3]
MANIFEST_PATH = ROOT / "runtime/v35_provenance/frozen_decision_feature_manifest.json"
REPORT_PATH = ROOT / "runtime/v35_provenance/two_cycle_smoke_report.json"
STATE_PATH = ROOT / "runtime/v35_provenance/live_adapter_state.json"
LOCK_PATH = ROOT / "runtime/v35_provenance/live_adapter.lock"
SYMBOL = "EURUSD"
TIMEFRAMES = ("M1", "M5", "M15", "H1", "H6", "H12")
TF_CONSTANT_NAMES = {
    "M1": "TIMEFRAME_M1", "M5": "TIMEFRAME_M5", "M15": "TIMEFRAME_M15",
    "H1": "TIMEFRAME_H1", "H6": "TIMEFRAME_H6", "H12": "TIMEFRAME_H12",
}


class PreflightError(RuntimeError):
    def __init__(self, message: str, *, reason_code: str | None = None,
                 diagnostic: dict[str, Any] | None = None):
        RuntimeError.__init__(self, message)
        self.reason_code = reason_code or self._classify_reason(message)
        self.diagnostic = diagnostic

    @staticmethod
    def _classify_reason(message: str) -> str:
        upper = message.upper()
        for fragment, reason in (
            ("FUTURE_TICK", RejectionReason.QUOTE_FUTURE.value),
            ("TICK_UNAVAILABLE", RejectionReason.QUOTE_MISSING.value),
            ("INVALID_BID_ASK", RejectionReason.QUOTE_INVALID.value),
            ("TICK_STALE", RejectionReason.QUOTE_STALE.value),
            ("CLOCK", RejectionReason.CLOCK_INVALID.value),
            ("DISCONNECTED", RejectionReason.TERMINAL_DISCONNECTED.value),
            ("API_ERROR", RejectionReason.TERMINAL_API_ERROR.value),
            ("SYMBOL", RejectionReason.SYMBOL_UNAVAILABLE.value),
            ("SESSION", RejectionReason.SESSION_INVALID.value),
        ):
            if fragment in upper:
                return reason
        return RejectionReason.OTHER_EXISTING_SAFETY_REASON.value


_freshness_logger = logging.getLogger("navora.v3_5.market_data_freshness")


def _raise_quote_rejection(message: str, snapshot: MarketDataHealthSnapshot) -> None:
    """Emit one structured diagnostic at the rejection boundary, never per poll."""
    diagnostic = snapshot.to_dict()
    _freshness_logger.warning("market_data_quote_rejected", extra={"market_data_health": diagnostic})
    raise PreflightError(message, reason_code=(snapshot.rejection_reason.value
                                               if snapshot.rejection_reason else None),
                         diagnostic=diagnostic)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _command(*args: str, timeout_seconds: float = 15.0) -> str:
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout_seconds, check=False,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    output = result.stdout + "\n" + result.stderr
    if result.returncode:
        raise PreflightError(f"COMMAND_FAILED:{args[0]}:exit={result.returncode}:output={output}")
    return output


class ClockCertificationError(PreflightError, ClockHealthError):
    def __init__(self, reason: str, diagnostics: dict):
        PreflightError.__init__(self, reason)
        self.diagnostics = diagnostics


_clock_heartbeat = ContextVar("v35_clock_heartbeat", default=None)


def clock_evidence(*, deadline_monotonic: float | None = None) -> dict[str, Any]:
    def query(*args):
        heartbeat = _clock_heartbeat.get()
        if heartbeat is not None:
            heartbeat()
        remaining = 15.0 if deadline_monotonic is None else min(15.0, deadline_monotonic - time.monotonic())
        if remaining <= 0:
            raise ClockQueryError("CLOCK_RECERTIFICATION_DEADLINE")
        try:
            try:
                return _command(*args, timeout_seconds=remaining)
            except (PreflightError, OSError, subprocess.TimeoutExpired) as exc:
                raise ClockQueryError(f"{type(exc).__name__}:{exc}") from exc
        finally:
            if heartbeat is not None:
                heartbeat()
    try:
        evidence, diagnostics = certify(query, _utc_now, validate_clock)
    except ClockHealthError as exc:
        log_event(ROOT, {"event": "CLOCK_CERTIFICATION_FAILED", "diagnostics": exc.diagnostics})
        raise ClockCertificationError(str(exc), exc.diagnostics) from exc
    log_event(ROOT, {"event": "CLOCK_CERTIFICATION_PASS", "diagnostics": diagnostics})
    return evidence


def _lifecycle_state(owner: dict[str, Any], **fields: Any) -> None:
    """Same-thread state refresh; preserves counts/cursors and does not query data."""
    assert_owner(LOCK_PATH, owner)
    state = json.loads(read_text_shared(STATE_PATH))
    stamp = _utc_now().isoformat()
    owner["heartbeat_at_utc"] = stamp
    write_json_atomic(LOCK_PATH, owner)
    write_json_atomic(STATE_PATH, {**state, **owner, **fields, "heartbeat_at_utc": stamp, "updated_at_utc": stamp})


def _recover_collector_clock(owner, failure, *, resume_status="RUNNING"):
    def publish(phase, error, attempt, deadline):
        _lifecycle_state(owner,
            status=resume_status if phase=="HEALTHY" else phase,
            lifecycle_status=phase, clock_status="PASS" if phase=="HEALTHY" else "FAIL",
            last_error=None if phase=="HEALTHY" else str(error),
            clock_failure=None if error is None else error.diagnostics,
            recertification_attempt=attempt, recertification_deadline_monotonic=deadline,
            continuous_collection=phase=="HEALTHY" and resume_status=="RUNNING",
            fit_model=0, order_send=0, demo_execution=False, real_execution=False)
    evidence = recertify(lambda deadline: clock_evidence(deadline_monotonic=deadline), failure, publish,
                         monotonic=time.monotonic, sleep=time.sleep)
    log_event(ROOT, {"event":"COLLECTOR_CLOCK_RECERTIFIED", "clock_evidence":evidence,
                     "missed_bar_backfill":False})
    return evidence


def _failure_lifecycle_status(exc: Exception, *, storage_failure: bool) -> str:
    if storage_failure:
        return "PAUSED"
    if isinstance(exc, ClockHealthError):
        return "FAILED_PERSISTENT"
    return "FAILED"


def _broker_offset_seconds() -> int:
    offset = configured_offset_seconds()
    if offset != int(POLICY["broker_offset_seconds"]):
        raise RuntimeError("V35_BROKER_OFFSET_POLICY_MISMATCH")
    return offset


def _rate_arrays(mt5: Any) -> tuple[dict[str, list[Any]], dict[str, list[Any]], dict[str, list[dict[str, Any]]]]:
    from app.v3.strategy_inputs import StrategyCandle

    offset = _broker_offset_seconds()
    rates_by_tf: dict[str, list[Any]] = {}
    candles_by_tf: dict[str, list[Any]] = {}
    raw_bars_by_tf: dict[str, list[dict[str, Any]]] = {}
    for tf in TIMEFRAMES:
        rates = mt5.copy_rates_from_pos(SYMBOL, getattr(mt5, TF_CONSTANT_NAMES[tf]), 1, 80)
        if rates is None or len(rates) < 80:
            raise PreflightError(f"INSUFFICIENT_COMPLETED_BARS:{tf}:{0 if rates is None else len(rates)}")
        rates_by_tf[tf] = list(rates)
        fetched_at = _utc_now()
        raw_bars_by_tf[tf] = []
        candles_by_tf[tf] = []
        for rate in rates:
            raw_epoch = float(rate["time"])
            stamp = normalize_mt5_epoch(raw_epoch, offset_seconds=offset, source=f"{SYMBOL}_{tf}")
            ohlcv = {
                "open": float(rate["open"]), "high": float(rate["high"]),
                "low": float(rate["low"]), "close": float(rate["close"]),
                "tick_volume": int(rate["tick_volume"]), "real_volume": int(rate["real_volume"]),
                "spread_points": int(rate["spread"]),
            }
            raw_bars_by_tf[tf].append({"raw_epoch":raw_epoch,"stamp":stamp,"ohlcv":ohlcv,"acquired_at":fetched_at})
            candles_by_tf[tf].append(StrategyCandle(
                tf, stamp.timestamp, ohlcv["open"], ohlcv["high"], ohlcv["low"], ohlcv["close"],
                f"DIRECT_MT5:{tf}:v3.5-fixed-offset-r1",
            ))
    return rates_by_tf, candles_by_tf, raw_bars_by_tf


def _feature_inputs(strategy_input: dict[str, Any]) -> dict[str, Any]:
    ctx = strategy_input["contexts"]
    return {
        "trend_state.M1": strategy_input["states"]["M1"],
        "trend_state.M5": strategy_input["states"]["M5"],
        "trend_state.M15": strategy_input["states"]["M15"],
        "trend_state.H1": strategy_input["states"]["H1"],
        "trend_state.H6": strategy_input["states"]["H6"],
        "trend_state.H12": strategy_input["states"]["H12"],
        "normalized_atr": strategy_input["normalized_atr"],
        "close": strategy_input["close"],
        "prior_high": strategy_input["prior_high"],
        "prior_low": strategy_input["prior_low"],
        "m1_return": strategy_input["m1_return"],
        "deviation": strategy_input["deviation"],
        "range_expansion": strategy_input["range_expansion"],
        "range_compression": ctx["M1"]["range_compression"],
        "volatility_state": ctx["M1"]["volatility_state"],
        "continuation": strategy_input["continuation"],
        "regime": strategy_input["regime"],
    }


def _ancestors(evidence_by_tf: dict[str, list[dict[str, Any]]], value_name: str) -> list[dict[str, Any]]:
    def last(tf: str, count: int) -> list[dict[str, Any]]:
        return evidence_by_tf[tf][-count:]
    direct = {
        # EMA recursion consumes the complete bounded 80-bar provider window.
        "trend_state.M1": last("M1", 80), "trend_state.M5": last("M5", 80),
        "trend_state.M15": last("M15", 80), "trend_state.H1": last("H1", 80),
        "trend_state.H6": last("H6", 80), "trend_state.H12": last("H12", 80),
        "normalized_atr": last("M1", 15), "close": last("M1", 1),
        "prior_high": last("M1", 21), "prior_low": last("M1", 21),
        "m1_return": last("M1", 2), "deviation": last("M1", 20),
        "range_expansion": last("M1", 21), "range_compression": last("M1", 21),
        "volatility_state": last("M1", 15),
        "continuation": last("M1", 80) + last("H1", 80) + last("H6", 80) + last("H12", 80),
        "regime": last("M1", 80) + last("M5", 80) + last("M15", 80) + last("H1", 80),
    }
    return direct[value_name]


def _required_lineage_counts(feature_name: str) -> dict[str, int]:
    if feature_name.startswith("trend_state."):
        return {feature_name.split(".", 1)[1]: 80}
    return {
        "normalized_atr": {"M1": 15}, "close": {"M1": 1},
        "prior_high": {"M1": 21}, "prior_low": {"M1": 21},
        "m1_return": {"M1": 2}, "deviation": {"M1": 20},
        "range_expansion": {"M1": 21}, "range_compression": {"M1": 21},
        "volatility_state": {"M1": 15},
        "continuation": {"M1": 80, "H1": 80, "H6": 80, "H12": 80},
        "regime": {"M1": 80, "M5": 80, "M15": 80, "H1": 80},
    }[feature_name]


def _assert_feature_ancestor_manifest(feature_evidence: dict[str, dict[str, Any]]) -> int:
    total = 0
    for name, expected in ((k, _required_lineage_counts(k)) for k in feature_evidence if k != "current_tick_quote"):
        ancestors = feature_evidence[name]
        actual: dict[str, int] = {}
        ids: set[str] = set()
        for ancestor in ancestors:
            if ancestor.get("kind") != "bar" or ancestor.get("provenance_status") != "CERTIFIED":
                raise PreflightError("UNSAFE_FEATURE_ANCESTOR:" + name)
            timeframe = ancestor.get("timeframe")
            actual[timeframe] = actual.get(timeframe, 0) + 1
            if ancestor.get("evidence_id") in ids:
                raise PreflightError("DUPLICATE_FEATURE_ANCESTOR:" + name)
            ids.add(ancestor.get("evidence_id"))
        if actual != expected:
            raise PreflightError(f"INCOMPLETE_FEATURE_LINEAGE:{name}:{actual}:{expected}")
        total += len(ancestors)
    return total


def _wait_completed_m1(mt5: Any, after_open: datetime, timeout_seconds: int = 90, *,
                       poll_seconds: float = 0.2, heartbeat_callback: Any = None) -> tuple[datetime, dict[str, Any]]:
    deadline = time.monotonic() + timeout_seconds
    last_heartbeat = time.monotonic()
    while time.monotonic() < deadline:
        if heartbeat_callback and time.monotonic() - last_heartbeat >= 5:
            heartbeat_callback()
            last_heartbeat = time.monotonic()
        now = _utc_now()
        rates = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M1, 1, 2)
        if rates is not None and len(rates):
            candidates = []
            for rate in rates:
                stamp = normalize_mt5_epoch(float(rate["time"]), offset_seconds=_broker_offset_seconds(), source="EURUSD_M1")
                close_at = stamp.timestamp + timedelta(seconds=60)
                if stamp.timestamp > after_open and close_at <= now:
                    candidates.append((stamp.timestamp, close_at))
            if candidates:
                open_at, close_at = max(candidates)
                if open_at != after_open + timedelta(minutes=1):
                    raise PreflightError("NON_CONSECUTIVE_COMPLETED_M1")
                return open_at, {"bar_open_utc": open_at.isoformat(), "bar_close_utc": close_at.isoformat()}
        time.sleep(poll_seconds)
    raise PreflightError("WAIT_COMPLETED_M1_TIMEOUT")


def _write_state(status: str, *, preserve_failed_temp: bool = False, **extra: Any) -> None:
    if extra.get("worker_id"):
        assert_owner(LOCK_PATH, {"pid": os.getpid(), "worker_id": extra["worker_id"]})
    value = {"status": status, "pid": os.getpid(), "updated_at_utc": _utc_now().isoformat(),
             "fit_model": 0, "order_send": 0, "continuous_collection": False,
             "storage_health": storage_health.sample(STORE), **extra}
    write_json_atomic(STATE_PATH, value, preserve_failed_temp=preserve_failed_temp)


def _tick(mt5: Any, decision_time: datetime, clock: dict[str, Any], cycle_id: str) -> dict[str, Any]:
    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is None:
        raise PreflightError("EURUSD_TICK_UNAVAILABLE")
    acquired = _utc_now()
    raw_epoch = float(tick.time_msc) / 1000 if getattr(tick, "time_msc", 0) else float(tick.time)
    return make_tick_evidence(
        raw_epoch_seconds=raw_epoch,
        configured_offset_seconds=_broker_offset_seconds(),
        decision_time_utc=decision_time,
        acquired_at_utc=acquired,
        bid=float(tick.bid), ask=float(tick.ask), symbol=SYMBOL,
        broker_server="ICMarketsSC-Demo", request_id=cycle_id,
        clock_evidence=clock, max_age_seconds=5.0,
        last=float(getattr(tick, "last", 0.0) or 0.0),
        volume=float(getattr(tick, "volume", 0.0) or 0.0),
    )


def _fresh_tick(mt5: Any, *, wait_seconds: float = 8.0,
                collector_state: CollectorHealth = CollectorHealth.UNKNOWN) -> tuple[Any, datetime, datetime]:
    """Return only a sampled quote inside the existing 0–5 second age gate.

    R6 showed that EURUSD can be quiet while control symbols advance. That is
    useful feed diagnosis, never permission to use a stale quote. The R6
    evidence does not support promoting another production threshold.
    """
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            tick = mt5.symbol_info_tick(SYMBOL)
        except Exception as exc:
            acquired = _utc_now()
            diagnostic = assess_quote(observed_at=acquired, quote_timestamp=None,
                bid=None, ask=None, max_age_seconds=float(POLICY["tick_max_age_seconds"]),
                quote_present=False, terminal_state=TerminalHealth.API_ERROR,
                collector_state=collector_state)
            payload = diagnostic.to_dict()
            payload["rejection_reason"] = RejectionReason.TERMINAL_API_ERROR.value
            _freshness_logger.warning("market_data_api_error", extra={"market_data_health": payload,
                "api_error_type": type(exc).__name__})
            raise PreflightError("MT5_TICK_API_ERROR:" + type(exc).__name__,
                reason_code=RejectionReason.TERMINAL_API_ERROR.value, diagnostic=payload) from exc
        acquired = _utc_now()
        if tick is None:
            diagnostic = assess_quote(observed_at=acquired, quote_timestamp=None,
                bid=None, ask=None, max_age_seconds=float(POLICY["tick_max_age_seconds"]),
                quote_present=False, collector_state=collector_state)
            _raise_quote_rejection("EURUSD_TICK_UNAVAILABLE", diagnostic)
        raw_epoch = float(tick.time_msc) / 1000 if getattr(tick, "time_msc", 0) else float(tick.time)
        normalized = normalize_mt5_epoch(raw_epoch, offset_seconds=_broker_offset_seconds(), source=SYMBOL).timestamp
        age = (acquired - normalized).total_seconds()
        diagnostic = assess_quote(observed_at=acquired, quote_timestamp=normalized,
            bid=getattr(tick, "bid", None), ask=getattr(tick, "ask", None),
            max_age_seconds=float(POLICY["tick_max_age_seconds"]),
            collector_state=collector_state)
        if age < 0:
            _raise_quote_rejection(f"EURUSD_FUTURE_TICK:{age:.6f}", diagnostic)
        if diagnostic.quote_age_state.value == "INVALID":
            _raise_quote_rejection("EURUSD_INVALID_BID_ASK", diagnostic)
        if diagnostic.quote_age_eligible:
            return tick, acquired, normalized
        if time.monotonic() >= deadline:
            _raise_quote_rejection(f"EURUSD_TICK_STALE:{age:.6f}", diagnostic)
        time.sleep(0.25)


def _decision_direction(outputs: dict[str, dict[str, Any]]) -> str:
    setups = [item for item in outputs.values() if item["setup"]]
    if not setups:
        return "NO_SETUP"
    if any(item["side"] not in {"BUY", "SELL"} for item in setups):
        return "EXCLUDED"
    sides = {item["side"] for item in setups}
    if sides == {"BUY"}:
        return "BUY_SETUP"
    if sides == {"SELL"}:
        return "SELL_SETUP"
    return "EXCLUDED"


def _decision_identity(m1_open: datetime, *, continuous: bool) -> str:
    if continuous:
        return f"{COHORT_VERSION}|{POLICY_HASH}|EURUSD|M1|{m1_open.isoformat()}"
    return f"eurusd-m1:{m1_open.isoformat()}"


def _collect_cycle(mt5: Any, db: Any, m1_open: datetime, m1_info: dict[str, Any], cycle_number: int,
                   *, continuous: bool = False, heartbeat_callback=None) -> dict[str, Any]:
    from app.v3.strategy_families import evaluate_all
    from app.v3.strategy_inputs import STRATEGY_INPUT_POLICY_VERSION, build_strategy_input

    cycle_id = f"EURUSD:{m1_open.isoformat()}"
    decision_id = _decision_identity(m1_open, continuous=continuous)
    existing = db.execute("SELECT observation_id,snapshot_sha256,payload FROM observations WHERE decision_id=?", (decision_id,)).fetchone()
    if existing:
        return {"cycle": cycle_number, "decision_id": decision_id, "idempotent_replay": True,
                "observation_id": existing[0], "snapshot_sha256": existing[1],
                "completed_m1": m1_info["bar_close_utc"]}

    clock = clock_evidence()
    if not clock["clock_safe"]:
        raise PreflightError("CLOCK_UNSAFE_AT_DECISION_CYCLE")
    if heartbeat_callback is not None:
        heartbeat_callback()
    provider_started = time.perf_counter()
    rates_by_tf, candles_by_tf, raw_bars_by_tf = _rate_arrays(mt5)
    provider_latency = time.perf_counter() - provider_started
    compute_started = time.perf_counter()
    provisional_decision = _utc_now()
    completed_candles = [c for tf in TIMEFRAMES for c in candles_by_tf[tf]
                         if c.timestamp + timedelta(seconds=TIMEFRAME_SECONDS[tf]) <= provisional_decision]
    strategy_input = build_strategy_input(
        completed_candles, provisional_decision
    )
    features = _feature_inputs(strategy_input)
    outputs = {
        name: {"setup": result.setup, "side": result.side, "reason_codes": list(result.reason_codes),
               "conditions_passed": list(result.conditions_passed), "conditions_failed": list(result.conditions_failed),
               "strategy_policy_version": result.strategy_policy_version, "strategy_policy_hash": result.strategy_policy_hash}
        for name, result in evaluate_all(strategy_input).items()
    }
    computed_at = _utc_now()
    implementation_hash = hashlib.sha256(
        (ROOT / "backend/app/v3/strategy_inputs.py").read_bytes()
        + (ROOT / "backend/app/v3/strategy_families.py").read_bytes()
    ).hexdigest()

    # Capture the current quote after feature calculation so tick freshness is
    # evaluated at a decision time immediately following its acquisition.
    tick_clock = clock
    quote, tick_acquired, normalized = _fresh_tick(mt5,
        collector_state=CollectorHealth.HEALTHY if continuous else CollectorHealth.UNKNOWN)
    decision_time = _utc_now()
    quote_acquired = tick_acquired
    raw_epoch = float(quote.time_msc) / 1000 if getattr(quote, "time_msc", 0) else float(quote.time)
    tick_ev = make_tick_evidence(
        raw_epoch_seconds=raw_epoch, configured_offset_seconds=_broker_offset_seconds(),
        decision_time_utc=decision_time, acquired_at_utc=quote_acquired,
        bid=float(quote.bid), ask=float(quote.ask), symbol=SYMBOL,
        broker_server="ICMarketsSC-Demo", request_id=cycle_id,
        clock_evidence=tick_clock, max_age_seconds=5.0,
        last=float(getattr(quote, "last", 0.0) or 0.0),
        volume=float(getattr(quote, "volume", 0.0) or 0.0),
    )
    if tick_ev["provenance_status"] != "CERTIFIED":
        reasons = tick_ev["exclusion_reasons"]
        if any(reason in reasons for reason in ("STALE_TICK", "FUTURE_TICK", "INVALID_BID_ASK")):
            diagnostic = assess_quote(observed_at=decision_time, quote_timestamp=normalized,
                bid=float(quote.bid), ask=float(quote.ask),
                max_age_seconds=float(POLICY["tick_max_age_seconds"]),
                collector_state=CollectorHealth.HEALTHY if continuous else CollectorHealth.UNKNOWN)
            if diagnostic.rejection_reason is not None:
                _raise_quote_rejection("EURUSD_TICK_FAIL_CLOSED:" + ",".join(reasons), diagnostic)
        raise PreflightError("EURUSD_TICK_FAIL_CLOSED:" + ",".join(reasons),
            reason_code="CLOCK_INVALID" if "CLOCK_SKEW" in reasons else "OTHER_EXISTING_SAFETY_REASON")
    evidence_by_tf: dict[str, list[dict[str, Any]]] = {}
    for tf in TIMEFRAMES:
        evidence_by_tf[tf] = []
        for raw_bar in raw_bars_by_tf[tf]:
            stamp = raw_bar["stamp"]
            if stamp.timestamp + timedelta(seconds=TIMEFRAME_SECONDS[tf]) > provisional_decision:
                continue
            evidence = make_bar_evidence(
                source="MT5:ICMarketsSC-Demo", symbol=SYMBOL, timeframe=tf,
                raw_open_utc=stamp.timestamp, acquired_at_utc=raw_bar["acquired_at"],
                decision_time_utc=decision_time, ohlcv=raw_bar["ohlcv"],
                request_id=f"{cycle_id}:{tf}:{raw_bar['raw_epoch']:.3f}",
                clock_evidence=tick_clock, broker_boundary_offset_seconds=_broker_offset_seconds(),
            )
            evidence["raw_source_timestamp"] = stamp.raw_timestamp.isoformat()
            evidence["source_timezone"] = "MT5_SERVER_EPOCH_UTC_PLUS_03"
            evidence["normalization_rule"] = f"V3.5 broker epoch minus configured offset {_broker_offset_seconds()} seconds exactly once"
            evidence["extra"].update({"raw_epoch_seconds":raw_bar["raw_epoch"],
                "configured_utc_offset_seconds":_broker_offset_seconds(),"broker_server":"ICMarketsSC-Demo",
                "normalized_open_utc":stamp.timestamp.isoformat()})
            evidence["evidence_id"] = digest({k:v for k,v in evidence.items() if k!="evidence_id"})
            evidence_by_tf[tf].append(evidence)
        evidence_by_tf[tf].sort(key=lambda item:item["normalized_timestamp"])
    if any(not evidence_by_tf[tf] for tf in TIMEFRAMES):
        raise PreflightError("NO_COMPLETED_BAR_ANCESTOR")
    write_started = time.perf_counter()
    for evidence in (ev for tf in TIMEFRAMES for ev in evidence_by_tf[tf]):
        append_evidence(db,evidence,appended_at_utc=decision_time)
        if heartbeat_callback is not None:
            heartbeat_callback()
    feature_evidence: dict[str,dict[str,Any]] = {}
    feature_ancestors: dict[str, list[dict[str, Any]]] = {}
    for name,value in features.items():
        deps=_ancestors(evidence_by_tf,name)
        if any(dep.get("provenance_status")!="CERTIFIED" for dep in deps):
            raise PreflightError(f"UNSAFE_FEATURE_ANCESTOR:{name}")
        dep_closes=[datetime.fromisoformat(dep["bar_close_utc"]) for dep in deps]
        record=make_derived_evidence(feature_name=name,feature_version=STRATEGY_INPUT_POLICY_VERSION,
            calculation_hash=implementation_hash,value=value,dependencies=deps,
            window_start_utc=min(datetime.fromisoformat(d["normalized_timestamp"]) for d in deps),
            window_end_utc=max(dep_closes),computed_at_utc=computed_at,decision_time_utc=decision_time,
            request_id=f"{cycle_id}:feature:{name}",clock_evidence=tick_clock,
            source_observation_ids=[d["evidence_id"] for d in deps])
        append_evidence(db,record,appended_at_utc=decision_time)
        feature_evidence[name]=record
        feature_ancestors[name]=deps
    append_evidence(db,tick_ev,appended_at_utc=decision_time)
    features["current_tick_quote"] = tick_ev["raw_value"]
    feature_evidence["current_tick_quote"] = tick_ev
    lineage_ancestor_count = _assert_feature_ancestor_manifest(feature_ancestors)
    required = list(features)
    feature_manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf8"))
    values_by_name = {f["feature_name"] for f in feature_manifest["features"] if f["status"] == "REQUIRED"}
    if not values_by_name.issubset(features):
        raise PreflightError("FEATURE_MANIFEST_MEMBERSHIP_MISMATCH")
    snapshot = freeze_decision(
        decision_id=decision_id, decision_time_utc=decision_time,
        feature_set_version=feature_manifest["manifest_version"],
        feature_values=features, feature_evidence=feature_evidence,
        required_features=required, clock_evidence=tick_clock,
        decision_outputs={"strategy_policy_version": "v3-strategy-family-r1",
                          "families": outputs, "classification": _decision_direction(outputs),
                          "aggregation_performed": False},
    )
    if not snapshot["training_eligible"]:
        raise PreflightError("DECISION_SNAPSHOT_INELIGIBLE:" + ",".join(snapshot["eligibility_reasons"]))
    direction = _decision_direction(outputs)
    if direction == "EXCLUDED":
        # Conflicting frozen family outputs do not create a synthetic side.
        snapshot["training_eligible"] = False
        snapshot["data_training_eligible"] = False
        snapshot["eligibility_reasons"] = sorted(set(snapshot["eligibility_reasons"] + ["CONFLICTING_FROZEN_FAMILY_SIDES"]))
        snapshot["snapshot_sha256"] = digest({k:v for k,v in snapshot.items() if k!="snapshot_sha256"})
    obs_id = append_observation(db, snapshot, direction=direction, appended_at_utc=_utc_now())
    storage_audit = _verify_cycle_storage(db, obs_id)
    write_seconds = time.perf_counter() - write_started
    tick_age = (quote_acquired - datetime.fromisoformat(tick_ev["normalized_timestamp"])).total_seconds()
    return {
        "cycle": cycle_number, "decision_id": decision_id, "completed_m1": m1_info,
        "decision_time_utc": snapshot["decision_time_utc"], "observation_id": obs_id,
        "snapshot_sha256": snapshot["snapshot_sha256"], "policy_hash": POLICY_HASH,
        "training_eligible": snapshot["training_eligible"],
        "data_training_eligible": snapshot["data_training_eligible"],
        "training_execution_authorized": False,
        "eligibility_semantics_version": snapshot["eligibility_semantics_version"],
        "eligibility_reasons": snapshot["eligibility_reasons"], "direction": direction,
        "tick_evidence_id": tick_ev["evidence_id"], "tick_timestamp_utc": tick_ev["normalized_timestamp"],
        "tick_age_seconds": tick_age, "tick_bid": quote.bid, "tick_ask": quote.ask,
        "bars_by_timeframe": {tf: {"count": len(evidence_by_tf[tf]),
            "latest_open_utc": max(evidence_by_tf[tf], key=lambda x:x["normalized_timestamp"])["normalized_timestamp"],
            "latest_close_utc": max(evidence_by_tf[tf], key=lambda x:x["bar_close_utc"])["bar_close_utc"],
            "evidence_ids": [x["evidence_id"] for x in evidence_by_tf[tf]]} for tf in TIMEFRAMES},
        "derived_feature_count": len(feature_evidence)-1,
        "lineage_ancestor_count": lineage_ancestor_count,
        "storage_audit": storage_audit,
        "derived_feature_refs": {k:v["evidence_id"] for k,v in feature_evidence.items() if k!="current_tick_quote"},
        "clock_evidence": tick_clock, "strategy_outputs": outputs,
        "provider_latency_seconds": provider_latency,
        "provenance_write_seconds": write_seconds,
        "cycle_duration_seconds": time.perf_counter()-compute_started,
        "snapshot": snapshot,
    }


def run_two_cycle_smoke() -> dict[str, Any]:
    os.environ.update({"APP_ENV":"paper", "MT5_READ_ONLY":"true", "TRADING_EXECUTION_ENABLED":"false",
                       "DEMO_EXECUTION_ENABLED":"false", "REAL_EXECUTION_ENABLED":"false",
                       "FIT_MODEL":"0", "ORDER_SEND":"0"})
    _broker_offset_seconds()
    if LOCK_PATH.exists():
        raise PreflightError("V35_ADAPTER_LOCK_EXISTS; recovery requires explicit stale-owner audit")
    owner = {"pid": os.getpid(), "worker_id": str(uuid.uuid4()), "acquired_at_utc": _utc_now().isoformat()}
    fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf8") as f:
        f.write(json.dumps(owner)); f.flush(); os.fsync(f.fileno())
    mt5 = None
    db = None
    report: dict[str, Any] = {
        "campaign": "V3.5 EURUSD two-cycle prospective smoke",
        "target_symbol": SYMBOL, "xauusd_required": False,
        "cohort_version": "v3.5-provenance-certified-r1",
        "schema_version": "v3.5-provenance-schema-r1",
        "policy_hash": POLICY_HASH, "fit_model": 0, "order_send": 0,
        "demo_execution": False, "real_execution": False,
        "cycles": [], "continuous_collection_started": False,
    }
    try:
        _write_state("RUNNING", **owner)
        clock = clock_evidence()
        if not clock["clock_safe"]:
            raise PreflightError("CLOCK_UNSAFE")
        import MetaTrader5 as mt5_module
        mt5 = mt5_module
        connection = initialize_checked(mt5)
        report["mt5_connection"] = connection
        if connection.get("server") != "ICMarketsSC-Demo":
            raise PreflightError("UNEXPECTED_MT5_SERVER")
        account = mt5.account_info()
        if account is None or int(getattr(account,"trade_mode",-1)) != 0:
            raise PreflightError("ACCOUNT_NOT_READ_ONLY_DEMO_MODE")
        symbol_info = mt5.symbol_info(SYMBOL)
        if symbol_info is None or not symbol_info.visible:
            raise PreflightError("EURUSD_SYMBOL_NOT_VISIBLE")
        mt5.symbol_select(SYMBOL, True)
        # Measure advancing live quotes for 30 seconds; stale intermediate
        # samples are retained as diagnostics, while the final gate is strict.
        tick_samples=[]
        for sample_index in range(6):
            local=_utc_now(); tick=mt5.symbol_info_tick(SYMBOL)
            if tick is None:
                diagnostic=assess_quote(observed_at=local,quote_timestamp=None,bid=None,ask=None,
                    max_age_seconds=float(POLICY["tick_max_age_seconds"]),quote_present=False)
                _raise_quote_rejection("EURUSD_TICK_UNAVAILABLE_AT_STARTUP",diagnostic)
            stamp=normalize_mt5_epoch(tick.time_msc/1000 if getattr(tick,"time_msc",0) else tick.time,
                                      offset_seconds=_broker_offset_seconds(),source=SYMBOL)
            tick_samples.append({"system_utc":local.isoformat(),"raw_time":tick.time,
                "raw_time_msc":getattr(tick,"time_msc",None),"normalized_utc":stamp.timestamp.isoformat(),
                "age_seconds":(local-stamp.timestamp).total_seconds(),"bid":tick.bid,"ask":tick.ask})
            if sample_index<5: time.sleep(6)
        if len({s["raw_time_msc"] or s["raw_time"] for s in tick_samples})<2:
            raise PreflightError("EURUSD_TICK_NOT_ADVANCING")
        last_tick=tick_samples[-1]
        if not (0<float(last_tick["bid"])<=float(last_tick["ask"])) or not 0<=last_tick["age_seconds"]<=5:
            diagnostic=assess_quote(observed_at=datetime.fromisoformat(last_tick["system_utc"]),
                quote_timestamp=datetime.fromisoformat(last_tick["normalized_utc"]),
                bid=last_tick["bid"],ask=last_tick["ask"],
                max_age_seconds=float(POLICY["tick_max_age_seconds"]))
            _raise_quote_rejection(f"EURUSD_PREFLIGHT_TICK_FAIL:age={last_tick['age_seconds']:.3f}",diagnostic)
        decision=_utc_now()
        _,candles,raw_bars=_rate_arrays(mt5)
        from app.v3.strategy_families import evaluate_all
        from app.v3.strategy_inputs import build_strategy_input
        strategy_input=build_strategy_input([c for tf in TIMEFRAMES for c in candles[tf]],decision)
        preflight_features=_feature_inputs(strategy_input)
        manifest=json.loads(MANIFEST_PATH.read_text(encoding="utf8"))
        required={f["feature_name"] for f in manifest["features"] if f["status"]=="REQUIRED"}
        if required-set(preflight_features):
            raise PreflightError("FEATURE_MANIFEST_UNSATISFIED")
        if any(preflight_features[n] is None or preflight_features[n]=="UNAVAILABLE" for n in required):
            raise PreflightError("REQUIRED_EURUSD_FEATURE_UNAVAILABLE")
        bar_report={}
        for tf in TIMEFRAMES:
            completed=[b for b in raw_bars[tf] if b["stamp"].timestamp+timedelta(seconds=TIMEFRAME_SECONDS[tf])<=decision]
            if not completed:
                raise PreflightError(f"NO_COMPLETED_EURUSD_BAR:{tf}")
            latest=max(completed,key=lambda b:b["stamp"].timestamp)
            close=latest["stamp"].timestamp+timedelta(seconds=TIMEFRAME_SECONDS[tf])
            age=(decision-close).total_seconds()
            if age<0 or age>2*TIMEFRAME_SECONDS[tf]:
                raise PreflightError(f"STALE_EURUSD_BAR:{tf}:{age:.3f}")
            bar_report[tf]={"status":"PASS","count":len(completed),
                "latest_open_utc":latest["stamp"].timestamp.isoformat(),
                "completion_utc":close.isoformat(),"age_seconds":age}
        clock=clock_evidence()
        final_local=_utc_now();final_tick=mt5.symbol_info_tick(SYMBOL)
        if final_tick is None:
            diagnostic=assess_quote(observed_at=final_local,quote_timestamp=None,bid=None,ask=None,
                max_age_seconds=float(POLICY["tick_max_age_seconds"]),quote_present=False)
            _raise_quote_rejection("EURUSD_FINAL_TICK_FAIL:missing",diagnostic)
        final_stamp=normalize_mt5_epoch(final_tick.time_msc/1000 if getattr(final_tick,"time_msc",0) else final_tick.time,
                                        offset_seconds=_broker_offset_seconds(),source=SYMBOL)
        final_age=(final_local-final_stamp.timestamp).total_seconds()
        if final_age<0 or final_age>5 or not (0<float(final_tick.bid)<=float(final_tick.ask)):
            diagnostic=assess_quote(observed_at=final_local,quote_timestamp=final_stamp.timestamp,
                bid=final_tick.bid,ask=final_tick.ask,
                max_age_seconds=float(POLICY["tick_max_age_seconds"]))
            _raise_quote_rejection(f"EURUSD_FINAL_TICK_FAIL:age={final_age:.3f}",diagnostic)
        base_open=max((b["stamp"].timestamp for b in raw_bars["M1"] if b["stamp"].timestamp+timedelta(minutes=1)<=decision))
        report["preflight"] = {"system_clock":"PASS", "mt5_connection":"PASS", "eurusd_tick":"PASS",
            "tick_samples":tick_samples,"tick_age_seconds":final_age,
            "timeframes":bar_report,"feature_manifest":"PASS","required_feature_count":len(required),
            "strategy_outputs":{k:{"setup":v.setup,"side":v.side} for k,v in evaluate_all(strategy_input).items()},
            "macro":"NOT_REQUIRED_BY_FROZEN_EURUSD_DECISION","cross_asset":"NOT_REQUIRED_BY_FROZEN_EURUSD_DECISION",
            "no_lookahead":"PASS","clock_evidence":clock,"xauusd":"EXCLUDED_UNUSED"}
        db = connect(STORE)
        cycles=[]
        previous = base_open
        for idx in (1,2):
            m1_open, info = _wait_completed_m1(mt5, previous)
            cycle = _collect_cycle(mt5,db,m1_open,info,idx)
            cycles.append(cycle)
            previous = m1_open
            _write_state("RUNNING", **owner, cycles_completed=idx, last_completed_m1=info["bar_close_utc"])
        report["cycles"] = cycles
        ids=[c["observation_id"] for c in cycles]
        if len(set(ids))!=2 or cycles[0]["completed_m1"]["bar_open_utc"]==cycles[1]["completed_m1"]["bar_open_utc"]:
            raise PreflightError("DUPLICATE_OR_NONADVANCING_CYCLES")
        report["two_cycle_smoke_pass"] = all(c["training_eligible"] for c in cycles)
        report["storage_monitor"] = __import__("app.v3_5.provenance",fromlist=["monitor"]).monitor(db)
        report["safe_to_continue_certified_collection"] = False
        report["safe_to_train"] = False
        report["restart_idempotency"] = "CHECKED_BY_DETERMINISTIC_DECISION_ID_GUARD"
        REPORT_PATH.write_text(json.dumps(report,indent=2,sort_keys=True,default=str)+"\n",encoding="utf8")
        _write_state("STOPPED", **owner, cycles_completed=2, reason="EXACTLY_TWO_SMOKE_CYCLES_COMPLETE")
        return report
    except Exception as exc:
        report["two_cycle_smoke_pass"] = False
        report["failure"] = f"{type(exc).__name__}:{exc}"
        report["safe_to_continue_certified_collection"] = False
        report["safe_to_train"] = False
        REPORT_PATH.write_text(json.dumps(report,indent=2,sort_keys=True,default=str)+"\n",encoding="utf8")
        _write_state("FAILED", **owner, reason=report["failure"])
        raise
    finally:
        if db is not None:
            db.close()
        if mt5 is not None:
            shutdown(mt5)
        try:
            if LOCK_PATH.exists() and json.loads(read_text_shared(LOCK_PATH)).get("worker_id")==owner["worker_id"]:
                LOCK_PATH.unlink()
        except OSError:
            pass


def _system_preflight(mt5: Any) -> dict[str, Any]:
    clock = clock_evidence()
    if not clock["clock_safe"]:
        raise PreflightError("CLOCK_UNSAFE_AT_STARTUP", reason_code=RejectionReason.CLOCK_INVALID.value)
    try:
        connection = mt5.terminal_info()
    except Exception as exc:
        raise PreflightError("MT5_TERMINAL_API_ERROR:" + type(exc).__name__,
            reason_code=RejectionReason.TERMINAL_API_ERROR.value) from exc
    if connection is None or not connection.connected:
        raise PreflightError("MT5_DISCONNECTED_AT_STARTUP",
            reason_code=RejectionReason.TERMINAL_DISCONNECTED.value)
    try:
        account = mt5.account_info()
    except Exception as exc:
        raise PreflightError("MT5_ACCOUNT_API_ERROR:" + type(exc).__name__,
            reason_code=RejectionReason.TERMINAL_API_ERROR.value) from exc
    if account is None or account.server != "ICMarketsSC-Demo" or int(getattr(account, "trade_mode", -1)) != 0:
        raise PreflightError("MT5_ACCOUNT_NOT_READ_ONLY_DEMO")
    try:
        info = mt5.symbol_info(SYMBOL)
    except Exception as exc:
        raise PreflightError("MT5_SYMBOL_API_ERROR:" + type(exc).__name__,
            reason_code=RejectionReason.TERMINAL_API_ERROR.value) from exc
    if info is None:
        raise PreflightError("EURUSD_SYMBOL_UNAVAILABLE",
            reason_code=RejectionReason.SYMBOL_UNAVAILABLE.value)
    if not info.visible and not mt5.symbol_select(SYMBOL, True):
        raise PreflightError("EURUSD_SYMBOL_SELECT_FAILED")
    try:
        info = mt5.symbol_info(SYMBOL)
    except Exception as exc:
        raise PreflightError("MT5_SYMBOL_API_ERROR:" + type(exc).__name__,
            reason_code=RejectionReason.TERMINAL_API_ERROR.value) from exc
    if info is None or not info.visible:
        raise PreflightError("EURUSD_SYMBOL_NOT_VISIBLE",
            reason_code=RejectionReason.SYMBOL_UNAVAILABLE.value)

    tick_samples: list[dict[str, Any]] = []
    for idx in range(6):
        tick = mt5.symbol_info_tick(SYMBOL)
        if tick is None:
            diagnostic = assess_quote(observed_at=_utc_now(), quote_timestamp=None,
                bid=None, ask=None, max_age_seconds=float(POLICY["tick_max_age_seconds"]),
                quote_present=False)
            _raise_quote_rejection("EURUSD_TICK_UNAVAILABLE_AT_STARTUP", diagnostic)
        acquired = _utc_now()
        raw = float(tick.time_msc) / 1000 if getattr(tick, "time_msc", 0) else float(tick.time)
        normalized = normalize_mt5_epoch(raw, offset_seconds=_broker_offset_seconds(), source=SYMBOL).timestamp
        age = (acquired - normalized).total_seconds()
        diagnostic = assess_quote(observed_at=acquired, quote_timestamp=normalized,
            bid=getattr(tick, "bid", None), ask=getattr(tick, "ask", None),
            max_age_seconds=float(POLICY["tick_max_age_seconds"]))
        if age < 0:
            _raise_quote_rejection(f"EURUSD_FUTURE_TICK_AT_STARTUP:{age:.6f}", diagnostic)
        if diagnostic.quote_age_state.value == "INVALID":
            _raise_quote_rejection("EURUSD_INVALID_BID_ASK_AT_STARTUP", diagnostic)
        tick_samples.append({"acquired_at_utc": acquired.isoformat(), "raw_time": tick.time,
            "raw_time_msc": getattr(tick, "time_msc", None), "normalized_utc": normalized.isoformat(),
            "age_seconds": age, "bid": float(tick.bid), "ask": float(tick.ask)})
        if idx < 5:
            time.sleep(6)
    if len({s["raw_time_msc"] or s["raw_time"] for s in tick_samples}) < 2:
        raise PreflightError("EURUSD_TICK_NOT_ADVANCING_AT_STARTUP")
    if tick_samples[-1]["age_seconds"] > float(POLICY["tick_max_age_seconds"]):
        sample = tick_samples[-1]
        diagnostic = assess_quote(observed_at=datetime.fromisoformat(sample["acquired_at_utc"]),
            quote_timestamp=datetime.fromisoformat(sample["normalized_utc"]), bid=sample["bid"],
            ask=sample["ask"], max_age_seconds=float(POLICY["tick_max_age_seconds"]))
        _raise_quote_rejection(f"EURUSD_TICK_STALE_AT_STARTUP:{sample['age_seconds']:.6f}", diagnostic)

    decision = _utc_now()
    _, candles, raw_bars = _rate_arrays(mt5)
    from app.v3.strategy_families import evaluate_all
    from app.v3.strategy_inputs import build_strategy_input
    strategy_input = build_strategy_input([c for tf in TIMEFRAMES for c in candles[tf]], decision)
    features = _feature_inputs(strategy_input)
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf8"))
    required = {f["feature_name"] for f in manifest["features"] if f["status"] == "REQUIRED"}
    if len(required) != 17 or required - features.keys():
        raise PreflightError("FEATURE_MANIFEST_MEMBERSHIP_MISMATCH")
    if any(features[n] is None or features[n] == "UNAVAILABLE" for n in required):
        raise PreflightError("REQUIRED_EURUSD_FEATURE_UNAVAILABLE")
    bars: dict[str, Any] = {}
    for tf in TIMEFRAMES:
        complete = [b for b in raw_bars[tf]
                    if b["stamp"].timestamp + timedelta(seconds=TIMEFRAME_SECONDS[tf]) <= decision]
        if not complete:
            raise PreflightError(f"NO_COMPLETED_EURUSD_BAR:{tf}")
        latest = max(complete, key=lambda b: b["stamp"].timestamp)
        close = latest["stamp"].timestamp + timedelta(seconds=TIMEFRAME_SECONDS[tf])
        age = (decision - close).total_seconds()
        if age < 0 or age > 2 * TIMEFRAME_SECONDS[tf]:
            raise PreflightError(f"STALE_EURUSD_BAR:{tf}:{age:.3f}")
        bars[tf] = {"status": "PASS", "count": len(complete), "latest_open_utc": latest["stamp"].timestamp.isoformat(),
                    "completion_utc": close.isoformat(), "age_seconds": age}
    clock = clock_evidence()
    tick, final_acquired, normalized = _fresh_tick(mt5)
    age = (final_acquired - normalized).total_seconds()
    if age < 0 or age > 5 or not (0 < float(tick.bid) <= float(tick.ask)):
        raise PreflightError(f"EURUSD_FINAL_TICK_FAIL:{age:.6f}")
    latest_m1 = max(b["stamp"].timestamp for b in raw_bars["M1"]
                    if b["stamp"].timestamp + timedelta(minutes=1) <= decision)
    return {"system_clock": "PASS", "clock_evidence": clock, "mt5_connection": "PASS",
        "account_server": account.server, "eurusd_tick": "PASS", "tick_samples": tick_samples,
        "latest_tick_age_seconds": age, "timeframes": bars, "feature_manifest": "PASS",
        "feature_manifest_version": manifest["manifest_version"], "required_feature_count": len(required),
        "macro": "NOT_REQUIRED_BY_FROZEN_EURUSD_DECISION",
        "cross_asset": "NOT_REQUIRED_BY_FROZEN_EURUSD_DECISION", "xauusd": "UNUSED",
        "strategy_outputs": {k: {"setup": v.setup, "side": v.side} for k, v in evaluate_all(strategy_input).items()},
        "no_lookahead_contract": "PASS", "latest_completed_m1_open_utc": latest_m1.isoformat()}


def _pid_exists(pid: int) -> bool | None:
    """True/False when process liveness is known; None when access is ambiguous."""
    if pid <= 0:
        return False
    try:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        kernel.GetExitCodeProcess.restype = ctypes.c_int
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.CloseHandle.restype = ctypes.c_int
        handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            error = ctypes.get_last_error()
            return False if error == 87 else None  # invalid PID; access denied is unknown
        try:
            code = ctypes.c_ulong()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                return None
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel.CloseHandle(handle)
    except Exception:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return None


@serialized_acquisition
def _acquire_collector_lock() -> dict[str, Any]:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if STATE_PATH.exists():
        try:
            prior_state = json.loads(read_text_shared(STATE_PATH))
            if prior_state.get("status") in ("STARTING", "RUNNING", "CLOCK_UNCERTIFIED_TRANSIENT") and _pid_exists(int(prior_state.get("pid", 0))) is not False:
                raise PreflightError("V35_STATE_REPORTS_LIVE_OWNER_OR_PID_AMBIGUOUS")
        except PreflightError:
            raise
        except Exception as exc:
            raise PreflightError("V35_STATE_UNREADABLE_FAIL_CLOSED") from exc
    owner = {"pid": os.getpid(), "worker_id": str(uuid.uuid4()),
             "worker_identity": "v3.5-eurusd-collector", "process_startup_at_utc": _utc_now().isoformat(),
             "acquired_at_utc": _utc_now().isoformat(), "heartbeat_at_utc": _utc_now().isoformat()}
    try:
        fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        original = read_text_shared(LOCK_PATH).encode("utf8")
        try:
            locked = json.loads(original)
            pid = int(locked.get("pid", 0))
            alive = _pid_exists(pid)
            heartbeat = datetime.fromisoformat(locked.get("heartbeat_at_utc", locked.get("acquired_at_utc", "")))
            stale = (_utc_now() - heartbeat.astimezone(UTC)).total_seconds() > 90
        except Exception as exc:
            raise PreflightError("V35_LOCK_UNREADABLE_FAIL_CLOSED") from exc
        state_live = False
        if STATE_PATH.exists():
            try:
                state = json.loads(read_text_shared(STATE_PATH))
                state_live = state.get("status") in ("STARTING", "RUNNING", "CLOCK_UNCERTIFIED_TRANSIENT") and _pid_exists(int(state.get("pid", 0))) is not False
            except Exception:
                state_live = True
        if alive is not False or state_live or not stale:
            raise PreflightError("V35_COLLECTOR_LOCK_OWNED_OR_NOT_PROVEN_STALE")
        if read_text_shared(LOCK_PATH).encode("utf8") != original:
            raise PreflightError("V35_LOCK_CHANGED_DURING_RECOVERY")
        LOCK_PATH.unlink()
        try:
            fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise PreflightError("V35_COLLECTOR_LOCK_RECOVERY_RACE") from exc
    with os.fdopen(fd, "w", encoding="utf8") as handle:
        handle.write(json.dumps(owner, sort_keys=True))
        handle.flush()
        os.fsync(handle.fileno())
    # Keep abandoned state/lock temp files as recovery evidence. They are not
    # part of active ownership and must not be silently pruned at startup.
    return owner


def _write_heartbeat(owner: dict[str, Any], **health: Any) -> None:
    owner["heartbeat_at_utc"] = _utc_now().isoformat()
    assert_owner(LOCK_PATH, owner)
    write_json_atomic(LOCK_PATH, owner)
    state = {**owner, **health, "continuous_collection": True, "fit_model": 0, "order_send": 0}
    _write_state("RUNNING", **state)


def _install_sqlite_heartbeat(db: Any, owner: dict[str, Any]) -> list[Exception]:
    """Keep long integrity queries observable without querying SQLite recursively."""
    failure: list[Exception] = []
    last = time.monotonic()

    def progress() -> int:
        nonlocal last
        if time.monotonic() - last < 5:
            return 0
        try:
            assert_owner(LOCK_PATH, owner)
            state = json.loads(read_text_shared(STATE_PATH))
            if state.get("status") != "RUNNING":
                return 0
            stamp = _utc_now().isoformat()
            owner["heartbeat_at_utc"] = stamp
            write_json_atomic(LOCK_PATH, owner)
            write_json_atomic(STATE_PATH, {**state, "heartbeat_at_utc": stamp, "updated_at_utc": stamp})
            last = time.monotonic()
            return 0
        except Exception as exc:
            failure.append(exc)
            return 1  # Abort the query; outer worker failure retains the original error.

    db.set_progress_handler(progress, 10000)
    return failure


def _process_memory() -> dict[str, int | None]:
    class Counters(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
            ("PrivateUsage", ctypes.c_size_t)]
    try:
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
        psapi.GetProcessMemoryInfo.restype = ctypes.c_int
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return {"working_set_bytes": None, "peak_working_set_bytes": None}
        return {"working_set_bytes": int(counters.WorkingSetSize),
                "peak_working_set_bytes": int(counters.PeakWorkingSetSize)}
    except Exception:
        return {"working_set_bytes": None, "peak_working_set_bytes": None}


def _database_size(path: Path = STORE) -> int:
    return sum(candidate.stat().st_size for candidate in
               (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")) if candidate.exists())


def _verify_cycle_storage(db: Any, observation_id: str) -> dict[str, Any]:
    row = db.execute("SELECT snapshot_sha256,payload FROM observations WHERE observation_id=?", (observation_id,)).fetchone()
    if row is None:
        raise PreflightError("V35_OBSERVATION_NOT_DURABLY_VISIBLE")
    snapshot_hash, text = row
    observation = json.loads(text)
    snapshot = observation["snapshot"]
    if snapshot_hash != digest({k:v for k,v in snapshot.items() if k != "snapshot_sha256"}) or snapshot["snapshot_sha256"] != snapshot_hash:
        raise PreflightError("V35_STORED_SNAPSHOT_HASH_FAILURE")
    if observation.get("outcome_ref") is not None or observation.get("outcome_status") != "UNKNOWN_AT_DECISION":
        raise PreflightError("V35_OUTCOME_ISOLATION_FAILURE")
    visited: set[str] = set()
    for name, evidence_id in snapshot["feature_provenance_refs"].items():
        found = db.execute("SELECT payload_sha256,payload FROM provenance WHERE evidence_id=?", (evidence_id,)).fetchone()
        if found is None:
            raise PreflightError("V35_DANGLING_PROVENANCE_REFERENCE:" + name)
        stored_hash, evidence_text = found
        evidence = json.loads(evidence_text)
        if stored_hash != digest(evidence) or evidence.get("evidence_id") != digest({k:v for k,v in evidence.items() if k != "evidence_id"}):
            raise PreflightError("V35_PROVENANCE_HASH_FAILURE:" + name)
        if evidence.get("provenance_status") != "CERTIFIED" or evidence.get("decision_time_utc") != snapshot["decision_time_utc"]:
            raise PreflightError("V35_PROVENANCE_CONTRACT_FAILURE:" + name)
        if datetime.fromisoformat(evidence["information_available_at_utc"]) > datetime.fromisoformat(snapshot["decision_time_utc"]):
            raise PreflightError("V35_FUTURE_INFORMATION:" + name)
        if digest(snapshot["feature_values"].get(name)) != evidence.get("value_sha256"):
            raise PreflightError("V35_STORED_FEATURE_VALUE_FAILURE:" + name)
        pending = list(evidence.get("dependencies", []))
        if name != "current_tick_quote":
            if len(pending) != len(set(pending)):
                raise PreflightError("V35_DUPLICATE_FEATURE_ANCESTOR:" + name)
            actual_counts: dict[str, int] = {}
            for ancestor_id in pending:
                ancestor_row = db.execute("SELECT payload FROM provenance WHERE evidence_id=?", (ancestor_id,)).fetchone()
                if ancestor_row is None:
                    raise PreflightError("V35_DANGLING_ANCESTOR_PROVENANCE:" + name)
                timeframe = json.loads(ancestor_row[0]).get("timeframe")
                actual_counts[timeframe] = actual_counts.get(timeframe, 0) + 1
            if actual_counts != _required_lineage_counts(name):
                raise PreflightError("V35_INCOMPLETE_FEATURE_LINEAGE:" + name)
        while pending:
            ancestor_id = pending.pop()
            if ancestor_id in visited:
                continue
            visited.add(ancestor_id)
            ancestor_row = db.execute("SELECT payload_sha256,payload FROM provenance WHERE evidence_id=?", (ancestor_id,)).fetchone()
            if ancestor_row is None:
                raise PreflightError("V35_DANGLING_ANCESTOR_PROVENANCE:" + name)
            ancestor_hash, ancestor_text = ancestor_row
            ancestor = json.loads(ancestor_text)
            if ancestor_hash != digest(ancestor) or ancestor.get("evidence_id") != digest({k:v for k,v in ancestor.items() if k != "evidence_id"}):
                raise PreflightError("V35_ANCESTOR_HASH_FAILURE:" + name)
            if ancestor.get("provenance_status") != "CERTIFIED":
                raise PreflightError("V35_UNSAFE_ANCESTOR:" + name)
            if datetime.fromisoformat(ancestor["information_available_at_utc"]) > datetime.fromisoformat(snapshot["decision_time_utc"]):
                raise PreflightError("V35_FUTURE_ANCESTOR:" + name)
            if ancestor.get("kind") == "bar" and datetime.fromisoformat(ancestor["bar_close_utc"]) > datetime.fromisoformat(snapshot["decision_time_utc"]):
                raise PreflightError("V35_INCOMPLETE_ANCESTOR_BAR:" + name)
            pending.extend(ancestor.get("dependencies", []))
    return {"snapshot_hash": snapshot_hash, "provenance_ref_count": len(snapshot["feature_provenance_refs"]),
            "lineage_evidence_count": len(visited),
            "lineage_complete": True, "no_lookahead": True, "outcome_unknown": True}


def _latest_completed_m1(mt5: Any, now: datetime | None = None) -> datetime:
    now = now or _utc_now()
    offset = _broker_offset_seconds()
    rates = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M1, 1, 4)
    if rates is None:
        raise PreflightError("EURUSD_M1_HISTORY_UNAVAILABLE")
    completed = [normalize_mt5_epoch(float(rate["time"]), offset_seconds=offset, source="EURUSD_M1").timestamp
                 for rate in rates]
    completed = [opened for opened in completed if opened + timedelta(minutes=1) <= now]
    if not completed:
        raise PreflightError("EURUSD_NO_COMPLETED_M1_AT_STARTUP")
    return max(completed)


def run_continuous_collection(*, max_cycles_for_test: int | None = None) -> dict[str, Any] | None:
    """Start one prospective collector; production mode remains active indefinitely."""
    os.environ.update({"APP_ENV":"paper", "MT5_READ_ONLY":"true", "TRADING_EXECUTION_ENABLED":"false",
        "DEMO_EXECUTION_ENABLED":"false", "REAL_EXECUTION_ENABLED":"false", "FIT_MODEL":"0", "ORDER_SEND":"0"})
    _broker_offset_seconds()
    owner = _acquire_collector_lock()
    mt5 = db = None
    sqlite_heartbeat_failure: list[Exception] = []
    new_cycles = validation = idempotent_replays = 0
    errors: list[str] = []
    started = _utc_now().isoformat()
    heartbeat_token = None
    terminal_status = "STOPPED"
    terminal_reason: str | None = None
    terminal_fields: dict[str, Any] = {}
    finalization_errors: list[dict[str, str]] = []
    result: dict[str, Any] | None = None
    try:
        _write_state("STARTING", **owner, collector_id="v3.5-eurusd-continuous-r1", startup_at_utc=started,
                     continuous_collection=True, observations_before_start=None)
        storage_health.require(STORE)
        heartbeat_token = _clock_heartbeat.set(lambda: _lifecycle_state(owner))
        try:
            clock = clock_evidence()
        except ClockCertificationError as exc:
            clock = _recover_collector_clock(owner, exc, resume_status="STARTING")
        if not clock["clock_safe"]:
            raise PreflightError("CLOCK_UNSAFE_AT_COLLECTOR_START")
        import MetaTrader5 as mt5_module
        mt5 = mt5_module
        connection = initialize_checked(mt5)
        if connection.get("server") != "ICMarketsSC-Demo":
            raise PreflightError("UNEXPECTED_MT5_SERVER")
        try:
            preflight = _system_preflight(mt5)
        except ClockCertificationError as exc:
            _recover_collector_clock(owner, exc, resume_status="STARTING")
            preflight = _system_preflight(mt5)
        db = connect(STORE)
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise PreflightError("V35_DATABASE_INTEGRITY_FAILURE_AT_STARTUP")
        if db.execute("PRAGMA foreign_key_check").fetchall():
            raise PreflightError("V35_DATABASE_FOREIGN_KEY_FAILURE_AT_STARTUP")
        if db.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
            raise PreflightError("V35_DATABASE_WAL_REQUIRED")
        trigger_count = db.execute("SELECT count(*) FROM sqlite_master WHERE type='trigger' AND name IN "
            "('provenance_no_update','provenance_no_delete','observations_no_update','observations_no_delete','outcomes_no_update','outcomes_no_delete')").fetchone()[0]
        if trigger_count != 6:
            raise PreflightError("V35_APPEND_ONLY_TRIGGERS_INCOMPLETE")
        before = int(db.execute("SELECT count(*) FROM observations").fetchone()[0])
        anchor = _latest_completed_m1(mt5)
        _write_heartbeat(owner, lifecycle_status="HEALTHY", collector_id="v3.5-eurusd-continuous-r1", startup_at_utc=started,
            observations_before_start=before, observations_count=before, data_training_eligible_count=int(db.execute("SELECT coalesce(sum(eligible),0) FROM observations").fetchone()[0]),
            ineligible_count=int(db.execute("SELECT count(*)-coalesce(sum(eligible),0) FROM observations").fetchone()[0]),
            no_setup_count=int(db.execute("SELECT count(*) FROM observations WHERE json_extract(payload,'$.direction')='NO_SETUP'").fetchone()[0]),
            buy_count=int(db.execute("SELECT count(*) FROM observations WHERE json_extract(payload,'$.direction')='BUY_SETUP'").fetchone()[0]),
            sell_count=int(db.execute("SELECT count(*) FROM observations WHERE json_extract(payload,'$.direction')='SELL_SETUP'").fetchone()[0]),
            excluded_count=int(db.execute("SELECT count(*) FROM observations WHERE json_extract(payload,'$.direction')='EXCLUDED'").fetchone()[0]),
            latest_completed_m1=preflight["latest_completed_m1_open_utc"], last_completed_m1=None,
            last_error=None, clock_status="PASS", mt5_status="PASS", startup_preflight=preflight,
            validation_cycles_observed=0, idempotent_replays=0, memory=_process_memory(),
            database_size_bytes=_database_size(), provenance_count=int(db.execute("SELECT count(*) FROM provenance").fetchone()[0]))

        sqlite_heartbeat_failure = _install_sqlite_heartbeat(db, owner)

        def heartbeat() -> None:
            nonlocal last_heartbeat
            now_mono = time.monotonic()
            if now_mono - last_heartbeat >= 5:
                current_count = int(db.execute("SELECT count(*) FROM observations").fetchone()[0])
                _write_heartbeat(owner, collector_id="v3.5-eurusd-continuous-r1", startup_at_utc=started,
                    observations_count=current_count,
                    data_training_eligible_count=int(db.execute("SELECT coalesce(sum(eligible),0) FROM observations").fetchone()[0]),
                    ineligible_count=int(db.execute("SELECT count(*)-coalesce(sum(eligible),0) FROM observations").fetchone()[0]),
                    no_setup_count=int(db.execute("SELECT count(*) FROM observations WHERE json_extract(payload,'$.direction')='NO_SETUP'").fetchone()[0]),
                    buy_count=int(db.execute("SELECT count(*) FROM observations WHERE json_extract(payload,'$.direction')='BUY_SETUP'").fetchone()[0]),
                    sell_count=int(db.execute("SELECT count(*) FROM observations WHERE json_extract(payload,'$.direction')='SELL_SETUP'").fetchone()[0]),
                    excluded_count=int(db.execute("SELECT count(*) FROM observations WHERE json_extract(payload,'$.direction')='EXCLUDED'").fetchone()[0]),
                    last_completed_m1=last_completed, validation_cycles_observed=validation,
                    idempotent_replays=idempotent_replays, last_error=errors[-1] if errors else None,
                    lifecycle_status="HEALTHY", clock_status="PASS", mt5_status="PASS", memory=_process_memory(), database_size_bytes=_database_size(),
                    provenance_count=int(db.execute("SELECT count(*) FROM provenance").fetchone()[0]))
                last_heartbeat = now_mono

        last_completed: str | None = None
        last_heartbeat = time.monotonic()
        while True:
            try:
                storage_health.require(STORE)
            except storage_health.StorageHealthError:
                db.rollback()
                storage_health.wait_until_healthy(STORE, lambda metrics: _lifecycle_state(owner,
                    status="STORAGE_UNCERTIFIED", lifecycle_status="PAUSED", continuous_collection=False,
                    storage_health=metrics, last_error=metrics["reason"]))
                if db.execute("PRAGMA quick_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchall():
                    raise PreflightError("STORAGE_RECOVERY_DATABASE_PREFLIGHT_FAILED")
                clock_evidence()
                anchor = _latest_completed_m1(mt5)
                _lifecycle_state(owner,status="RUNNING",lifecycle_status="HEALTHY",continuous_collection=True,
                    storage_health=storage_health.sample(STORE),last_error=None,missed_bar_backfill=False,
                    resume_after_m1_open_utc=anchor.isoformat())
                continue
            try:
                m1_open, m1_info = _wait_completed_m1(mt5, anchor, timeout_seconds=86400,
                    poll_seconds=1.0, heartbeat_callback=heartbeat)
            except PreflightError as exc:
                if str(exc) != "NON_CONSECUTIVE_COMPLETED_M1":
                    raise
                # A slow audit or resume may miss bars. Keep the strict completed-bar
                # check, certify a fresh clock, and wait prospectively from a new anchor.
                _lifecycle_state(owner, status="CLOCK_UNCERTIFIED_TRANSIENT",
                    lifecycle_status="CLOCK_UNCERTIFIED_TRANSIENT", clock_status="UNKNOWN",
                    last_error="PROSPECTIVE_M1_GAP_RECERTIFICATION", continuous_collection=False)
                try:
                    clock_evidence()
                except ClockCertificationError as failure:
                    _recover_collector_clock(owner, failure)
                anchor = _latest_completed_m1(mt5)
                log_event(ROOT, {"event":"MISSED_M1_GAP_SKIPPED", "resume_after_m1_open_utc":anchor.isoformat(),
                                 "missed_bar_backfill":False})
                _lifecycle_state(owner, status="RUNNING", lifecycle_status="HEALTHY", clock_status="PASS",
                                 last_error=None, continuous_collection=True,
                                 resume_after_m1_open_utc=anchor.isoformat(), missed_bar_backfill=False)
                continue
            heartbeat()
            try:
                clock_before = clock_evidence()
                if not clock_before["clock_safe"]:
                    raise PreflightError("CLOCK_UNSAFE_BEFORE_DECISION")
                heartbeat()
                cycle_start = time.perf_counter()
                cycle = None
                for attempt in range(3):
                    try:
                        cycle = _collect_cycle(mt5, db, m1_open, m1_info, new_cycles + 1,
                                               continuous=True, heartbeat_callback=heartbeat)
                        break
                    except PreflightError as exc:
                        retryable = str(exc).startswith(("INSUFFICIENT_COMPLETED_BARS", "EURUSD_TICK_UNAVAILABLE"))
                        if not retryable or attempt == 2:
                            raise
                        time.sleep(0.5 * (attempt + 1))
            except storage_health.StorageHealthError:
                db.rollback()
                # The next iteration pauses and resumes from a fresh M1 anchor.
                continue
            except ClockCertificationError as exc:
                _recover_collector_clock(owner, exc)
                anchor = _latest_completed_m1(mt5)
                _lifecycle_state(owner, status="RUNNING", lifecycle_status="HEALTHY", last_error=None,
                                 clock_status="PASS", resume_after_m1_open_utc=anchor.isoformat(),
                                 missed_bar_backfill=False)
                continue
            if cycle is None:
                raise PreflightError("V35_CYCLE_RETRY_EXHAUSTED")
            anchor = m1_open
            if cycle.get("idempotent_replay"):
                idempotent_replays += 1
                cycle_audit = {"idempotent_replay": True}
            else:
                cycle_audit = _verify_cycle_storage(db, cycle["observation_id"])
                new_cycles += 1
                validation += 1
                # Optional post-commit shadow sidecar; never a decision dependency.
                try:
                    from app.v3_5.context_recorder import observe_committed
                    observe_committed(cycle)
                except Exception as shadow_error:
                    print(json.dumps({"event":"SHADOW_CONTEXT_FAILURE", "error":str(shadow_error)}),flush=True)
            gc.collect()
            last_completed = m1_info["bar_close_utc"]
            heartbeat()
            db.execute("PRAGMA wal_checkpoint(PASSIVE)")
            if db.execute("PRAGMA foreign_key_check").fetchall():
                raise PreflightError("V35_DATABASE_FOREIGN_KEY_FAILURE_AFTER_CYCLE")
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise PreflightError("V35_DATABASE_INTEGRITY_FAILURE_AFTER_CYCLE")
            heartbeat()
            count = int(db.execute("SELECT count(*) FROM observations").fetchone()[0])
            if count != before + new_cycles:
                raise PreflightError("V35_OBSERVATION_COUNT_NOT_MONOTONIC_BY_ONE")
            metrics = {"provider_latency_seconds": cycle.get("provider_latency_seconds"),
                "provenance_write_latency_seconds": cycle.get("provenance_write_seconds"),
                "cycle_duration_seconds": time.perf_counter() - cycle_start,
                "data_training_eligible": cycle.get("data_training_eligible"),
                "direction": cycle.get("direction"), "snapshot_hash": cycle.get("snapshot_sha256"),
                "provenance_ref_count": cycle_audit.get("provenance_ref_count"),
                "working_set_bytes": _process_memory()["working_set_bytes"],
                "peak_working_set_bytes": _process_memory()["peak_working_set_bytes"],
                "database_size_bytes": _database_size(),
                "provenance_count": int(db.execute("SELECT count(*) FROM provenance").fetchone()[0]),
                "outcome_count": int(db.execute("SELECT count(*) FROM outcomes").fetchone()[0]),
                "no_lookahead": cycle_audit.get("no_lookahead", True),
                "last_cycle": cycle}
            _write_heartbeat(owner, collector_id="v3.5-eurusd-continuous-r1", startup_at_utc=started,
                observations_before_start=before, observations_count=count,
                data_training_eligible_count=int(db.execute("SELECT coalesce(sum(eligible),0) FROM observations").fetchone()[0]),
                ineligible_count=int(db.execute("SELECT count(*)-coalesce(sum(eligible),0) FROM observations").fetchone()[0]),
                no_setup_count=int(db.execute("SELECT count(*) FROM observations WHERE json_extract(payload,'$.direction')='NO_SETUP'").fetchone()[0]),
                buy_count=int(db.execute("SELECT count(*) FROM observations WHERE json_extract(payload,'$.direction')='BUY_SETUP'").fetchone()[0]),
                sell_count=int(db.execute("SELECT count(*) FROM observations WHERE json_extract(payload,'$.direction')='SELL_SETUP'").fetchone()[0]),
                excluded_count=int(db.execute("SELECT count(*) FROM observations WHERE json_extract(payload,'$.direction')='EXCLUDED'").fetchone()[0]),
                last_completed_m1=last_completed, validation_cycles_observed=validation,
                idempotent_replays=idempotent_replays, last_error=errors[-1] if errors else None,
                clock_status="PASS", mt5_status="PASS", cycle_metrics=metrics,
                memory=_process_memory(), database_size_bytes=_database_size(),
                provenance_count=int(db.execute("SELECT count(*) FROM provenance").fetchone()[0]),
                lifecycle_status="HEALTHY", storage_integrity="PASS", append_only_triggers=trigger_count,
                safe_to_train=False, training_execution_authorized=False,
                fit_model=0, order_send=0, demo_execution=False, real_execution=False)
            if max_cycles_for_test is not None and validation >= max_cycles_for_test:
                terminal_reason = "TEST_CYCLE_LIMIT"
                terminal_fields.update(test_run=True)
                result = {"status": "STOPPED", "validation_cycles_observed": validation,
                          "observations_count": count}
                break
    except KeyboardInterrupt:
        terminal_reason = "OPERATOR_INTERRUPT"
        result = {"status": "STOPPED", "reason": terminal_reason,
                  "validation_cycles_observed": validation}
    except Exception as caught_error:
        failure = sqlite_heartbeat_failure[0] if sqlite_heartbeat_failure else caught_error
        terminal_status = ("STORAGE_UNCERTIFIED" if storage_health.is_storage_error(failure)
                           else "FAILED_PERSISTENT" if isinstance(failure, ClockHealthError | PersistentClockFailure)
                           else "FAILED")
        terminal_reason = f"{type(failure).__name__}:{failure}"
        terminal_fields.update(
            lifecycle_status=_failure_lifecycle_status(failure, storage_failure=storage_health.is_storage_error(failure)),
            storage_health=storage_health.sample(STORE), clock_failure=getattr(failure, "diagnostics", None),
            last_error=terminal_reason, clock_status="FAIL" if "CLOCK" in str(failure) else "UNKNOWN",
            mt5_status="FAIL" if "MT5" in str(failure) or "EURUSD" in str(failure) else "UNKNOWN",
            validation_cycles_observed=validation,
            observations_count=(int(db.execute("SELECT count(*) FROM observations").fetchone()[0]) if db else None),
        )
        raise failure from None
    finally:
        cleanup: list[tuple[str, Any]] = []
        if heartbeat_token is not None:
            cleanup.append(("reset_clock_heartbeat", lambda: _clock_heartbeat.reset(heartbeat_token)))
        if db is not None:
            cleanup.append(("close_cohort_database", db.close))
        if mt5 is not None:
            cleanup.append(("shutdown_mt5", lambda: shutdown(mt5)))

        def publish_terminal(cleanup_errors: list[dict[str, str]]) -> None:
            status = terminal_status
            reason = terminal_reason
            if cleanup_errors and status == "STOPPED":
                status = "FAILED"
                reason = "WORKER_CLEANUP_FAILED"
            lifecycle_errors = cleanup_errors
            if cleanup_errors and status == "FAILED" and reason == "WORKER_CLEANUP_FAILED":
                lifecycle_errors = cleanup_errors
            fields = {**terminal_fields, **owner, "collector_id": "v3.5-eurusd-continuous-r1",
                      "continuous_collection": False, "reason": reason,
                      "validation_cycles_observed": validation, "cleanup_errors": lifecycle_errors}
            if terminal_status in ("FAILED", "FAILED_PERSISTENT", "STORAGE_UNCERTIFIED"):
                fields["last_error"] = terminal_reason
            _write_state(status, preserve_failed_temp=True, **fields)
            if cleanup_errors:
                log_event(ROOT, {"event": "COLLECTOR_SHUTDOWN_CLEANUP_ERRORS", "worker_id": owner["worker_id"],
                                 "errors": cleanup_errors, "primary_reason": terminal_reason})

        finalization_errors = finalize_worker(owner=owner, lock_path=LOCK_PATH, cleanup=cleanup,
            publish_terminal=publish_terminal,
            record_event=lambda event: log_event(ROOT, event))
    if finalization_errors and result is not None:
        raise RuntimeError(f"V35_COLLECTOR_FINALIZATION_FAILED:{finalization_errors}")
    return result

