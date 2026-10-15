import pytest

from app.v1_1_child.orchestrator import (
    MarketDataResult,
    ProcessorFailClosed,
    normalize_provider_result,
    provider_call,
)


def test_provider_none_and_connectivity_are_unavailable() -> None:
    assert provider_call(lambda: None).status == "TEMPORARILY_UNAVAILABLE"
    assert provider_call(lambda: (_ for _ in ()).throw(ConnectionError("down"))).error_reason == "PROVIDER_UNAVAILABLE"
    assert provider_call(lambda: (_ for _ in ()).throw(TimeoutError("down"))).status == "TEMPORARILY_UNAVAILABLE"


def test_programmer_errors_are_not_masked() -> None:
    with pytest.raises(TypeError):
        provider_call(lambda: (_ for _ in ()).throw(TypeError("bug")))


def test_unknown_status_fails_closed() -> None:
    with pytest.raises(ProcessorFailClosed, match="UNKNOWN_PROVIDER_STATUS"):
        normalize_provider_result(MarketDataResult("UNKNOWN", [], ""))
