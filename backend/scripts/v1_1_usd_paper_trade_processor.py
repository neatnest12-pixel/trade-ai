"""USD-policy continuous, read-only V1.1 paper-trade collector."""
# ruff: noqa: E402
from __future__ import annotations

import hashlib
import json
import os
import signal
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.worker_lock import SingleWorkerLock  # noqa: E402
from app.v1_1_child.journal import PaperTradeJournalError, append, rows  # noqa: E402
from app.v1_1_child.outcome_engine import RR_MULTIPLIERS, resolve_rr_outcome  # noqa: E402
from app.v1_1_usd.contract import (  # noqa: E402
    HASH,
    PARENT_CONFIG,
    PARENT_SNAPSHOT,
    VERSION,
    eligible,
    journal_fields,
)
from app.v1_1_usd.outcome_costs import price_outcome  # noqa: E402
from app.v1_1_usd.risk_engine import size_usd  # noqa: E402

PARENT = ROOT / "certified_data" / "FORWARD_PAPER_JOURNAL.jsonl"
JOURNAL = ROOT / "certified_data" / "PAPER_TRADE_JOURNAL_USD.jsonl"
STATE = ROOT / "runtime" / "v1_1_usd_processor_state.json"
LOCK = ROOT / "runtime" / "v1_1_usd_processor.lock"
LOCK_NAME = "navora-v1-1-usd-paper-trade-processor"
OUTCOME_PERSISTENCE_FIXED_AT = "2026-08-18T15:10:00+00:00"


def now() -> str: return datetime.now(UTC).isoformat()


class Runtime:
    def __init__(self, mt5: Any | None = None) -> None:
        self.mt5, self.id, self.started, self.stop = mt5, str(uuid.uuid4()), now(), False
        self.lock = SingleWorkerLock(LOCK, self.id, 60, LOCK_NAME)

    def state(self, status: str, **extra: Any) -> None:
        try: child = rows(JOURNAL)
        except PaperTradeJournalError: child = []
        contracts = [r for r in child if r.get("record_type") == "TRADE_CONTRACT"]
        outcomes = {r.get("paper_trade_id") for r in child if r.get("record_type") == "RR_OUTCOME"}
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps({"status": status, "processor_id": self.id, "startup_timestamp": self.started, "last_heartbeat": now(), "contract_version": VERSION, "contract_hash": HASH, "parent_snapshot": PARENT_SNAPSHOT, "parent_config_hash": PARENT_CONFIG, "last_parent_seen": contracts[-1].get("parent_prediction_id") if contracts else None, "last_contract_processed": contracts[-1].get("paper_trade_id") if contracts else None, "unresolved_contract_count": sum(r.get("paper_trade_id") not in outcomes for r in contracts), "last_cycle_status": status, "last_error": None, **extra}, sort_keys=True), encoding="utf-8")

    def preflight(self) -> None:
        if os.getenv("MT5_READ_ONLY", "").lower() != "true" or os.getenv("TRADING_EXECUTION_ENABLED", "").lower() != "false": raise RuntimeError("UNSAFE_EXECUTION_FLAGS")
        if os.getenv("DEMO_EXECUTION_ENABLED", "false").lower() != "false" or os.getenv("REAL_EXECUTION_ENABLED", "false").lower() != "false": raise RuntimeError("EXECUTION_ENABLED")
        if VERSION != "v1.1-paper-trade-usd-01" or HASH != "59e19bbb154b1fa77be0a14a63dc6b6f61c28b2d67767aa5bbfb609d99b75d9a": raise RuntimeError("USD_CONTRACT_MISMATCH")
        snapshot = json.loads((ROOT / "V1_PAPER_SNAPSHOT.json").read_text()); assert snapshot["v1_snapshot_id"] == PARENT_SNAPSHOT and snapshot["configuration_hash"] == PARENT_CONFIG
        [json.loads(x) for x in PARENT.read_text().splitlines() if x.strip()]; rows(JOURNAL)
        if not os.access(JOURNAL.parent, os.W_OK): raise RuntimeError("USD_JOURNAL_NOT_CREATEABLE")
        if self.mt5 is None:
            import MetaTrader5 as mt5
            self.mt5 = mt5
        if not self.mt5.initialize(): raise RuntimeError("MT5_INIT_FAILED")
        account = self.mt5.account_info()
        if account is None or account.server != "ICMarketsSC-Demo" or account.currency != "USD": raise RuntimeError("USD_ACCOUNT_REQUIRED")
        if self.mt5.symbol_info("EURUSD") is None: raise RuntimeError("EURUSD_UNAVAILABLE")

    def bars(self, count: int = 2000) -> list[dict[str, Any]]:
        raw = self.mt5.copy_rates_from_pos("EURUSD", self.mt5.TIMEFRAME_M1, 1, count)
        if raw is None: raise ConnectionError("PROVIDER_UNAVAILABLE")
        return [{"timestamp": datetime.fromtimestamp(float(x["time"]), UTC).isoformat(), "open": str(x["open"]), "high": str(x["high"]), "low": str(x["low"]), "close": str(x["close"]), "spread_points": str(x["spread"])} for x in raw]

    def metadata(self) -> dict[str, str]:
        info = self.mt5.symbol_info("EURUSD")
        return {k: str(getattr(info, k)) for k in ("point", "trade_tick_size", "trade_tick_value", "volume_min", "volume_step", "volume_max")}

    def atr(self, history: list[dict[str, Any]]) -> Decimal:
        if len(history) < 15: raise ValueError("INSUFFICIENT_ATR_HISTORY")
        return sum((max(Decimal(x["high"])-Decimal(x["low"]), abs(Decimal(x["high"])-Decimal(p["close"])), abs(Decimal(x["low"])-Decimal(p["close"]))) for p, x in zip(history[-15:-1], history[-14:], strict=True)), Decimal()) / Decimal(14)

    def cycle(self) -> None:
        if not self.lock.refresh(): raise RuntimeError("PROCESSOR_LEASE_LOST")
        if os.getenv("MT5_READ_ONLY", "").lower() != "true" or os.getenv("TRADING_EXECUTION_ENABLED", "").lower() != "false": raise RuntimeError("UNSAFE_EXECUTION_FLAGS")
        parents = eligible([json.loads(x) for x in PARENT.read_text().splitlines() if x.strip()]); existing = {(r.get("parent_prediction_id"), r.get("trade_contract_hash")) for r in rows(JOURNAL)}; bars = self.bars(); meta = self.metadata()
        for parent in parents:
            key = (parent["record_hash"], HASH)
            if key in existing: continue
            signal = datetime.fromisoformat(parent["candle_timestamp"].replace("Z", "+00:00")); entry_at = (signal + timedelta(minutes=1)).isoformat(); index = next((i for i, b in enumerate(bars) if b["timestamp"] == entry_at), None)
            if index is None or index < 15: continue
            entry, distance = Decimal(bars[index]["open"]), self.atr(bars[index-15:index]) * Decimal("1.5"); stop = entry-distance if parent["decision"] == "BUY" else entry+distance; risk = size_usd(entry, stop, parent["decision"], meta, "USD")
            if risk["status"] != "READY": append(JOURNAL, {"record_type": "TRADE_SKIPPED", "created_at": now(), "parent_prediction_id": parent["record_hash"], "trade_contract_hash": HASH, "reason": risk["reason"]}); continue
            sign = Decimal(1) if parent["decision"] == "BUY" else Decimal(-1); trade_id = hashlib.sha256((parent["record_hash"] + HASH).encode()).hexdigest()
            record = {"record_type": "TRADE_CONTRACT", "created_at": now(), "paper_trade_id": trade_id, "parent_prediction_id": parent["record_hash"], "parent_v1_snapshot_id": PARENT_SNAPSHOT, "parent_v1_config_hash": PARENT_CONFIG, "decision": parent["decision"], "signal_candle_timestamp": parent["candle_timestamp"], "entry_timestamp": entry_at, "entry_price": entry, "stop_price": stop, "risk_distance": distance, "atr_value": distance / Decimal("1.5"), "observed_entry_spread": Decimal(bars[index]["spread_points"]) * Decimal(meta["point"]), "theoretical_volume": risk["final_volume"], "max_hold_m1": 60, "rr_2_target": entry+sign*distance*2, "rr_3_target": entry+sign*distance*3, "rr_5_target": entry+sign*distance*5, "rr_10_target": entry+sign*distance*10, **journal_fields()}
            append(JOURNAL, record)
        child = rows(JOURNAL)
        for contract in [r for r in child if r.get("record_type") == "TRADE_CONTRACT"]:
            future = [b for b in bars if b["timestamp"] >= contract["entry_timestamp"]]
            for profile in RR_MULTIPLIERS:
                outcome = resolve_rr_outcome(contract, profile, future)
                if outcome["outcome"] == "UNRESOLVED": continue
                for cost in ("IDEALIZED", "BASE", "STRESS"):
                    priced = price_outcome(outcome, cost, contract.get("observed_entry_spread"), contract["risk_distance"])
                    if priced is None: continue
                    append(JOURNAL, {"record_type": "RR_OUTCOME", "created_at": now(), "outcome_persistence_fixed_at": OUTCOME_PERSISTENCE_FIXED_AT, "paper_trade_id": contract["paper_trade_id"], "parent_prediction_id": contract["parent_prediction_id"], "cost_profile": cost, **outcome, **priced})
        self.state("RUNNING")

    def run(self, once: bool = False) -> int:
        try: self.preflight()
        except Exception as e: self.state("FAILED", last_cycle_status="PREFLIGHT_FAILED", last_error=str(e)); return 2
        if not self.lock.acquire(): self.state("ALREADY_RUNNING", last_error="PROCESSOR_LOCK_UNAVAILABLE"); return 0
        try:
            while not self.stop:
                try: self.cycle()
                except ConnectionError as e: self.state("RUNNING", last_cycle_status="DATA_UNAVAILABLE", last_error=str(e))
                if once: break
                time.sleep(5)
            return 0
        except Exception as e: self.state("FAILED", last_error=str(e)); return 1
        finally: self.lock.release(); self.mt5.shutdown(); self.state("STOPPED")


def main() -> int:
    runtime = Runtime(); signal.signal(signal.SIGINT, lambda *_: setattr(runtime, "stop", True)); return runtime.run(os.getenv("V1_1_USD_PROCESSOR_ONCE", "false").lower() == "true")
if __name__ == "__main__": raise SystemExit(main())
