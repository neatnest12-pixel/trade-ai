import json
from pathlib import Path

import pytest

from app.v1_1_child.contract import PARENT_CONFIG, PARENT_SNAPSHOT
from app.v1_1_child.journal import append, rows
from app.v1_1_child.orchestrator import PaperTradeProcessor, ProcessorFailClosed, official_parents


def parent(side: str = "BUY") -> dict[str, str]:
    return {"type": "prediction", "record_hash": f"parent-{side}", "v1_snapshot_id": PARENT_SNAPSHOT,
            "v1_config_hash": PARENT_CONFIG, "decision": side, "candle_timestamp": "2026-08-18T05:00:00+00:00"}


def candle(index: int, *, high: str = "101", low: str = "99", close: str = "100") -> dict[str, str]:
    return {"timestamp": f"2026-08-18T05:{index:02d}:00+00:00", "open": "100", "high": high, "low": low, "close": close}


def metadata() -> dict[str, str]:
    return {"point": ".01", "trade_tick_size": ".01", "trade_tick_value": ".01", "trade_contract_size": "1", "volume_min": ".01", "volume_step": ".01", "volume_max": "100"}


@pytest.fixture
def processor(tmp_path: Path) -> PaperTradeProcessor:
    return PaperTradeProcessor(tmp_path / "parent.jsonl", tmp_path / "child.jsonl", tmp_path / "runtime/state.json", tmp_path / "lock", "test")


@pytest.mark.parametrize("side,high,low", [("BUY", "130", "98"), ("SELL", "102", "70")])
def test_buy_sell_offline_end_to_end(processor: PaperTradeProcessor, side: str, high: str, low: str) -> None:
    processor.acquire()
    try:
        assert processor.create_contracts([parent(side)], lambda _p: ([candle(i) for i in range(16)], "100", metadata(), "INR")) == 1
        contract = rows(processor.child_path)[0]
        assert contract["parent_prediction_id"] == parent(side)["record_hash"]
        assert processor.recover_outcomes(lambda _c: [candle(30, high=high, low=low)]) == 12
        assert len([r for r in rows(processor.child_path) if r["record_type"] == "RR_OUTCOME"]) == 12
        assert processor.create_contracts([parent(side)], lambda _p: pytest.fail("duplicate contract")) == 0
    finally:
        processor.release()


def test_restart_partial_recovery_and_idempotency(processor: PaperTradeProcessor) -> None:
    processor.acquire()
    try:
        processor.create_contracts([parent()], lambda _p: ([candle(i) for i in range(16)], "100", metadata(), "INR"))
        contract = rows(processor.child_path)[0]
        for profile in ("RR_2",):
            for cost in ("IDEALIZED", "BASE", "STRESS"):
                append(processor.child_path, {"record_type": "RR_OUTCOME", "created_at": "2026-08-18T06:00:00+00:00", "paper_trade_id": contract["paper_trade_id"], "parent_prediction_id": contract["parent_prediction_id"], "rr_profile": profile, "cost_profile": cost, "outcome": "TP_HIT"})
        assert processor.recover_outcomes(lambda _c: [candle(30, high="130", low="98")]) == 9
        assert processor.recover_outcomes(lambda _c: [candle(30, high="130", low="98")]) == 0
    finally:
        processor.release()


def test_parent_filter_and_lock_heartbeat_and_child_fail_closed(processor: PaperTradeProcessor, tmp_path: Path) -> None:
    assert official_parents([parent(), {**parent(), "decision": "NO_TRADE"}, {**parent(), "candle_timestamp": "2026-08-18T04:45:31+00:00"}]) == [parent()]
    second = PaperTradeProcessor(tmp_path / "p", tmp_path / "c", tmp_path / "runtime/other.json", processor.lock.path, "other")
    processor.acquire()
    try:
        with pytest.raises(ProcessorFailClosed):
            second.acquire()
        processor._heartbeat(last_parent_seen="parent-B", last_contract_processed="contract-B")
        state = json.loads(processor.state_path.read_text(encoding="utf-8"))
        assert {"status", "processor_id", "startup_timestamp", "last_heartbeat", "trade_contract_hash", "last_parent_seen", "last_contract_processed", "unresolved_contract_count"} <= state.keys()
        processor.child_path.write_text("not-json\n", encoding="utf-8")
        with pytest.raises(ProcessorFailClosed):
            processor.recover_outcomes(lambda _c: [])
    finally:
        processor.release()


@pytest.mark.parametrize("side,close,high,low", [("BUY", "102", "103", "99"), ("SELL", "98", "101", "97")])
def test_expiry_recovery_is_idempotent(processor: PaperTradeProcessor, side: str, close: str, high: str, low: str) -> None:
    processor.acquire()
    try:
        processor.create_contracts([parent(side)], lambda _p: ([candle(i) for i in range(16)], "100", metadata(), "INR"))
        future = [candle(i, high=high, low=low, close=close) for i in range(60)]
        assert processor.recover_outcomes(lambda _c: future) == 12
        outcomes = [row for row in rows(processor.child_path) if row["record_type"] == "RR_OUTCOME"]
        assert len(outcomes) == 12
        assert {row["outcome"] for row in outcomes} == {"EXPIRED"}
        assert {row["holding_candles"] for row in outcomes} == {60}
        assert processor.recover_outcomes(lambda _c: future) == 0
    finally:
        processor.release()


@pytest.mark.parametrize("bad_metadata,reason", [({}, "RISK_METADATA_UNAVAILABLE"), ({"point": ".01", "trade_tick_size": ".01", "trade_tick_value": "10000", "trade_contract_size": "1", "volume_min": ".01", "volume_step": ".01", "volume_max": "100"}, "RISK_TOO_SMALL_FOR_MIN_LOT")])
def test_risk_skip_is_deduplicated_after_restart(processor: PaperTradeProcessor, bad_metadata: dict[str, str], reason: str) -> None:
    processor.acquire()
    try:
        def entry(_parent: object) -> tuple[list[dict[str, str]], str, dict[str, str], str]:
            return [candle(i) for i in range(16)], "100", bad_metadata, "INR"
        assert processor.create_contracts([parent()], entry) == 0
        first = rows(processor.child_path)
        assert len(first) == 1 and first[0]["record_type"] == "TRADE_SKIPPED" and first[0]["reason"] == reason
        assert processor.create_contracts([parent()], entry) == 0
        assert rows(processor.child_path) == first
    finally:
        processor.release()
