from pathlib import Path

import pytest
from test_orchestrator import candle, metadata, parent

from app.v1_1_child.journal import rows
from app.v1_1_child.orchestrator import (
    MarketDataResult,
    PaperTradeProcessor,
    ProcessorFailClosed,
    validate_completed_m1,
)


def data(status: str = "AVAILABLE", cutoff: str = "2026-08-18T05:16:00+00:00", error_reason: str | None = None) -> MarketDataResult:
    return MarketDataResult(status, [candle(i) for i in range(16)], cutoff, error_reason=error_reason, symbol_metadata=metadata())


@pytest.fixture
def processor(tmp_path: Path) -> PaperTradeProcessor:
    return PaperTradeProcessor(tmp_path / "p", tmp_path / "c", tmp_path / "s", tmp_path / "l", "r5")


def test_new_contract_future_data_and_provider_unavailable_write_nothing(processor: PaperTradeProcessor) -> None:
    processor.acquire()
    try:
        with pytest.raises(ProcessorFailClosed):
            processor.create_contracts([parent()], lambda _p: data(cutoff="2026-08-18T05:15:59+00:00"))
        assert not processor.child_path.exists()
        with pytest.raises(ProcessorFailClosed, match="PROVIDER_UNAVAILABLE"):
            processor.create_contracts([parent()], lambda _p: data("TEMPORARILY_UNAVAILABLE", error_reason="PROVIDER_UNAVAILABLE"))
        assert not processor.child_path.exists()
    finally:
        processor.release()


def test_exact_m1_completion_boundary() -> None:
    bars = [candle(0)]
    with pytest.raises(ProcessorFailClosed):
        validate_completed_m1(bars, "2026-08-18T05:00:59+00:00")
    validate_completed_m1(bars, "2026-08-18T05:01:00+00:00")
    validate_completed_m1(bars, "2026-08-18T05:01:01+00:00")


def test_provider_recovery_creates_once(processor: PaperTradeProcessor) -> None:
    processor.acquire()
    try:
        with pytest.raises(ProcessorFailClosed):
            processor.create_contracts([parent()], lambda _p: data("TEMPORARILY_UNAVAILABLE", error_reason="PROVIDER_UNAVAILABLE"))
        assert processor.create_contracts([parent()], lambda _p: data()) == 1
        assert len(rows(processor.child_path)) == 1
    finally:
        processor.release()
