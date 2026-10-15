from __future__ import annotations

import sys
from datetime import UTC, datetime
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import URLError

from app.v3_4.providers.adapters.bea import BeaApiAdapter
from app.v3_4.providers.adapters.bls import BlsApiAdapter, asof_available
from app.v3_4.providers.adapters.bundesbank import BundesbankYieldAdapter
from app.v3_4.providers.adapters.ecb import EcbApiAdapter
from app.v3_4.providers.adapters.fred import FredApiAdapter, FredCsvAdapter
from app.v3_4.providers.adapters.macro_calendar import MacroCalendarAdapter
from app.v3_4.providers.adapters.mt5_gold import Mt5GoldAdapter


class _Response:
    def __init__(self, text: str) -> None:
        self._bytes = text.encode("utf-8")

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return BytesIO(self._bytes).read()


def test_fred_latest_and_normalized_rate() -> None:
    csv = "observation_date,DGS10\n2026-08-19,4.65\n2026-08-20,.\n"
    with patch("app.v3_4.providers.adapters.fred.urlopen", return_value=_Response(csv)):
        point = FredCsvAdapter().fetch_latest("US_10Y")
    assert not isinstance(point, dict)
    assert point.value == 4.65
    assert point.series_id == "DGS10"


def test_bls_api_fails_closed_without_key() -> None:
    with patch.dict("os.environ", {}, clear=True):
        record = BlsApiAdapter().fetch_latest("CPI")
    assert record["state"] == "UNAVAILABLE"
    assert record["reason"] == "BLS_KEY_MISSING"


def test_bls_api_parses_cpi_with_provenance() -> None:
    payload = '{"status":"REQUEST_SUCCEEDED","Results":{"series":[{"seriesID":"CUUR0000SA0","data":[{"year":"2026","period":"M07","value":"325.1","footnotes":[{}]}]}]}}'

    def fake_urlopen(_request: object, timeout: int = 20) -> _Response:
        return _Response(payload)

    with patch.dict("os.environ", {"V3_BLS_API_KEY": "SECRET"}, clear=True):
        with patch("app.v3_4.providers.adapters.bls.urlopen", side_effect=fake_urlopen):
            record = BlsApiAdapter().fetch_latest("CPI")
    assert record["provider"] == "BLS"
    assert record["series_id"] == "CUUR0000SA0"
    assert record["observation_period"] == "2026-M07"
    assert record["value"] == 325.1
    assert record["release_timestamp"] is None
    assert record["consensus_forecast"] == "UNAVAILABLE"
    assert record["no_lookahead_safe_for_decision_join"] is False
    assert "SECRET" not in str(record)


def test_bls_api_malformed_response_fails_closed() -> None:
    def fake_urlopen(_request: object, timeout: int = 20) -> _Response:
        return _Response('{"status":"REQUEST_FAILED"}')

    with patch.dict("os.environ", {"V3_BLS_API_KEY": "SECRET"}, clear=True):
        with patch("app.v3_4.providers.adapters.bls.urlopen", side_effect=fake_urlopen):
            record = BlsApiAdapter().fetch_latest("UNEMPLOYMENT")
    assert record["state"] == "UNAVAILABLE"
    assert record["provider"] == "BLS"


def test_bls_asof_requires_release_timestamp() -> None:
    assert asof_available({"release_timestamp": None}, datetime(2026, 8, 21, tzinfo=UTC)) is False
    assert asof_available({"release_timestamp": "2026-08-20T00:00:00+00:00"}, datetime(2026, 8, 21, tzinfo=UTC)) is True


def test_bea_api_fails_closed_without_key() -> None:
    with patch.dict("os.environ", {}, clear=True):
        record = BeaApiAdapter().fetch_latest("GDP")
    assert record["state"] == "UNAVAILABLE"
    assert record["reason"] == "BEA_KEY_MISSING"


def test_bea_api_parses_latest_nipa_record_without_release_time() -> None:
    payload = '{"BEAAPI":{"Results":{"Data":[{"LineNumber":"1","TimePeriod":"2025Q4","DataValue":"29,000.1","CL_UNIT":"Billions"},{"LineNumber":"1","TimePeriod":"2026Q2","DataValue":"30,000.1","CL_UNIT":"Billions"}]}}}'

    def fake_urlopen(_url: str, timeout: int = 20) -> _Response:
        return _Response(payload)

    with patch.dict("os.environ", {"V3_BEA_API_KEY": "SECRET"}, clear=True):
        with patch("app.v3_4.providers.adapters.bea.urlopen", side_effect=fake_urlopen):
            record = BeaApiAdapter().fetch_latest("GDP")
    assert record["provider"] == "BEA"
    assert record["observation_period"] == "2026Q2"
    assert record["historical_start"] == "2025Q4"
    assert record["observation_count"] == 2
    assert record["value"] == 30000.1
    assert record["release_timestamp"] is None
    assert record["decision_time_safe"] is False
    assert "SECRET" not in str(record)


def test_bea_api_error_fails_closed() -> None:
    payload = '{"BEAAPI":{"Results":{"Error":{"APIErrorDescription":"Invalid Request - Invalid Parameters."}}}}'

    def fake_urlopen(_url: str, timeout: int = 20) -> _Response:
        return _Response(payload)

    with patch.dict("os.environ", {"V3_BEA_API_KEY": "SECRET"}, clear=True):
        with patch("app.v3_4.providers.adapters.bea.urlopen", side_effect=fake_urlopen):
            record = BeaApiAdapter().fetch_latest("GDP")
    assert record["state"] == "UNAVAILABLE"
    assert record["reason"].startswith("BEA_API_ERROR")


def test_ecb_api_parses_csv_record() -> None:
    csv = "KEY,TIME_PERIOD,OBS_VALUE\nFM.D.U2.EUR.4F.KR.DFR.LEV,2026-08-20,2.25\n"
    with patch("app.v3_4.providers.adapters.ecb.urlopen", return_value=_Response(csv)):
        record = EcbApiAdapter().fetch_latest("ECB_RATE")
    assert record["provider"] == "ECB"
    assert record["value"] == 2.25
    assert record["release_time_status"] == "DATE_ONLY"
    assert record["decision_time_safe"] is False


def test_ecb_api_retries_transient_urlerror() -> None:
    csv = "KEY,TIME_PERIOD,OBS_VALUE\nFM.D.U2.EUR.4F.KR.DFR.LEV,2026-08-20,2.25\n"
    with patch("app.v3_4.providers.adapters.ecb.urlopen", side_effect=[URLError("temporary"), _Response(csv)]) as fake_urlopen:
        record = EcbApiAdapter().fetch_latest("ECB_RATE")
    assert fake_urlopen.call_count == 2
    assert record["provider"] == "ECB"
    assert record["value"] == 2.25


def test_ecb_api_fails_closed_after_retry_exhausted() -> None:
    with patch("app.v3_4.providers.adapters.ecb.urlopen", side_effect=URLError("dns")) as fake_urlopen:
        record = EcbApiAdapter().fetch_latest("ECB_RATE")
    assert fake_urlopen.call_count == 2
    assert record["state"] == "UNAVAILABLE"
    assert record["reason"] == "ECB_FETCH_FAILED:URLError"
    assert record["provider"] == "ECB"


def test_fred_api_fails_closed_without_key() -> None:
    with patch.dict("os.environ", {}, clear=True):
        record = FredApiAdapter().fetch_latest("US_10Y")
    assert record["state"] == "UNAVAILABLE"
    assert record["reason"] == "FRED_KEY_MISSING"


def test_fred_api_uses_present_key_without_exposing_it() -> None:
    payload = '{"observations":[{"date":"2026-08-20","value":"4.65"}]}'

    def fake_urlopen(request: object, timeout: int = 15) -> _Response:
        assert "SECRET" in request.full_url
        return _Response(payload)

    with patch.dict("os.environ", {"V3_FRED_API_KEY": "SECRET"}, clear=True):
        with patch("app.v3_4.providers.adapters.fred.urlopen", side_effect=fake_urlopen):
            record = FredApiAdapter().normalized_rate("US_10Y")
    assert record["instrument_id"] == "US_10Y"
    assert record["series_id"] == "DGS10"
    assert record["provider_id"] == "FRED"
    assert record["quality"] == "VERIFIED"
    assert record["no_lookahead"] is True
    assert "SECRET" not in str(record)


def test_fred_unavailable_de_2y_5y_fail_closed() -> None:
    adapter = FredCsvAdapter()
    assert adapter.fetch_latest("DE_2Y")["state"] == "UNAVAILABLE"
    assert adapter.fetch_latest("DE_5Y")["state"] == "UNAVAILABLE"


def test_fred_bundle_contains_required_labels() -> None:
    def fake_urlopen(url: str, timeout: int = 10) -> _Response:
        series = url.split("=")[-1]
        return _Response(f"observation_date,{series}\n2026-08-19,1.23\n")

    with patch("app.v3_4.providers.adapters.fred.urlopen", side_effect=fake_urlopen):
        bundle = FredCsvAdapter().fetch_required_bundle()
    assert bundle["FED_RATE"]["central_bank"] == "FED"
    assert bundle["ECB_RATE"]["central_bank"] == "ECB"
    assert bundle["US_2Y"]["instrument_id"] == "US_2Y"
    assert bundle["DE_10Y"]["instrument_id"] == "DE_10Y"
    assert bundle["DE_2Y"]["state"] == "UNAVAILABLE"
    assert bundle["DE_5Y"]["state"] == "UNAVAILABLE"


def test_fred_cross_asset_bundle() -> None:
    def fake_urlopen(url: str, timeout: int = 10) -> _Response:
        series = url.split("=")[-1]
        return _Response(f"observation_date,{series}\n2026-08-19,123.45\n")

    with patch("app.v3_4.providers.adapters.fred.urlopen", side_effect=fake_urlopen):
        bundle = FredCsvAdapter().fetch_cross_asset_bundle()
    assert bundle["USD_PROXY"]["instrument_id"] == "USD_PROXY"
    assert bundle["GOLD"]["instrument_id"] == "GOLD"
    assert bundle["EQUITY_PROXY"]["instrument_id"] == "EQUITY_PROXY"
    assert bundle["VOLATILITY_PROXY"]["instrument_id"] == "VOLATILITY_PROXY"


def test_bundesbank_german_2y_5y_bundle() -> None:
    csv = "TIME_PERIOD,OBS_VALUE\n2026-08-19,2.8\n2026-08-20,2.9\n"
    with patch("app.v3_4.providers.adapters.bundesbank.urlopen", return_value=_Response(csv)):
        bundle = BundesbankYieldAdapter().fetch_required_bundle()
    assert bundle["DE_2Y"]["instrument_id"] == "DE_2Y"
    assert bundle["DE_2Y"]["yield"] == 2.9
    assert bundle["DE_5Y"]["instrument_id"] == "DE_5Y"


def test_macro_calendar_adapter_fails_closed_without_credentials() -> None:
    with patch.dict("os.environ", {}, clear=True):
        sample = MacroCalendarAdapter().fetch_sample()
    assert sample["state"] == "UNAVAILABLE"
    assert sample["quality"] == "UNAVAILABLE"
    assert sample["reason"] == "MACRO_CREDENTIAL_REQUIRED"
    assert "forecast" in sample["required_fields"]


def test_mt5_gold_adapter_normalizes_xauusd_tick(monkeypatch) -> None:
    trusted_now = datetime(2026, 8, 21, 6, 30, tzinfo=UTC)
    server_epoch = int(trusted_now.timestamp()) + 3 * 60 * 60
    fake_mt5 = SimpleNamespace(
        initialize=lambda: True,
        shutdown=lambda: None,
        symbol_info=lambda symbol: SimpleNamespace(visible=True) if symbol == "XAUUSD" else None,
        symbol_info_tick=lambda symbol: SimpleNamespace(
            bid=4554.89, ask=4554.99, time=server_epoch, time_msc=server_epoch * 1000
        ) if symbol == "XAUUSD" else None,
    )
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake_mt5)
    with patch.dict("os.environ", {"V3_MT5_SERVER_UTC_OFFSET_SECONDS": "10800"}):
        with patch("app.v3_4.providers.adapters.mt5_gold.datetime") as fake_datetime:
            fake_datetime.now.return_value = trusted_now
            record = Mt5GoldAdapter().normalized()
    assert record["instrument_id"] == "XAUUSD"
    assert round(record["price"], 2) == 4554.94
    assert record["quality"] == "VERIFIED"
    assert record["stale"] is False
    assert record["timestamp"] == trusted_now.isoformat()
    assert record["timestamp_normalization"] == "v3.4-mt5-explicit-offset-r1"


def test_mt5_gold_adapter_fails_closed_without_mt5(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "MetaTrader5", None)
    record = Mt5GoldAdapter().normalized()
    assert record["state"] == "UNAVAILABLE"
    assert record["quality"] == "UNAVAILABLE"
