from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import UTC, datetime
from io import StringIO
from urllib.error import URLError
from urllib.request import Request, urlopen

from app.v3_4.providers.contracts import yield_record

BUNDESBANK_CSV = "https://api.statistiken.bundesbank.de/rest/data/BBSSY/{series_path}?format=csv&lang=en"


@dataclass(frozen=True)
class BundesbankSeriesPoint:
    label: str
    series_id: str
    timestamp: datetime
    value: float
    source: str = "bundesbank_public_csv"


class BundesbankYieldAdapter:
    provider_id = "bundesbank_public_csv"
    timeout_seconds = 15
    max_retries = 1
    series = {
        "DE_2Y": "D.REN.EUR.A610.000000WT0202.A",
        "DE_5Y": "D.REN.EUR.A620.000000WT0505.A",
    }

    def fetch_latest(self, label: str) -> BundesbankSeriesPoint | dict[str, str]:
        series_path = self.series[label]
        text = self._download(series_path)
        reader = csv.reader(StringIO(text.lstrip("\ufeff")))
        latest: BundesbankSeriesPoint | None = None
        for row in reader:
            if len(row) < 2:
                continue
            date_text, value_text = row[0].strip().strip('"'), row[1].strip()
            try:
                timestamp = datetime.fromisoformat(date_text).replace(tzinfo=UTC)
                value = float(value_text)
            except ValueError:
                continue
            latest = BundesbankSeriesPoint(label, series_path, timestamp, value)
        if latest is None:
            return {"state": "UNAVAILABLE", "reason": "NO_VALID_OBSERVATION", "provider_id": self.provider_id, "series_id": series_path}
        return latest

    def normalized_yield(self, label: str) -> dict[str, object]:
        point = self.fetch_latest(label)
        if isinstance(point, dict):
            return point
        return yield_record(label, point.timestamp, point.value, self.provider_id)

    def fetch_required_bundle(self) -> dict[str, dict[str, object]]:
        return {label: self.normalized_yield(label) for label in ("DE_2Y", "DE_5Y")}

    def _download(self, series_path: str) -> str:
        last_error: Exception | None = None
        for _ in range(self.max_retries + 1):
            try:
                request = Request(BUNDESBANK_CSV.format(series_path=series_path), headers={"Accept": "text/csv"})
                with urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310
                    return response.read().decode("utf-8-sig", "ignore")
            except URLError as exc:
                last_error = exc
        raise RuntimeError(f"Bundesbank fetch failed for {series_path}: {last_error}")
