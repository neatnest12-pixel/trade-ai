from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from collections.abc import Iterator

from app.v3.outcomes import (
    MAX_HOLD,
    RR_MATRIX,
    V3_OUTCOME_POLICY_HASH,
    V3_OUTCOME_POLICY_VERSION,
    OutcomeCandle,
    OutcomeTick,
    atr_stop_contract,
    first_executable_entry,
    stop_and_target,
)

STRATEGY_POLICY_VERSION = "v3-strategy-family-r1"
STRATEGY_INPUT_POLICY_VERSION = "v3-strategy-input-r1"
TARGET_RESOLVED_CLEAN_SETUPS = 100


@dataclass(frozen=True)
class CleanSetup:
    observation: dict[str, Any]
    direction: str
    families: tuple[str, ...]


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    if not path.exists():
        return
    with path.open(encoding="utf8") as handle:
        for line in handle:
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise RuntimeError("NON_OBJECT_OUTCOME_ROW")
            yield payload


def clean_setups(observations: list[dict[str, Any]]) -> list[CleanSetup]:
    setups: list[CleanSetup] = []
    for row in observations:
        if row.get("strategy_policy_version") != STRATEGY_POLICY_VERSION:
            continue
        if row.get("strategy_input_policy_version") != STRATEGY_INPUT_POLICY_VERSION:
            continue
        active: list[tuple[str, str]] = []
        for family, payload in (row.get("strategy_families") or {}).items():
            if isinstance(payload, dict) and payload.get("setup") is True and payload.get("side") in {"BUY", "SELL"}:
                active.append((family, payload["side"]))
        sides = {side for _, side in active}
        if sides == {"BUY"} or sides == {"SELL"}:
            setups.append(CleanSetup(row, next(iter(sides)), tuple(family for family, _ in active)))
    return sorted(setups, key=lambda setup: setup.observation.get("timestamp", ""))


def outcome_key(observation_id: str, rr: float) -> str:
    return hashlib.sha256(f"{observation_id}|{V3_OUTCOME_POLICY_HASH}|{rr}".encode()).hexdigest()


def resolved_setup_count(outcomes: list[dict[str, Any]]) -> int:
    return len({row["observation_id"] for row in outcomes if row.get("status") == "RESOLVED"})


def resolve_setup(
    setup: CleanSetup,
    rr: float,
    ticks: list[OutcomeTick],
) -> dict[str, Any]:
    observation = setup.observation
    if str(observation.get("collector_policy_version", "")).startswith("v3.4"):
        raise RuntimeError("V34_REQUIRES_CERTIFIED_DECISION_TIME_PIPELINE")
    decision_timestamp = datetime.fromisoformat(observation["timestamp"])
    observation_id = observation["observation_id"]
    key = outcome_key(observation_id, rr)
    entry = first_executable_entry(decision_timestamp, setup.direction, ticks)
    base = {
        "outcome_id": key,
        "observation_id": observation_id,
        "direction": setup.direction,
        "families": list(setup.families),
        "signal_timestamp": observation["timestamp"],
        "rr": rr,
        "outcome_policy_version": V3_OUTCOME_POLICY_VERSION,
        "outcome_policy_hash": V3_OUTCOME_POLICY_HASH,
        "commission_status": "UNAVAILABLE",
    }
    if entry is None:
        return {**base, "status": "UNRESOLVED", "exit_reason": "ENTRY_UNAVAILABLE"}
    candles = _m15_candles_from_observation(observation)
    stop_contract = atr_stop_contract(decision_timestamp, candles)
    if stop_contract is None:
        return {
            **base,
            "status": "UNRESOLVED",
            "exit_reason": "ATR_UNAVAILABLE",
            "entry_timestamp": entry.timestamp.isoformat(),
            "entry_price": entry.price,
            "entry_bid": entry.bid,
            "entry_ask": entry.ask,
            "entry_spread": entry.spread,
            "entry_provenance": entry.provenance,
        }
    stop, target = stop_and_target(entry.price, setup.direction, stop_contract.stop_distance, rr)
    max_hold_at = entry.timestamp + MAX_HOLD
    enriched = {
        **base,
        "status": "UNRESOLVED",
        "entry_timestamp": entry.timestamp.isoformat(),
        "entry_price": entry.price,
        "entry_bid": entry.bid,
        "entry_ask": entry.ask,
        "entry_spread": entry.spread,
        "entry_provenance": entry.provenance,
        "atr_timeframe": stop_contract.atr_timeframe,
        "atr_period": stop_contract.atr_period,
        "atr_value": stop_contract.atr_value,
        "atr_timestamp": stop_contract.atr_timestamp.isoformat(),
        "stop_multiplier": stop_contract.stop_multiplier,
        "stop_distance": stop_contract.stop_distance,
        "sl": stop,
        "tp": target,
        "max_hold_timestamp": max_hold_at.isoformat(),
    }
    mfe_price = entry.price
    mae_price = entry.price
    mfe_timestamp = entry.timestamp
    mae_timestamp = entry.timestamp
    for tick in sorted((tick for tick in ticks if tick.timestamp > entry.timestamp and tick.valid()), key=lambda item: item.timestamp):
        exit_price = tick.bid if setup.direction == "BUY" else tick.ask
        favorable_r = (
            (exit_price - entry.price) / stop_contract.stop_distance
            if setup.direction == "BUY"
            else (entry.price - exit_price) / stop_contract.stop_distance
        )
        adverse_r = -favorable_r
        current_mfe_r = (
            (mfe_price - entry.price) / stop_contract.stop_distance
            if setup.direction == "BUY"
            else (entry.price - mfe_price) / stop_contract.stop_distance
        )
        current_mae_r = (
            (entry.price - mae_price) / stop_contract.stop_distance
            if setup.direction == "BUY"
            else (mae_price - entry.price) / stop_contract.stop_distance
        )
        if favorable_r > current_mfe_r:
            mfe_price = exit_price
            mfe_timestamp = tick.timestamp
        if adverse_r > current_mae_r:
            mae_price = exit_price
            mae_timestamp = tick.timestamp
        if tick.timestamp <= max_hold_at:
            if setup.direction == "BUY" and exit_price <= stop:
                return _resolved_with_excursions(
                    enriched, tick.timestamp, exit_price, "SL_HIT", -1.0, entry, stop_contract.stop_distance, mfe_price, mfe_timestamp, mae_price, mae_timestamp
                )
            if setup.direction == "BUY" and exit_price >= target:
                return _resolved_with_excursions(
                    enriched, tick.timestamp, exit_price, "TP_HIT", rr, entry, stop_contract.stop_distance, mfe_price, mfe_timestamp, mae_price, mae_timestamp
                )
            if setup.direction == "SELL" and exit_price >= stop:
                return _resolved_with_excursions(
                    enriched, tick.timestamp, exit_price, "SL_HIT", -1.0, entry, stop_contract.stop_distance, mfe_price, mfe_timestamp, mae_price, mae_timestamp
                )
            if setup.direction == "SELL" and exit_price <= target:
                return _resolved_with_excursions(
                    enriched, tick.timestamp, exit_price, "TP_HIT", rr, entry, stop_contract.stop_distance, mfe_price, mfe_timestamp, mae_price, mae_timestamp
                )
        if tick.timestamp >= max_hold_at:
            realized = (
                (exit_price - entry.price) / stop_contract.stop_distance
                if setup.direction == "BUY"
                else (entry.price - exit_price) / stop_contract.stop_distance
            )
            return _resolved_with_excursions(
                enriched, tick.timestamp, exit_price, "TIME_EXIT", realized, entry, stop_contract.stop_distance, mfe_price, mfe_timestamp, mae_price, mae_timestamp
            )
    return {**enriched, "exit_reason": "IMMATURE_OR_NO_FUTURE_TICK"}


def append_new_outcomes(
    observations_path: Path,
    outcomes_path: Path,
    ticks: list[OutcomeTick],
    *,
    max_setups: int | None = None,
) -> dict[str, Any]:
    # Legacy fixture compatibility only. Production V3.4 uses certified_store.
    if observations_path.stat().st_size > 16 * 1024 * 1024:
        raise RuntimeError("LEGACY_RESOLVER_DISABLED_FOR_LARGE_DATASETS")
    observations = read_jsonl(observations_path)
    setups = clean_setups(observations)
    existing = {row.get("outcome_id") for row in read_jsonl(outcomes_path)}
    appended: list[dict[str, Any]] = []
    processed_setups = 0
    for setup in setups:
        missing_rr = [
            rr for rr in RR_MATRIX if outcome_key(setup.observation["observation_id"], rr) not in existing
        ]
        if not missing_rr:
            continue
        if max_setups is not None and processed_setups >= max_setups:
            break
        processed_setups += 1
        for rr in missing_rr:
            key = outcome_key(setup.observation["observation_id"], rr)
            outcome = resolve_setup(setup, rr, ticks)
            if outcome["status"] == "RESOLVED":
                appended.append(outcome)
                existing.add(key)
    if appended:
        with outcomes_path.open("a", encoding="utf8") as handle:
            for outcome in appended:
                handle.write(json.dumps(outcome, sort_keys=True) + "\n")
    return summarize(read_jsonl(observations_path), list(read_jsonl(outcomes_path)))


def summarize(observations: list[dict[str, Any]], outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    setups = clean_setups(observations)
    resolved_count = resolved_setup_count(outcomes)
    by_rr = Counter(str(row.get("rr")) for row in outcomes if row.get("status") == "RESOLVED")
    latest = max((row for row in outcomes if row.get("status") == "RESOLVED"), key=lambda row: row.get("exit_timestamp", ""), default=None)
    return {
        "clean_setups": len(setups),
        "resolved_clean_setups": resolved_count,
        "unresolved_clean_setups": max(0, len(setups) - resolved_count),
        "progress_to_100": resolved_count / TARGET_RESOLVED_CLEAN_SETUPS * 100,
        "outcomes_by_rr": dict(by_rr),
        "latest_resolved_observation": None if latest is None else latest.get("observation_id"),
        "outcome_rows": len(outcomes),
    }


def _resolved(base: dict[str, Any], timestamp: datetime, exit_price: float, reason: str, realized_r: float) -> dict[str, Any]:
    return {
        **base,
        "status": "RESOLVED",
        "exit_timestamp": timestamp.isoformat(),
        "exit_price": exit_price,
        "exit_reason": reason,
        "realized_r": realized_r,
        "results_before_commission": True,
    }


def _resolved_with_excursions(
    base: dict[str, Any],
    timestamp: datetime,
    exit_price: float,
    reason: str,
    realized_r: float,
    entry: Any,
    stop_distance: float,
    mfe_price: float,
    mfe_timestamp: datetime,
    mae_price: float,
    mae_timestamp: datetime,
) -> dict[str, Any]:
    resolved = _resolved(base, timestamp, exit_price, reason, realized_r)
    direction = str(base["direction"])
    mfe_r = (
        (mfe_price - entry.price) / stop_distance
        if direction == "BUY"
        else (entry.price - mfe_price) / stop_distance
    )
    mae_r = (
        (entry.price - mae_price) / stop_distance
        if direction == "BUY"
        else (mae_price - entry.price) / stop_distance
    )
    pip_size = 0.0001
    resolved.update(
        {
            "mfe_price": mfe_price,
            "mfe_pips": abs(mfe_price - entry.price) / pip_size,
            "mfe_r": max(0.0, mfe_r),
            "time_to_mfe_seconds": (mfe_timestamp - entry.timestamp).total_seconds(),
            "mae_price": mae_price,
            "mae_pips": abs(mae_price - entry.price) / pip_size,
            "mae_r": max(0.0, mae_r),
            "time_to_mae_seconds": (mae_timestamp - entry.timestamp).total_seconds(),
        }
    )
    return resolved


def _m15_candles_from_observation(observation: dict[str, Any]) -> list[OutcomeCandle]:
    payload = observation.get("strategy_input_payload") or {}
    context = (payload.get("contexts") or {}).get("M15") or {}
    timestamp = context.get("source_candle_timestamp")
    atr = context.get("atr14")
    close = context.get("close")
    if timestamp is None or atr is None or close is None:
        return []
    # Construct a deterministic equivalent ATR window whose final completed candle
    # ends at the recorded decision-time M15 context. The ATR value remains the
    # one persisted from completed M15 candles at decision time.
    final_timestamp = datetime.fromisoformat(timestamp)
    value = float(atr)
    center = float(close)
    out = []
    if final_timestamp.tzinfo is None:
        final_timestamp = final_timestamp.replace(tzinfo=UTC)
    final_timestamp = final_timestamp.astimezone(UTC)
    for index in range(15):
        out.append(
            OutcomeCandle(
                timestamp=final_timestamp - timedelta(minutes=15 * (14 - index)),
                high=center + value / 2,
                low=center - value / 2,
                close=center,
            )
        )
    return out
