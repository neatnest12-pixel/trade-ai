from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from app.v3_4.providers.contracts import Quality

BEA_API_URL = "https://apps.bea.gov/api/data/"


@dataclass(frozen=True)
class BeaSeriesConfig:
    label: str
    dataset: str
    table_name: str
    line_number: str
    frequency: str
    economic_category: str


class BeaApiAdapter:
    provider_id = "BEA"
    api_key_env = "V3_BEA_API_KEY"
    timeout_seconds = 20
    series = {
        "GDP": BeaSeriesConfig("GDP", "NIPA", "T10105", "1", "Q", "GDP"),
        "REAL_GDP": BeaSeriesConfig("REAL_GDP", "NIPA", "T10106", "1", "Q", "REAL_GDP"),
        "PCE": BeaSeriesConfig("PCE", "NIPA", "T20804", "1", "M", "PCE"),
        "CORE_PCE": BeaSeriesConfig("CORE_PCE", "NIPA", "T20804", "2", "M", "CORE_PCE"),
        "PERSONAL_INCOME": BeaSeriesConfig("PERSONAL_INCOME", "NIPA", "T20600", "1", "M", "PERSONAL_INCOME"),
        "CORPORATE_PROFITS": BeaSeriesConfig("CORPORATE_PROFITS", "NIPA", "T11600", "1", "Q", "CORPORATE_PROFITS"),
    }

    def key_present(self) -> bool:
        return bool(os.getenv(self.api_key_env, "").strip())

    def fetch_latest(self, label: str) -> dict[str, Any]:
        if not self.key_present():
            return {"state": "UNAVAILABLE", "reason": "BEA_KEY_MISSING", "provider": self.provider_id}
        config = self.series[label]
        try:
            payload = self._download(config)
        except RuntimeError as exc:
            return {"state": "UNAVAILABLE", "reason": str(exc), "provider": self.provider_id, "series_id": config.table_name}
        api_error = payload.get("BEAAPI", {}).get("Results", {}).get("Error")
        if api_error:
            return {
                "state": "UNAVAILABLE",
                "reason": f"BEA_API_ERROR:{api_error.get('APIErrorDescription', 'UNKNOWN')}",
                "provider": self.provider_id,
                "series_id": config.table_name,
            }
        rows = payload.get("BEAAPI", {}).get("Results", {}).get("Data", [])
        valid_rows = []
        for row in rows:
            if str(row.get("LineNumber")) != config.line_number:
                continue
            valid_rows.append(row)
        if not valid_rows:
            return {"state": "UNAVAILABLE", "reason": "NO_VALID_OBSERVATION", "provider": self.provider_id, "series_id": config.table_name}
        ordered_rows = sorted(valid_rows, key=lambda row: str(row.get("TimePeriod", "")))
        latest = ordered_rows[-1]
        retrieved_at = datetime.now(UTC)
        value_text = str(latest.get("DataValue", "")).replace(",", "")
        try:
            value = float(value_text)
        except ValueError:
            return {"state": "UNAVAILABLE", "reason": "INVALID_VALUE", "provider": self.provider_id, "series_id": config.table_name}
        return {
            "provider": self.provider_id,
            "dataset": config.dataset,
            "table_or_series_id": config.table_name,
            "line_number": config.line_number,
            "observation_period": latest.get("TimePeriod"),
            "historical_start": ordered_rows[0].get("TimePeriod"),
            "observation_count": len(ordered_rows),
            "value": value,
            "retrieved_at": retrieved_at.isoformat(),
            "release_timestamp": None,
            "release_timestamp_provenance": "RELEASE_TIMESTAMP_UNAVAILABLE",
            "revision_vintage_metadata": latest.get("CL_UNIT") or "UNAVAILABLE",
            "quality": Quality.VERIFIED.value,
            "staleness": None,
            "economic_category": config.economic_category,
            "decision_time_safe": False,
        }

    def fetch_bundle(self) -> dict[str, dict[str, Any]]:
        return {label: self.fetch_latest(label) for label in self.series}

    def _download(self, config: BeaSeriesConfig) -> dict[str, Any]:
        query = urlencode(
            {
                "UserID": os.getenv(self.api_key_env, "").strip(),
                "method": "GetData",
                "datasetname": config.dataset,
                "TableName": config.table_name,
                "Frequency": config.frequency,
                "Year": "X",
                "ResultFormat": "JSON",
            }
        )
        try:
            with urlopen(f"{BEA_API_URL}?{query}", timeout=self.timeout_seconds) as response:  # noqa: S310
                return json.loads(response.read().decode("utf-8"))
        except (URLError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"BEA_FETCH_FAILED:{type(exc).__name__}") from exc
