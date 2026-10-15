from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import UTC, datetime
from io import StringIO
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from app.v3_4.providers.contracts import Quality

ECB_API_CSV = "https://data-api.ecb.europa.eu/service/data/{flow}/{key}?lastNObservations=1&format=csvdata"


@dataclass(frozen=True)
class EcbSeriesConfig:
    label: str
    flow: str
    key: str
    economic_category: str


class EcbApiAdapter:
    provider_id = "ECB"
    timeout_seconds = 20
    max_retries = 1
    series = {
        "ECB_RATE": EcbSeriesConfig("ECB_RATE", "FM", "D.U2.EUR.4F.KR.DFR.LEV", "ECB_POLICY_RATE"),
        "EURO_HICP": EcbSeriesConfig("EURO_HICP", "ICP", "M.U2.N.000000.4.ANR", "EURO_HICP"),
        "EURO_CORE_INFLATION": EcbSeriesConfig("EURO_CORE_INFLATION", "ICP", "M.U2.N.XEF000.4.ANR", "EURO_CORE_INFLATION"),
        "EURO_GDP": EcbSeriesConfig("EURO_GDP", "MNA", "Q.Y.I9.W2.S1.S1.B.B1GQ._Z._Z._Z.EUR.LR.N", "EURO_GDP"),
    }

    def fetch_latest(self, label: str) -> dict[str, Any]:
        config = self.series[label]
        try:
            text = self._download(config)
        except RuntimeError as exc:
            return {"state": "UNAVAILABLE", "reason": str(exc), "provider": self.provider_id, "series_id": f"{config.flow}.{config.key}"}
        reader = csv.DictReader(StringIO(text))
        latest = None
        for row in reader:
            if row.get("OBS_VALUE") not in (None, "") and row.get("TIME_PERIOD"):
                latest = row
        if latest is None:
            return {"state": "UNAVAILABLE", "reason": "NO_VALID_OBSERVATION", "provider": self.provider_id, "series_id": f"{config.flow}.{config.key}"}
        retrieved_at = datetime.now(UTC)
        return {
            "provider": self.provider_id,
            "series_id": f"{config.flow}.{config.key}",
            "observation_period": latest["TIME_PERIOD"],
            "value": float(latest["OBS_VALUE"]),
            "effective_or_release_timestamp": f"{latest['TIME_PERIOD']}T00:00:00+00:00" if len(latest["TIME_PERIOD"]) == 10 else None,
            "retrieved_at": retrieved_at.isoformat(),
            "quality": Quality.VERIFIED.value,
            "staleness": None,
            "economic_category": config.economic_category,
            "release_time_status": "DATE_ONLY",
            "decision_time_safe": False,
        }

    def fetch_bundle(self) -> dict[str, dict[str, Any]]:
        return {label: self.fetch_latest(label) for label in self.series}

    def _download(self, config: EcbSeriesConfig) -> str:
        request = Request(
            ECB_API_CSV.format(flow=config.flow, key=config.key),
            headers={"Accept": "text/csv"},
        )
        last_error: Exception | None = None
        for _ in range(self.max_retries + 1):
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310
                    return response.read().decode("utf-8-sig", "ignore")
            except URLError as exc:
                last_error = exc
        raise RuntimeError(f"ECB_FETCH_FAILED:{type(last_error).__name__}") from last_error
