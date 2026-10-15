from pathlib import Path

import pytest
from test_orchestrator import candle, metadata, parent

from app.v1_1_child.journal import rows
from app.v1_1_child.orchestrator import PaperTradeProcessor, ProcessorFailClosed


@pytest.fixture
def processor(tmp_path: Path) -> PaperTradeProcessor:
    return PaperTradeProcessor(tmp_path / "p", tmp_path / "c", tmp_path / "state.json", tmp_path / "lock", "r4")


def test_future_m1_outcome_fails_closed_without_writes(processor: PaperTradeProcessor) -> None:
    processor.acquire()
    try:
        processor.create_contracts([parent()], lambda _p: ([candle(i) for i in range(16)], "100", metadata(), "INR"))
        before = rows(processor.child_path)
        with pytest.raises(ProcessorFailClosed, match="FUTURE_M1_CANDLE"):
            processor.recover_outcomes(lambda _c: [candle(0), candle(1)], observation_cutoff="2026-08-18T05:00:30+00:00")
        assert rows(processor.child_path) == before
    finally:
        processor.release()


def test_m1_completion_boundary_and_provider_failure(processor: PaperTradeProcessor) -> None:
    processor.acquire()
    try:
        processor.create_contracts([parent()], lambda _p: ([candle(i) for i in range(16)], "100", metadata(), "INR"))
        before = rows(processor.child_path)
        with pytest.raises(RuntimeError, match="PROVIDER_UNAVAILABLE"):
            processor.recover_outcomes(lambda _c: (_ for _ in ()).throw(RuntimeError("PROVIDER_UNAVAILABLE")))
        assert rows(processor.child_path) == before
    finally:
        processor.release()
