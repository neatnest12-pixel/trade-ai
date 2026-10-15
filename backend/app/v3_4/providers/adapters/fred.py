from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from io import StringIO
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.v3_4.providers.contracts import Quality, central_bank_rate_record, yield_record

FRED_GRAPH_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
FRED_API_OBSERVATIONS = "https://api.stlouisfed.org/fred/series/observations"


@dataclass(frozen=True)
class FredSeriesPoint:
    series_id: str
    timestamp: datetime
    value: float
    source: str = "FRED"
    quality: Quality = Quality.VERIFIED


class FredCsvAdapter:
    provider_id = "fred_public_csv"
    timeout_seconds = 10
    max_retries = 1

    series = {
        "FED_RATE": "FEDFUNDS",
        "ECB_RATE": "ECBDFR",
        "US_2Y": "DGS2",
        "US_5Y": "DGS5",
        "US_10Y": "DGS10",
        "DE_10Y": "IRLTLT01DEM156N",
        "USD_PROXY": "DTWEXBGS",
        "GOLD": "GOLDAMGBD228NLBM",
        "EQUITY_PROXY": "SP500",
        "VOLATILITY_PROXY": "VIXCLS",
    }

    unavailable_series = {
        "DE_2Y": "UNAVAILABLE_NO_CERTIFIED_SERIES_SELECTED",
        "DE_5Y": "UNAVAILABLE_NO_CERTIFIED_SERIES_SELECTED",
    }

    def fetch_latest(self, label: str) -> FredSeriesPoint | dict[str, str]:
        if label in self.unavailable_series:
            return {"state": "UNAVAILABLE", "reason": self.unavailable_series[label], "provider_id": self.provider_id}
        series_id = self.series[label]
        text = self._download(series_id)
        reader = csv.DictReader(StringIO(text))
        latest: FredSeriesPoint | None = None
        for row in reader:
            value = row.get(series_id)
            date = row.get("observation_date")
            if not value or value == "." or not date:
                continue
            latest = FredSeriesPoint(series_id, datetime.fromisoformat(date).replace(tzinfo=UTC), float(value))
        if latest is None:
            return {"state": "UNAVAILABLE", "reason": "NO_VALID_OBSERVATION", "provider_id": self.provider_id, "series_id": series_id}
        return latest

    def normalized_rate(self, label: str) -> dict[str, object]:
        point = self.fetch_latest(label)
        if isinstance(point, dict):
            return point
        if label == "FED_RATE":
            return central_bank_rate_record("FED", "USD", point.value, point.timestamp, None, self.provider_id)
        if label == "ECB_RATE":
            return central_bank_rate_record("ECB", "EUR", point.value, point.timestamp, None, self.provider_id)
        return yield_record(label, point.timestamp, point.value, self.provider_id)

    def fetch_required_bundle(self) -> dict[str, dict[str, object]]:
        return {label: self.normalized_rate(label) for label in ["FED_RATE", "ECB_RATE", "US_2Y", "US_5Y", "US_10Y", "DE_2Y", "DE_5Y", "DE_10Y"]}

    def fetch_cross_asset_bundle(self) -> dict[str, dict[str, object]]:
        return {label: self.normalized_rate(label) for label in ["USD_PROXY", "GOLD", "EQUITY_PROXY", "VOLATILITY_PROXY"]}

    def _download(self, series_id: str) -> str:
        last_error: Exception | None = None
        for _ in range(self.max_retries + 1):
            try:
                with urlopen(FRED_GRAPH_CSV.format(series_id=series_id), timeout=self.timeout_seconds) as response:  # noqa: S310
                    return response.read().decode("utf-8")
            except URLError as exc:
                last_error = exc
        raise RuntimeError(f"FRED fetch failed for {series_id}: {last_error}")


class FredApiAdapter(FredCsvAdapter):
    provider_id = "FRED"
    api_key_env = "V3_FRED_API_KEY"
    timeout_seconds = 15
    max_retries = 1

    def key_present(self) -> bool:
        return bool(os.getenv(self.api_key_env, "").strip())

    def fetch_latest(self, label: str) -> FredSeriesPoint | dict[str, str]:
        if not self.key_present():
            return {"state": "UNAVAILABLE", "reason": "FRED_KEY_MISSING", "provider_id": self.provider_id}
        if label in self.unavailable_series:
            return {"state": "UNAVAILABLE", "reason": self.unavailable_series[label], "provider_id": self.provider_id}
        series_id = self.series[label]
        try:
            payload = self._download_api(series_id)
        except RuntimeError as exc:
            return {"state": "UNAVAILABLE", "reason": str(exc), "provider_id": self.provider_id, "series_id": series_id}
        latest: FredSeriesPoint | None = None
        for row in payload.get("observations", []):
            value = row.get("value")
            date = row.get("date")
            if not value or value == "." or not date:
                continue
            point = FredSeriesPoint(series_id, datetime.fromisoformat(date).replace(tzinfo=UTC), float(value), source=self.provider_id)
            if latest is None or point.timestamp > latest.timestamp:
                latest = point
        if latest is None:
            return {"state": "UNAVAILABLE", "reason": "NO_VALID_OBSERVATION", "provider_id": self.provider_id, "series_id": series_id}
        return latest

    def normalized_rate(self, label: str) -> dict[str, object]:
        record = super().normalized_rate(label)
        if record.get("state") == "UNAVAILABLE":
            return record
        timestamp = record.get("effective_timestamp") or record.get("timestamp")
        if isinstance(timestamp, str):
            source_time = datetime.fromisoformat(timestamp)
        else:
            source_time = datetime.now(UTC)
        retrieved_at = datetime.now(UTC)
        record.update(
            {
                "provider_id": self.provider_id,
                "series_id": self.series.get(label),
                "source_timestamp": source_time.isoformat(),
                "retrieved_at": retrieved_at.isoformat(),
                "staleness": (retrieved_at - source_time).total_seconds(),
                "no_lookahead": source_time <= retrieved_at,
            }
        )
        return record

    def _download_api(self, series_id: str) -> dict[str, object]:
        api_key = os.getenv(self.api_key_env, "").strip()
        query = urlencode(
            {
                "series_id": series_id,
                "api_key": api_key,
                "file_type": "json",
                "sort_order": "desc",
                "limit": 5000,
            }
        )
        request = Request(f"{FRED_API_OBSERVATIONS}?{query}", headers={"Accept": "application/json"})
        last_error: Exception | None = None
        for _ in range(self.max_retries + 1):
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310
                    return json.loads(response.read().decode("utf-8"))
            except (URLError, json.JSONDecodeError) as exc:
                last_error = exc
        raise RuntimeError(f"FRED_API_FETCH_FAILED:{series_id}:{type(last_error).__name__}")
