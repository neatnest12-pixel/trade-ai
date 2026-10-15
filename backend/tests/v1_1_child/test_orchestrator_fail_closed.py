from pathlib import Path

import pytest
from test_orchestrator import candle, metadata, parent

from app.v1_1_child.journal import append, rows
from app.v1_1_child.orchestrator import PaperTradeProcessor, ProcessorFailClosed


@pytest.fixture
def processor(tmp_path: Path) -> PaperTradeProcessor:
    return PaperTradeProcessor(tmp_path / "parent.jsonl", tmp_path / "child.jsonl", tmp_path / "state.json", tmp_path / "lock", "fail-test")


def test_malformed_parent_and_hash_mismatch_do_not_write(processor: PaperTradeProcessor) -> None:
    processor.parent_path.write_text('{"type":"prediction"}\nnot-json\n', encoding="utf-8")
    with pytest.raises(ProcessorFailClosed, match="PARENT_JOURNAL_MALFORMED"):
        processor.read_parent_journal()
    assert not processor.child_path.exists()
    processor.acquire()
    try:
        append(processor.child_path, {"record_type": "TRADE_CONTRACT", "created_at": "t", "trade_contract_hash": "wrong"})
        before = processor.child_path.read_bytes()
        with pytest.raises(ProcessorFailClosed, match="TRADE_CONTRACT_HASH_MISMATCH"):
            processor.recover_outcomes(lambda _c: [])
        assert processor.child_path.read_bytes() == before
    finally:
        processor.release()


def test_lease_loss_before_contract_or_outcome_write_is_safe(processor: PaperTradeProcessor) -> None:
    processor.acquire()
    processor.lock.abandon_for_test()
    with pytest.raises(ProcessorFailClosed, match="PROCESSOR_LEASE_LOST"):
        processor.create_contracts([parent()], lambda _p: ([candle(i) for i in range(16)], "100", metadata(), "INR"))
    assert not processor.child_path.exists()


def test_invalid_or_out_of_order_future_path_writes_nothing(processor: PaperTradeProcessor) -> None:
    processor.acquire()
    try:
        processor.create_contracts([parent()], lambda _p: ([candle(i) for i in range(16)], "100", metadata(), "INR"))
        before = rows(processor.child_path)
        with pytest.raises(ValueError):
            processor.recover_outcomes(lambda _c: [candle(2), candle(1)])
        assert rows(processor.child_path) == before
    finally:
        processor.release()
