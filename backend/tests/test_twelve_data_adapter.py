import httpx
import pytest

from app.market_data.adapters import TwelveDataAdapter


def test_twelve_data_adapter_fetches_and_normalizes_sample():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["symbol"] == "EUR/USD"
        assert request.url.params["interval"] == "1min"
        assert request.url.params["timezone"] == "UTC"
        assert request.url.params["apikey"] == "test-key"
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "values": [
                    {
                        "datetime": "2026-01-01 00:01:00",
                        "open": "1.1000",
                        "high": "1.1010",
                        "low": "1.0990",
                        "close": "1.1005",
                    },
                    {
                        "datetime": "2026-01-01 00:00:00",
                        "open": "1.0990",
                        "high": "1.1000",
                        "low": "1.0985",
                        "close": "1.1000",
                    },
                ],
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    frame = TwelveDataAdapter("test-key", client=client).fetch("EURUSD", "1m", limit=2)

    assert list(frame["timestamp"].astype(str)) == [
        "2026-01-01 00:00:00+00:00",
        "2026-01-01 00:01:00+00:00",
    ]
    assert frame["source"].unique().tolist() == ["twelve_data"]
    assert frame["tick_volume"].tolist() == [0.0, 0.0]


def test_twelve_data_adapter_fails_without_api_key():
    with pytest.raises(RuntimeError, match="TWELVE_DATA_API_KEY"):
        TwelveDataAdapter(None)


def test_twelve_data_adapter_redacts_provider_error_key():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "error",
                "code": 401,
                "message": "bad test-key",
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError) as excinfo:
        TwelveDataAdapter("test-key", client=client).fetch("EURUSD", "1m", limit=2)

    message = str(excinfo.value)
    assert "test-key" not in message
    assert "[REDACTED]" in message
