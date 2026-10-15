"""Fail-closed, allowlisted boundary between immutable research rows and labels.

This is a timing/data certification revision, not a strategy revision.
No fitting, calibration or execution is permitted by this module.
"""
from __future__ import annotations

import hashlib
import math
import os
from datetime import UTC, datetime, timedelta
from typing import Any

COLLECTOR_VERSION = "v3.4-complete-fx-intelligence-r1"
COLLECTOR_HASH = "c056fac9dca4cf331dc94c52ea3e46dc7e6257e9fd0009506fbf808ce795ee58"
NORMALIZATION = "v3.4-mt5-explicit-offset-r1"
POLICY_VERSION = "v3-outcome-r1-certified-time-r2"
# Freeze both temporal semantics and conservative coverage gates in the identity.
POLICY_CONTRACT = {
    "version": POLICY_VERSION, "base": "v3-outcome-r1", "rr": [1, 1.5, 2, 3, 5],
    "entry": "first valid tick strictly after explicit decision_timestamp",
    "clock": NORMALIZATION, "atr": "persisted completed M15 ATR14 * 1.5",
    "hold_seconds": 21600, "entry_grace_seconds": 60, "max_gap_seconds": 300,
    "timeout_grace_seconds": 60, "prices": "BUY ask entry/bid exit; SELL bid entry/ask exit",
    "realized_r": "observed exit quote displacement / stop distance; PRE_COMMISSION",
    "policy_r": "original fixed TP/SL R retained separately",
    "synthetic_fields": "excluded by positive allowlist; no training permitted",
}
import json
POLICY_HASH = hashlib.sha256(json.dumps(POLICY_CONTRACT, sort_keys=True).encode()).hexdigest()
SAFETY = {"APP_ENV": "paper", "MT5_READ_ONLY": "true", "TRADING_EXECUTION_ENABLED": "false",
          "DEMO_EXECUTION_ENABLED": "false", "REAL_EXECUTION_ENABLED": "false",
          "FIT_MODEL": "0", "ORDER_SEND": "0"}
EXCLUSIONS = {
    "v3_3_system_intelligence.rich_outcome_labels": "SYNTHETIC_FUTURE_PATH",
    "v3_3_system_intelligence.drift_monitoring": "SYNTHETIC_EXAMPLE_RETURNS",
    "v3_3_system_intelligence.future_training_targets": "SCHEMA_ONLY_NOT_LABELS",
    "v3_3_system_intelligence.uncertainty_foundation": "UNTRAINED_PLACEHOLDERS",
    "v3_3_system_intelligence.risk_intelligence": "UNAVAILABLE_PERFORMANCE_PLACEHOLDERS",
    "v3_3_system_intelligence.ablation_foundation": "RESEARCH_PLAN_NOT_MEASUREMENTS",
    "v3_3_system_intelligence.redundancy_diagnostics": "EXAMPLE_FEATURE_NAMES",
    "v3_4_forex_intelligence.drift": "SYNTHETIC_FEATURE_DRIFT_EXAMPLE",
    "v3_4_forex_intelligence.calibration": "FUTURE_PLACEHOLDERS",
    "v3_4_forex_intelligence.volatility_forecast": "LABEL_SCHEMA_ONLY",
    "v3_4_forex_intelligence.meta_labeling": "TARGET_SCHEMA_ONLY",
    "v3_4_forex_intelligence.strategy_attribution": "HARDCODED_EXAMPLE_ATTRIBUTION",
    "v3_4_forex_intelligence.currency_exposure": "HARDCODED_LONG_EXPOSURE_EXAMPLE",
    "v3_4_forex_intelligence.providers": "NOT_CERTIFIED_DECISION_TIME_FEATURES",
}


def require_safety() -> None:
    bad = [k for k, v in SAFETY.items() if os.getenv(k, "").lower() != v]
    if bad:
        raise RuntimeError("SAFETY_FLAGS_REQUIRED:" + ",".join(bad))


def utc(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() != timedelta(0):
        raise ValueError("EXPLICIT_UTC_REQUIRED")
    return result.astimezone(UTC)


def is_v34(row: dict) -> bool:
    return row.get("collector_policy_version") == COLLECTOR_VERSION and row.get("collector_policy_hash") == COLLECTOR_HASH


def eligible(row: dict) -> tuple[str, list[str]] | None:
    for key, version in [("strategy_policy", "v3-strategy-family-r1"), ("strategy_input_policy", "v3-strategy-input-r1")]:
        if row.get(key + "_version") != version:
            return None
    active = [(k, v.get("side")) for k, v in row.get("strategy_families", {}).items()
              if v.get("setup") is True and v.get("side") in {"BUY", "SELL"}]
    sides = {v for _, v in active}
    return (next(iter(sides)), sorted(k for k, _ in active)) if len(sides) == 1 else None


def project_setup(row: dict[str, Any]) -> dict | None:
    """Only this positive allowlist can enter the new resolver or its reports."""
    selection = eligible(row)
    if not is_v34(row) or selection is None:
        return None
    side, families = selection
    inp = row.get("strategy_input_payload", {})
    result = {"observation_id": row["observation_id"], "symbol": row.get("symbol"),
              "bar_timestamp": row.get("timestamp"), "decision_timestamp": row.get("decision_timestamp"),
              "direction": side, "families": families, "regime": inp.get("market_regime", "UNKNOWN"),
              "session": inp.get("session", {}).get("session", "UNKNOWN"),
              "volatility": inp.get("contexts", {}).get("M1", {}).get("volatility_state", "UNKNOWN"),
              "timing_status": "LEGACY_UNCERTIFIED", "timing_reason": "EXPLICIT_NORMALIZATION_MISSING"}
    if row.get("time_normalization_version") != NORMALIZATION or not row.get("decision_timestamp"):
        return result
    try:
        if row.get("symbol") != "EURUSD":
            raise ValueError("UNSUPPORTED_SYMBOL")
        bar, decision = utc(row["timestamp"]), utc(row["decision_timestamp"])
        offset = row["mt5_server_utc_offset_seconds"]
        if type(offset) is not int or abs(offset) > 50400:
            raise ValueError("INVALID_OFFSET")
        if decision != bar + timedelta(minutes=1):
            raise ValueError("DECISION_BAR_MISMATCH")
        for raw, normalized in [("raw_m1_timestamp", "timestamp"), ("raw_tick_timestamp", "tick_timestamp")]:
            if utc(row[raw]) - timedelta(seconds=offset) != utc(row[normalized]):
                raise ValueError("RAW_UTC_MISMATCH")
        if abs(float(row["raw_m1_epoch_seconds"]) - utc(row["raw_m1_timestamp"]).timestamp()) > .001:
            raise ValueError("RAW_BAR_EPOCH_MISMATCH")
        if abs(float(row["raw_tick_epoch_seconds"]) - utc(row["raw_tick_timestamp"]).timestamp()) > .001:
            raise ValueError("RAW_TICK_EPOCH_MISMATCH")
        if not 0 <= (decision - utc(row["tick_timestamp"])).total_seconds() <= 5:
            raise ValueError("FUTURE_OR_STALE_FEATURE_TICK")
        if not (0 < float(row["bid"]) <= float(row["ask"]) and math.isfinite(float(row["ask"]))):
            raise ValueError("INVALID_QUOTE")
        for key, version in [("strategy_policy", "v3-strategy-family-r1"), ("strategy_input_policy", "v3-strategy-input-r1")]:
            if row.get(key + "_hash") != hashlib.sha256(version.encode()).hexdigest():
                raise ValueError("STRATEGY_POLICY_MISMATCH")
        if utc(inp["decision_timestamp"]) > decision:
            raise ValueError("FUTURE_STRATEGY_INPUT")
        durations = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600, "H6": 21600, "H12": 43200}
        for tf, ctx in inp.get("contexts", {}).items():
            if ctx.get("available"):
                if utc(ctx["source_candle_timestamp"]) + timedelta(seconds=durations[tf]) > decision:
                    raise ValueError("INCOMPLETE_FEATURE_CANDLE")
                if utc(ctx["feature_timestamp"]) > decision:
                    raise ValueError("FUTURE_FEATURE_TIMESTAMP")
        atr = inp["contexts"]["M15"]
        value = float(atr["atr14"])
        if not math.isfinite(value) or value <= 0 or utc(atr["source_candle_timestamp"]) + timedelta(minutes=15) > decision:
            raise ValueError("INVALID_ATR")
        result.update(timing_status="CERTIFIED", timing_reason=None, offset_seconds=offset,
                      atr_value=value, atr_timestamp=atr["source_candle_timestamp"], stop_distance=value * 1.5)
    except (KeyError, ValueError, TypeError, OverflowError) as exc:
        result.update(timing_status="TIMING_UNCERTIFIABLE", timing_reason=str(exc))
    return result


def contract_id(observation_id: str, rr: float) -> str:
    return hashlib.sha256(f"{observation_id}|{POLICY_HASH}|{rr:g}".encode()).hexdigest()


def require_certified_outcome(row: dict) -> None:
    if (row.get("outcome_policy_hash") != POLICY_HASH or row.get("certification") != "CERTIFIED"
            or row.get("outcome_id") != contract_id(row.get("observation_id", ""), row.get("rr", 0))):
        raise ValueError("UNTRUSTED_LABEL_SOURCE")
    if not utc(row["entry_timestamp"]) > utc(row["decision_timestamp"]):
        raise ValueError("ENTRY_NOT_FUTURE")
    if not utc(row["exit_timestamp"]) > utc(row["entry_timestamp"]):
        raise ValueError("EXIT_NOT_AFTER_ENTRY")


def training_targets(*_args: Any, **_kwargs: Any) -> None:
    raise RuntimeError("TRAINING_DISABLED_PENDING_ALL_READINESS_GATES")
