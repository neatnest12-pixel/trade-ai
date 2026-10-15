"""Append-only V1.2 $50K shadow-contract primitives; no execution integration."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.v1_1_child.outcome_engine import resolve_rr_outcome
from app.v1_2.demo_shadow import (
    HASH,
    VERSION,
    ShadowState,
    apply_net_r,
)

PROSPECTIVE_START = "2026-08-19T05:15:00+00:00"
JOURNAL = Path("certified_data/PAPER_TRADE_JOURNAL_V1_2_50K.jsonl")
RECORD_TYPES = {"SHADOW_CONTRACT", "SHADOW_OUTCOME", "SHADOW_SKIPPED", "SHADOW_GUARD"}


class ShadowJournalError(RuntimeError):
    pass


def digest(record: dict[str, Any]) -> str:
    payload = {k: v for k, v in record.items() if k != "record_hash"}
    return hashlib.sha256(
        json.dumps(payload, default=str, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def read(path: Path = JOURNAL) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    try:
        lines = path.read_text(encoding="utf8").splitlines()
    except OSError as error:
        raise ShadowJournalError("MALFORMED_SHADOW_JOURNAL") from error
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise ShadowJournalError("MALFORMED_SHADOW_JOURNAL") from error
        if row.get("record_type") not in RECORD_TYPES or row.get("record_hash") != digest(row):
            raise ShadowJournalError("MALFORMED_SHADOW_JOURNAL")
        out.append(row)
    return out


def append(record: dict[str, Any], path: Path = JOURNAL) -> bool:
    existing = read(path)
    identity = (
        record.get("record_type"),
        record.get("shadow_trade_id"),
        record.get("profile"),
        record.get("rr_profile"),
        record.get("cost_profile"),
    )
    if any(
        (
            r.get("record_type"),
            r.get("shadow_trade_id"),
            r.get("profile"),
            r.get("rr_profile"),
            r.get("cost_profile"),
        )
        == identity
        for r in existing
    ):
        return False
    record = {**record, "record_hash": digest(record)}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf8", newline="\n") as f:
        f.write(json.dumps(record, default=str, sort_keys=True, separators=(",", ":")) + "\n")
    return True


def eligible(parents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    dedup = {}
    for p in parents:
        if (
            p.get("type") != "prediction"
            or p.get("v1_snapshot_id") != "v1-paper-r4-0d891a2356e35464"
            or p.get("v1_config_hash")
            != "0d891a2356e35464dbe3ba9f51c50fe240e40feff84fe4008d8b1564b55f9eac"
            or p.get("decision") not in {"BUY", "SELL"}
            or p.get("candle_timestamp", "") <= PROSPECTIVE_START
        ):
            continue
        key = (p.get("symbol"), p.get("timeframe"), p.get("candle_timestamp"))
        dedup.setdefault(key, p)
    return list(dedup.values())


def contract(
    parent: dict[str, Any], entry: Decimal, atr: Decimal, spread: Decimal, metadata: dict[str, Any]
) -> dict[str, Any]:
    side = parent["decision"]
    risk = atr * Decimal("1.5")
    sign = Decimal(1) if side == "BUY" else Decimal(-1)
    trade_id = hashlib.sha256((parent["record_hash"] + HASH).encode()).hexdigest()
    return {
        "record_type": "SHADOW_CONTRACT",
        "shadow_trade_id": trade_id,
        "parent_prediction_id": parent["record_hash"],
        "signal_timestamp": parent["candle_timestamp"],
        "entry_timestamp": "NEXT_M1",
        "decision": side,
        "entry_price": entry,
        "atr_value": atr,
        "stop_price": entry - sign * risk,
        "risk_distance": risk,
        "observed_entry_spread": spread,
        "symbol_metadata": metadata,
        "max_hold_m1": 60,
        "rr_2_target": entry + sign * risk * 2,
        "rr_3_target": entry + sign * risk * 3,
        "rr_5_target": entry + sign * risk * 5,
        "rr_10_target": entry + sign * risk * 10,
        "policy_version": VERSION,
        "policy_hash": HASH,
        "prospective_start": PROSPECTIVE_START,
    }


def priced(
    contract_row: dict[str, Any], rr: str, cost: str, candles: list[dict[str, Any]]
) -> dict[str, Any] | None:
    raw = resolve_rr_outcome(contract_row, rr, candles)
    if raw["outcome"] == "UNRESOLVED":
        return None
    gross = Decimal(str(raw["gross_r"]))
    spread = Decimal(str(contract_row["observed_entry_spread"]))
    distance = Decimal(str(contract_row["risk_distance"]))
    fixed = {"IDEALIZED": Decimal(), "BASE": Decimal(".05"), "STRESS": Decimal(".15")}[cost]
    spread_r = (
        Decimal()
        if cost == "IDEALIZED"
        else spread / distance * (Decimal("1.5") if cost == "STRESS" else Decimal())
    )
    return {
        **raw,
        "cost_profile": cost,
        "gross_r": gross,
        "spread_cost_r": spread_r,
        "fixed_cost_r": fixed,
        "cost_r": spread_r + fixed,
        "net_r": gross - spread_r - fixed,
    }


def branch_state(records: list[dict[str, Any]], profile: str, rr: str, cost: str) -> ShadowState:
    state = ShadowState()
    for row in sorted(
        (
            r
            for r in records
            if r.get("record_type") == "SHADOW_OUTCOME"
            and (r.get("profile"), r.get("rr_profile"), r.get("cost_profile"))
            == (profile, rr, cost)
        ),
        key=lambda r: r.get("exit_timestamp", ""),
    ):
        state, _ = apply_net_r(state, profile, Decimal(str(row["net_r"])))
    return state
