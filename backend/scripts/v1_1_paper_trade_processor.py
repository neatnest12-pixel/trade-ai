"""Certified continuous, read-only runtime for the frozen V1.1 child processor."""
# ruff: noqa: E402
from __future__ import annotations

import json
import os
import signal
import sys
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.v1_1_child.contract import HASH, PARENT_CONFIG, PARENT_SNAPSHOT, VERSION  # noqa: E402
from app.v1_1_child.journal import PaperTradeJournalError, rows  # noqa: E402
from app.v1_1_child.orchestrator import (
    MarketDataResult,
    PaperTradeProcessor,
    ProcessorFailClosed,
)  # noqa: E402

PARENT_JOURNAL = ROOT / "certified_data" / "FORWARD_PAPER_JOURNAL.jsonl"
CHILD_JOURNAL = ROOT / "certified_data" / "PAPER_TRADE_JOURNAL.jsonl"
STATE_PATH = ROOT / "runtime" / "v1_1_processor_state.json"
LOCK_PATH = ROOT / "runtime" / "v1_1_processor.lock"
SERVER = "ICMarketsSC-Demo"
SYMBOL = "EURUSD"
POLL_SECONDS = 5
FX_QUOTE_MAX_AGE_SECONDS = 60


class PreflightError(RuntimeError):
    pass


def now_utc() -> str:
    return datetime.now(UTC).isoformat()


class Runtime:
    """Owns process lifetime only; contract, journal, and outcome logic remain in the child package."""

    def __init__(
        self,
        *,
        mt5: Any | None = None,
        processor_id: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], str] = now_utc,
        parent_path: Path = PARENT_JOURNAL,
        child_path: Path = CHILD_JOURNAL,
        state_path: Path = STATE_PATH,
        lock_path: Path = LOCK_PATH,
    ) -> None:
        self.mt5 = mt5
        self.processor_id = processor_id or str(uuid.uuid4())
        self.sleep, self.now = sleep, now
        self.parent_path, self.child_path, self.state_path = parent_path, child_path, state_path
        self.processor = PaperTradeProcessor(parent_path, child_path, state_path, lock_path, self.processor_id)
        self.started_at = self.now()
        self.stop_requested = False
        self.server_offset_hours = 0

    def _write_state(self, status: str, **extra: Any) -> None:
        contracts = outcomes = skipped = unresolved = 0
        last_contract = None
        try:
            child_rows = rows(self.child_path)
            contracts_rows = [r for r in child_rows if r.get("record_type") == "TRADE_CONTRACT"]
            contracts = len(contracts_rows)
            outcomes = len([r for r in child_rows if r.get("record_type") == "RR_OUTCOME"])
            skipped = len([r for r in child_rows if r.get("record_type") == "TRADE_SKIPPED"])
            last_contract = contracts_rows[-1].get("paper_trade_id") if contracts_rows else None
            resolved = {r.get("paper_trade_id") for r in child_rows if r.get("record_type") == "RR_OUTCOME"}
            unresolved = sum(c.get("paper_trade_id") not in resolved for c in contracts_rows)
        except PaperTradeJournalError:
            pass
        last_parent = None
        try:
            parents = self.processor.read_parent_journal()
            eligible = self._official(parents)
            last_parent = eligible[-1].get("record_hash") if eligible else None
        except ProcessorFailClosed:
            pass
        payload = {
            "status": status,
            "processor_id": self.processor_id,
            "startup_timestamp": self.started_at,
            "last_heartbeat": self.now(),
            "trade_contract_version": VERSION,
            "trade_contract_hash": HASH,
            "parent_snapshot": PARENT_SNAPSHOT,
            "parent_config_hash": PARENT_CONFIG,
            "last_parent_seen": last_parent,
            "last_contract_processed": last_contract,
            "unresolved_contract_count": unresolved,
            "official_contract_count": contracts,
            "resolved_outcome_count": outcomes,
            "skipped_contract_count": skipped,
            "last_cycle_status": status,
            "last_error": None,
            **extra,
        }
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    @staticmethod
    def _official(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        from app.v1_1_child.orchestrator import official_parents

        return official_parents(records)

    def _guard_flags(self) -> None:
        if os.getenv("MT5_READ_ONLY", "").lower() != "true":
            raise PreflightError("MT5_READ_ONLY_REQUIRED")
        if os.getenv("TRADING_EXECUTION_ENABLED", "").lower() != "false":
            raise PreflightError("TRADING_EXECUTION_DISABLED_REQUIRED")
        if os.getenv("DEMO_EXECUTION_ENABLED", "false").lower() != "false":
            raise PreflightError("DEMO_EXECUTION_DISABLED_REQUIRED")
        if os.getenv("REAL_EXECUTION_ENABLED", "false").lower() != "false":
            raise PreflightError("REAL_EXECUTION_DISABLED_REQUIRED")

    def preflight(self) -> None:
        self._guard_flags()
        if HASH != "89163bcf300dcb4e5a940de345a8d3914b0e8cac1d70287ffb8a1f768d54c6cd":
            raise PreflightError("TRADE_CONTRACT_HASH_MISMATCH")
        snapshot = json.loads((ROOT / "V1_PAPER_SNAPSHOT.json").read_text(encoding="utf-8"))
        if snapshot.get("v1_snapshot_id") != PARENT_SNAPSHOT:
            raise PreflightError("PARENT_SNAPSHOT_MISMATCH")
        if snapshot.get("configuration_hash") != PARENT_CONFIG:
            raise PreflightError("PARENT_CONFIG_MISMATCH")
        self.processor.read_parent_journal()
        try:
            rows(self.child_path)
        except PaperTradeJournalError as error:
            raise PreflightError("MALFORMED_PAPER_TRADE_JOURNAL") from error
        if not os.access(self.child_path.parent, os.W_OK):
            raise PreflightError("PAPER_TRADE_JOURNAL_NOT_CREATEABLE")
        if self.mt5 is None:
            import MetaTrader5 as mt5

            self.mt5 = mt5
        if not self.mt5.initialize():
            raise PreflightError("MT5_INITIALIZE_FAILED")
        account = self.mt5.account_info()
        if account is None or getattr(account, "server", None) != SERVER:
            raise PreflightError("MT5_SERVER_MISMATCH")
        if self.mt5.symbol_info(SYMBOL) is None:
            raise PreflightError("EURUSD_UNAVAILABLE")
        tick = self.mt5.symbol_info_tick(SYMBOL)
        if tick is None:
            raise PreflightError("EURUSD_TICK_UNAVAILABLE")
        self.server_offset_hours = round(
            (datetime.fromtimestamp(float(tick.time), UTC) - datetime.now(UTC)).total_seconds() / 3600
        )

    def _candles(self, count: int = 2000) -> list[dict[str, Any]]:
        raw = self.mt5.copy_rates_from_pos(SYMBOL, self.mt5.TIMEFRAME_M1, 1, count)
        if raw is None:
            raise ConnectionError("MT5_RATES_UNAVAILABLE")
        result = []
        for value in raw:
            timestamp = (
                datetime.fromtimestamp(float(value["time"]), UTC)
                - timedelta(hours=self.server_offset_hours)
            ).isoformat()
            result.append({"timestamp": timestamp, "open": str(value["open"]), "high": str(value["high"]),
                           "low": str(value["low"]), "close": str(value["close"]), "spread": str(value["spread"])})
        return result

    def _account_to_inr_rate(self, account_currency: str) -> Decimal | None:
        """Return a conservative, fresh MT5-only account-currency -> INR conversion rate.

        A forward edge sells its base currency at bid; an inverse edge buys its base
        currency at ask.  Both choices ensure the final INR-risk recheck cannot use
        a more favourable side of the quote.  The shortest deterministic valid path
        is used, with no cache and no external FX source.
        """
        if account_currency == "INR":
            return Decimal("1")
        now = datetime.now(UTC)
        edges: dict[str, list[tuple[str, Decimal, str]]] = {}
        for symbol in self.mt5.symbols_get() or []:
            base, quote = str(getattr(symbol, "currency_base", "")), str(getattr(symbol, "currency_profit", ""))
            if not base or not quote or base == quote or not self.mt5.symbol_select(symbol.name, True):
                continue
            tick = self.mt5.symbol_info_tick(symbol.name)
            if tick is None or float(getattr(tick, "time", 0)) <= 0:
                continue
            timestamp = datetime.fromtimestamp(float(tick.time), UTC) - timedelta(hours=self.server_offset_hours)
            if abs((now - timestamp).total_seconds()) > FX_QUOTE_MAX_AGE_SECONDS:
                continue
            bid, ask = Decimal(str(getattr(tick, "bid", 0))), Decimal(str(getattr(tick, "ask", 0)))
            if bid <= 0 or ask <= 0 or ask < bid:
                continue
            edges.setdefault(base, []).append((quote, bid, symbol.name))
            edges.setdefault(quote, []).append((base, Decimal("1") / ask, symbol.name))
        queue: list[tuple[str, Decimal, tuple[str, ...]]] = [(account_currency, Decimal("1"), ())]
        seen = {account_currency}
        while queue:
            currency, rate, route = queue.pop(0)
            if currency == "INR":
                return rate
            for destination, edge_rate, name in sorted(edges.get(currency, []), key=lambda item: (item[0], item[2])):
                if destination not in seen:
                    seen.add(destination)
                    queue.append((destination, rate * edge_rate, (*route, name)))
        return None

    def _metadata(self) -> tuple[dict[str, Any], str, Decimal | None]:
        info = self.mt5.symbol_info(SYMBOL)
        account = self.mt5.account_info()
        if info is None or account is None:
            raise ConnectionError("MT5_METADATA_UNAVAILABLE")
        fields = ("point", "trade_tick_size", "trade_tick_value", "trade_contract_size", "volume_min", "volume_step", "volume_max")
        metadata = {field: str(getattr(info, field)) for field in fields}
        currency = str(getattr(account, "currency", ""))
        return metadata, currency, self._account_to_inr_rate(currency)

    def _entry_data(self, parent: dict[str, Any]) -> MarketDataResult:
        signal = datetime.fromisoformat(parent["candle_timestamp"].replace("Z", "+00:00"))
        entry_time = (signal + timedelta(minutes=1)).isoformat()
        bars = self._candles()
        index = next((i for i, bar in enumerate(bars) if bar["timestamp"] == entry_time), None)
        if index is None or index < 15:
            return MarketDataResult("INSUFFICIENT_HISTORY", [], self.now())
        metadata, currency, rate = self._metadata()
        return MarketDataResult("AVAILABLE", bars[index - 15:index + 1], self.now(), provider="MT5",
                                symbol_metadata=metadata, account_currency=currency,
                                account_currency_to_inr_rate=rate)

    def _future_candles(self, contract: dict[str, Any]) -> list[dict[str, Any]]:
        signal = datetime.fromisoformat(contract["signal_candle_timestamp"].replace("Z", "+00:00"))
        entry_time = (signal + timedelta(minutes=1)).isoformat()
        return [bar for bar in self._candles() if bar["timestamp"] >= entry_time]

    def cycle(self) -> None:
        if not self.processor.lock.refresh():
            raise ProcessorFailClosed("PROCESSOR_LEASE_LOST")
        self._guard_flags()
        parents = self.processor.read_parent_journal()
        try:
            self.processor.create_contracts(parents, self._entry_data)
            self.processor.recover_outcomes(self._future_candles, observation_cutoff=self.now())
        except ProcessorFailClosed as error:
            if str(error) in {"PROVIDER_UNAVAILABLE", "MT5_RATES_UNAVAILABLE", "MT5_METADATA_UNAVAILABLE"}:
                raise ConnectionError(str(error)) from error
            raise
        self._write_state("RUNNING")

    def run(self, *, once: bool = False, max_cycles: int | None = None) -> int:
        try:
            self.preflight()
        except Exception as error:
            self._write_state("FAILED", last_cycle_status="PREFLIGHT_FAILED", last_error=str(error))
            self.shutdown(release=False)
            return 2
        if not self.processor.lock.acquire():
            self._write_state("ALREADY_RUNNING", last_error="PROCESSOR_LOCK_UNAVAILABLE")
            self.shutdown(release=False)
            return 0
        cycles = 0
        self._write_state("RUNNING")
        try:
            while not self.stop_requested:
                try:
                    self.cycle()
                except (ConnectionError, TimeoutError) as error:
                    self._write_state("RUNNING", last_cycle_status="DATA_UNAVAILABLE", last_error=str(error))
                cycles += 1
                if once or (max_cycles is not None and cycles >= max_cycles):
                    break
                self.sleep(POLL_SECONDS)
            return 0
        except Exception as error:
            self._write_state("FAILED", last_cycle_status="FAILED", last_error=str(error))
            return 1
        finally:
            self.shutdown(release=True)

    def shutdown(self, *, release: bool) -> None:
        if release:
            self.processor.release()
        if self.mt5 is not None:
            self.mt5.shutdown()
        if release:
            self._write_state("STOPPED")


def main() -> int:
    runtime = Runtime()
    def stop(_signum: int, _frame: Any) -> None:
        runtime.stop_requested = True
    signal.signal(signal.SIGINT, stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, stop)
    return runtime.run(once=os.getenv("V1_1_PROCESSOR_ONCE", "false").lower() == "true")


if __name__ == "__main__":
    raise SystemExit(main())
