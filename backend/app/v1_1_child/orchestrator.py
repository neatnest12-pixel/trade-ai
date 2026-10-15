"""Restart-safe, dependency-injected orchestration for child paper trades."""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.services.worker_lock import SingleWorkerLock

from .contract import HASH, PARENT_CONFIG, PARENT_SNAPSHOT, VERSION, atr14, make
from .journal import PaperTradeJournalError, append, rows
from .outcome_engine import RR_MULTIPLIERS, resolve_rr_outcome
from .risk_engine import size_paper_position

PROSPECTIVE_START = "2026-08-18T04:45:31+00:00"
PROCESSOR_LOCK_NAME = "navora-v1-1-paper-trade-processor"


@dataclass(frozen=True)
class MarketDataResult:
    status: str
    bars: list[dict[str, Any]]
    observation_cutoff: str
    provider: str = "unknown"
    error_reason: str | None = None
    symbol_metadata: dict[str, Any] | None = None
    account_currency: str = "INR"
    account_currency_to_inr_rate: Decimal | None = None


def normalize_provider_result(value: MarketDataResult | None) -> MarketDataResult:
    """Normalize provider absence without masking programmer errors."""
    if value is None:
        return MarketDataResult("TEMPORARILY_UNAVAILABLE", [], "", error_reason="PROVIDER_UNAVAILABLE")
    if value.status not in {"AVAILABLE", "TEMPORARILY_UNAVAILABLE", "INSUFFICIENT_HISTORY", "INVALID_DATA"}:
        raise ProcessorFailClosed("UNKNOWN_PROVIDER_STATUS")
    return value


def provider_call(call: Callable[[], MarketDataResult | None]) -> MarketDataResult:
    """Convert only expected connectivity failures into a safe provider state."""
    try:
        return normalize_provider_result(call())
    except (ConnectionError, TimeoutError):
        return MarketDataResult("TEMPORARILY_UNAVAILABLE", [], "", error_reason="PROVIDER_UNAVAILABLE")


def validate_completed_m1(bars: list[dict[str, Any]], observation_cutoff: str) -> None:
    cutoff = datetime.fromisoformat(observation_cutoff.replace("Z", "+00:00"))
    previous: datetime | None = None
    for bar in bars:
        timestamp = datetime.fromisoformat(str(bar["timestamp"]).replace("Z", "+00:00"))
        if previous is not None and timestamp <= previous:
            raise ProcessorFailClosed("OUT_OF_ORDER_M1")
        if timestamp + timedelta(minutes=1) > cutoff:
            raise ProcessorFailClosed("FUTURE_M1_CANDLE")
        high, low = Decimal(str(bar["high"])), Decimal(str(bar["low"]))
        open_price, close = Decimal(str(bar["open"])), Decimal(str(bar["close"]))
        if low > high or not low <= open_price <= high or not low <= close <= high:
            raise ProcessorFailClosed("INVALID_M1_DATA")
        previous = timestamp


class ProcessorFailClosed(RuntimeError):
    """The processor cannot safely continue from its persisted inputs."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def official_parents(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return one official post-boundary BUY/SELL record per parent identity."""
    result: dict[tuple[Any, ...], dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict) or record.get("type") != "prediction":
            continue
        if record.get("v1_snapshot_id") != PARENT_SNAPSHOT or record.get("v1_config_hash") != PARENT_CONFIG:
            continue
        if record.get("decision") not in {"BUY", "SELL"} or not record.get("record_hash"):
            continue
        timestamp = record.get("candle_timestamp")
        if not isinstance(timestamp, str) or timestamp <= PROSPECTIVE_START:
            continue
        key = (record["record_hash"], HASH)
        result.setdefault(key, record)
    return list(result.values())


class PaperTradeProcessor:
    """A single-run, read-only-data orchestrator; it never opens a broker order."""

    def __init__(self, parent_path: Path, child_path: Path, state_path: Path, lock_path: Path, processor_id: str) -> None:
        self.parent_path, self.child_path, self.state_path = parent_path, child_path, state_path
        self.lock = SingleWorkerLock(lock_path, processor_id, 60, PROCESSOR_LOCK_NAME)
        self.processor_id = processor_id
        self.started_at = _now()

    def _heartbeat(self, **extra: Any) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps({"status": "READ_ONLY", "processor_id": self.processor_id,
            "startup_timestamp": self.started_at, "last_heartbeat": _now(), "trade_contract_hash": HASH,
            "last_parent_seen": None, "last_contract_processed": None, "unresolved_contract_count": 0,
            **extra}, sort_keys=True), encoding="utf-8")

    def acquire(self) -> None:
        if not self.lock.acquire():
            raise ProcessorFailClosed("PROCESSOR_LOCK_UNAVAILABLE")

    def _guarded_append(self, record: dict[str, Any]) -> bool:
        """Verify the lease immediately before every append-only child mutation."""
        if not self.lock.is_owner():
            self._heartbeat(status="LEASE_LOST")
            raise ProcessorFailClosed("PROCESSOR_LEASE_LOST")
        return append(self.child_path, record)

    def read_parent_journal(self) -> list[dict[str, Any]]:
        """Read all parent rows or fail before processing any potentially corrupted input."""
        try:
            return [json.loads(line) for line in self.parent_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        except (OSError, json.JSONDecodeError) as error:
            raise ProcessorFailClosed("PARENT_JOURNAL_MALFORMED") from error

    def release(self) -> None:
        self.lock.release()

    def recover_outcomes(
        self, future_candles: Callable[[dict[str, Any]], list[dict[str, Any]]], observation_cutoff: str | None = None
    ) -> int:
        """Append only missing outcome identities for all persisted contracts."""
        try:
            child = rows(self.child_path)
        except PaperTradeJournalError as error:
            raise ProcessorFailClosed("CHILD_JOURNAL_MALFORMED") from error
        if not self.lock.is_owner():
            raise ProcessorFailClosed("PROCESSOR_LEASE_LOST")
        existing = {(r.get("paper_trade_id"), r.get("rr_profile"), r.get("cost_profile")) for r in child if r.get("record_type") == "RR_OUTCOME"}
        created = 0
        contracts = [r for r in child if r.get("record_type") == "TRADE_CONTRACT"]
        if any(contract.get("trade_contract_hash") != HASH for contract in contracts):
            raise ProcessorFailClosed("TRADE_CONTRACT_HASH_MISMATCH")
        for contract in contracts:
            try:
                candles = future_candles(contract)
            except (ConnectionError, TimeoutError) as error:
                raise ProcessorFailClosed("PROVIDER_UNAVAILABLE") from error
            if candles is None or candles == []:
                raise ProcessorFailClosed("PROVIDER_UNAVAILABLE")
            if observation_cutoff is not None:
                try:
                    cutoff = datetime.fromisoformat(observation_cutoff.replace("Z", "+00:00"))
                    if any(datetime.fromisoformat(c["timestamp"].replace("Z", "+00:00")) + timedelta(minutes=1) > cutoff for c in candles):
                        raise ProcessorFailClosed("FUTURE_M1_CANDLE")
                except (KeyError, TypeError, ValueError) as error:
                    raise ProcessorFailClosed("INVALID_M1_TIMESTAMP") from error
            for profile in RR_MULTIPLIERS:
                result = resolve_rr_outcome(contract, profile, candles)
                if result["outcome"] == "UNRESOLVED":
                    continue
                for cost_profile in ("IDEALIZED", "BASE", "STRESS"):
                    key = (contract.get("paper_trade_id"), profile, cost_profile)
                    if key in existing:
                        continue
                    record = {"record_type": "RR_OUTCOME", "created_at": _now(), "paper_trade_id": key[0],
                        "parent_prediction_id": contract.get("parent_prediction_id"), "cost_profile": cost_profile,
                        **result}
                    self._guarded_append(record)
                    existing.add(key)
                    created += 1
        self._heartbeat(unresolved_contract_count=sum(1 for c in contracts if not any(r.get("paper_trade_id") == c.get("paper_trade_id") for r in child)))
        return created

    def create_contracts(
        self,
        parents: list[dict[str, Any]],
        entry_data: Callable[[dict[str, Any]], tuple[list[dict[str, Any]], Any, dict[str, Any], str]],
    ) -> int:
        """Create one risk-approved contract per official parent, or persist one safe skip."""
        try:
            child = rows(self.child_path)
        except PaperTradeJournalError as error:
            raise ProcessorFailClosed("CHILD_JOURNAL_MALFORMED") from error
        known = {(r.get("parent_prediction_id"), r.get("trade_contract_hash")) for r in child if r.get("record_type") in {"TRADE_CONTRACT", "TRADE_SKIPPED"}}
        made = 0
        for parent in official_parents(parents):
            identity = (parent["record_hash"], HASH)
            if identity in known:
                continue
            try:
                data = entry_data(parent)
            except (ConnectionError, TimeoutError) as error:
                raise ProcessorFailClosed("PROVIDER_UNAVAILABLE") from error
            if data is None:
                raise ProcessorFailClosed("PROVIDER_UNAVAILABLE")
            if isinstance(data, MarketDataResult):
                if data.status != "AVAILABLE":
                    if data.status == "INSUFFICIENT_HISTORY":
                        continue
                    raise ProcessorFailClosed(data.error_reason or data.status)
                if data.symbol_metadata is None:
                    raise ProcessorFailClosed("PROVIDER_UNAVAILABLE")
                if len(data.bars) < 16:
                    continue
                validate_completed_m1(data.bars, data.observation_cutoff)
                history = data.bars[:-1]
                next_open = data.bars[-1]["open"]
                metadata, currency = data.symbol_metadata, data.account_currency
            else:
                history, next_open, metadata, currency = data
            if next_open is None or len(history) < 15:
                continue
            risk = size_paper_position(
                next_open,
                Decimal(str(next_open)) - atr14(history) * Decimal("1.5")
                if parent["decision"] == "BUY"
                else Decimal(str(next_open)) + atr14(history) * Decimal("1.5"),
                parent["decision"],
                metadata,
                currency,
                account_currency_to_inr_rate=(
                    data.account_currency_to_inr_rate if isinstance(data, MarketDataResult) else None
                ),
            )
            if risk["status"] != "READY":
                self._guarded_append({"record_type": "TRADE_SKIPPED", "created_at": _now(), "parent_prediction_id": identity[0], "trade_contract_hash": HASH, "reason": risk["reason"]})
                known.add(identity)
                continue
            contract = make(parent, Decimal(str(next_open)), atr14(history), Decimal(str(metadata["trade_tick_value"])), Decimal(str(metadata["volume_step"])), Decimal(str(metadata["volume_min"]))).record()
            contract.update({"created_at": _now(), "parent_prediction_id": identity[0], "parent_v1_snapshot_id": PARENT_SNAPSHOT, "parent_v1_config_hash": PARENT_CONFIG, "trade_contract_version": VERSION, "theoretical_volume": risk["final_volume"]})
            self._guarded_append(contract)
            known.add(identity)
            made += 1
        return made
