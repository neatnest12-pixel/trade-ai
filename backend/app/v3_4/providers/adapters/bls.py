from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from app.v3_4.providers.contracts import Quality

BLS_API_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"


@dataclass(frozen=True)
class BlsSeriesConfig:
    label: str
    series_id: str
    economic_category: str


@dataclass(frozen=True)
class BlsObservation:
    provider: str
    series_id: str
    observation_period: str
    year: str
    period: str
    value: float
    economic_category: str
    retrieved_at: datetime
    release_timestamp: datetime | None = None
    revision: str | None = None
    footnotes: tuple[str, ...] = ()

    def normalized(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "series_id": self.series_id,
            "observation_period": self.observation_period,
            "year": self.year,
            "period": self.period,
            "value": self.value,
            "release_timestamp": self.release_timestamp.isoformat() if self.release_timestamp else None,
            "retrieved_at": self.retrieved_at.isoformat(),
            "revision": self.revision,
            "footnotes": list(self.footnotes),
            "data_quality": Quality.VERIFIED.value,
            "staleness": None if self.release_timestamp is None else (self.retrieved_at - self.release_timestamp).total_seconds(),
            "currency": "USD",
            "economic_category": self.economic_category,
            "consensus_forecast": "UNAVAILABLE",
            "surprise_vs_consensus": "UNAVAILABLE",
            "release_timestamp_provenance": "UNAVAILABLE_FROM_BLS_TIMESERIES_ENDPOINT" if self.release_timestamp is None else "BLS",
            "no_lookahead_safe_for_decision_join": self.release_timestamp is not None,
        }


class BlsApiAdapter:
    provider_id = "BLS"
    api_key_env = "V3_BLS_API_KEY"
    timeout_seconds = 20
    max_retries = 1
    series = {
        "CPI": BlsSeriesConfig("CPI", "CUUR0000SA0", "CPI_HEADLINE"),
        "CORE_CPI": BlsSeriesConfig("CORE_CPI", "CUUR0000SA0L1E", "CPI_CORE"),
        "NONFARM_PAYROLL": BlsSeriesConfig("NONFARM_PAYROLL", "CES0000000001", "EMPLOYMENT"),
        "UNEMPLOYMENT": BlsSeriesConfig("UNEMPLOYMENT", "LNS14000000", "EMPLOYMENT"),
        "LABOR_FORCE_PARTICIPATION": BlsSeriesConfig("LABOR_FORCE_PARTICIPATION", "LNS11300000", "EMPLOYMENT"),
        "AVERAGE_HOURLY_EARNINGS": BlsSeriesConfig("AVERAGE_HOURLY_EARNINGS", "CES0500000003", "WAGES"),
        "PPI": BlsSeriesConfig("PPI", "WPUFD4", "PRODUCER_INFLATION"),
    }

    def key_present(self) -> bool:
        return bool(os.getenv(self.api_key_env, "").strip())

    def fetch_latest(self, label: str) -> dict[str, Any]:
        if not self.key_present():
            return {"state": "UNAVAILABLE", "reason": "BLS_KEY_MISSING", "provider": self.provider_id}
        config = self.series[label]
        try:
            payload = self._download([config.series_id])
        except RuntimeError as exc:
            return {"state": "UNAVAILABLE", "reason": str(exc), "provider": self.provider_id, "series_id": config.series_id}
        records = self._parse_payload(payload, config)
        if not records:
            return {"state": "UNAVAILABLE", "reason": "NO_VALID_OBSERVATION", "provider": self.provider_id, "series_id": config.series_id}
        return records[0].normalized()

    def fetch_bundle(self) -> dict[str, dict[str, Any]]:
        return {label: self.fetch_latest(label) for label in self.series}

    def _download(self, series_ids: list[str]) -> dict[str, Any]:
        api_key = os.getenv(self.api_key_env, "").strip()
        payload = {
            "seriesid": series_ids,
            "registrationkey": api_key,
            "latest": "true",
        }
        request = Request(BLS_API_URL, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json", "Accept": "application/json"}, method="POST")
        last_error: Exception | None = None
        for _ in range(self.max_retries + 1):
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310
                    data = json.loads(response.read().decode("utf-8"))
                    if data.get("status") != "REQUEST_SUCCEEDED":
                        raise RuntimeError(f"BLS_REQUEST_FAILED:{data.get('status')}")
                    return data
            except (URLError, json.JSONDecodeError, RuntimeError) as exc:
                last_error = exc
        raise RuntimeError(f"BLS_FETCH_FAILED:{type(last_error).__name__}")

    def _parse_payload(self, payload: dict[str, Any], config: BlsSeriesConfig) -> list[BlsObservation]:
        retrieved_at = datetime.now(UTC)
        series_items = payload.get("Results", {}).get("series", [])
        for item in series_items:
            if item.get("seriesID") != config.series_id:
                continue
            records: list[BlsObservation] = []
            for raw in item.get("data", []):
                period = raw.get("period")
                year = raw.get("year")
                value = raw.get("value")
                if not period or not year or value is None or period == "M13":
                    continue
                try:
                    numeric_value = float(value)
                except (TypeError, ValueError):
                    continue
                footnotes = tuple(note.get("text", "") for note in raw.get("footnotes", []) if note.get("text"))
                records.append(
                    BlsObservation(
                        provider=self.provider_id,
                        series_id=config.series_id,
                        observation_period=f"{year}-{period}",
                        year=str(year),
                        period=str(period),
                        value=numeric_value,
                        economic_category=config.economic_category,
                        retrieved_at=retrieved_at,
                        revision="REVISED_OR_SPECIAL" if footnotes else None,
                        footnotes=footnotes,
                    )
                )
            return sorted(records, key=lambda rec: (rec.year, rec.period), reverse=True)
        return []


def asof_available(record: dict[str, Any], decision_timestamp: datetime) -> bool:
    release_timestamp = record.get("release_timestamp")
    if not release_timestamp:
        return False
    release = datetime.fromisoformat(str(release_timestamp))
    if release.tzinfo is None:
        release = release.replace(tzinfo=UTC)
    return release <= decision_timestamp.astimezone(UTC)
