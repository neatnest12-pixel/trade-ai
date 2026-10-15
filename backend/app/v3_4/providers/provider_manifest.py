from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class DataDomain(StrEnum):
    BROKER_COSTS = "BROKER_COSTS"
    EXECUTION_QUALITY = "EXECUTION_QUALITY"
    MACRO_CALENDAR = "MACRO_CALENDAR"
    CENTRAL_BANK_RATES = "CENTRAL_BANK_RATES"
    GOVERNMENT_YIELDS = "GOVERNMENT_YIELDS"
    CROSS_ASSET_MARKET_DATA = "CROSS_ASSET_MARKET_DATA"
    FX_FUTURES = "FX_FUTURES"
    COT_POSITIONING = "COT_POSITIONING"
    VALUE_MACRO = "VALUE_MACRO"


class ProviderQuality(StrEnum):
    VERIFIED = "VERIFIED"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNAVAILABLE = "UNAVAILABLE"


PRIORITY = {
    DataDomain.BROKER_COSTS: "P0",
    DataDomain.EXECUTION_QUALITY: "P0",
    DataDomain.MACRO_CALENDAR: "P1",
    DataDomain.CENTRAL_BANK_RATES: "P1",
    DataDomain.GOVERNMENT_YIELDS: "P1",
    DataDomain.CROSS_ASSET_MARKET_DATA: "P1",
    DataDomain.FX_FUTURES: "P2",
    DataDomain.COT_POSITIONING: "P2",
    DataDomain.VALUE_MACRO: "P2",
}


@dataclass(frozen=True)
class ProviderFeed:
    provider_id: str
    provider_type: str
    data_domains: tuple[DataDomain, ...]
    symbols_or_currencies: tuple[str, ...]
    historical_support: bool
    live_support: bool
    timestamp_semantics: str
    timezone: str
    staleness_limit_seconds: int
    rate_limits: str
    authentication_required: bool
    cost_tier: str
    provenance_quality: ProviderQuality
    fallback_policy: str = "return explicit unavailable"
    fail_closed_behavior: str = "never fabricate, zero-fill, or exceed staleness limit"
    real_data_connected: bool = False


def provider_manifest() -> list[dict[str, Any]]:
    feeds = [
        ProviderFeed("mt5_icmarkets_symbol_spec", "BROKER_TERMINAL", (DataDomain.BROKER_COSTS,), ("EURUSD",), True, True, "broker server timestamp normalized by adapter", "UTC-normalized", 5, "terminal-bound", False, "existing", ProviderQuality.VERIFIED, real_data_connected=True),
        ProviderFeed("future_demo_execution_journal", "INTERNAL_RESEARCH", (DataDomain.EXECUTION_QUALITY,), ("EURUSD",), True, False, "event timestamp", "UTC", 0, "n/a", False, "internal", ProviderQuality.UNAVAILABLE),
        ProviderFeed("macro_calendar_provider_required", "EXTERNAL_API", (DataDomain.MACRO_CALENDAR,), ("USD", "EUR"), True, True, "official release timestamp", "UTC", 86400, "provider-specific", True, "unknown", ProviderQuality.UNAVAILABLE),
        ProviderFeed("central_bank_rates_provider_required", "EXTERNAL_API", (DataDomain.CENTRAL_BANK_RATES,), ("USD", "EUR"), True, True, "effective timestamp", "UTC", 86400, "provider-specific", True, "unknown", ProviderQuality.UNAVAILABLE),
        ProviderFeed("government_yields_provider_required", "EXTERNAL_API", (DataDomain.GOVERNMENT_YIELDS,), ("US2Y", "US5Y", "US10Y", "DE2Y", "DE5Y", "DE10Y"), True, True, "market timestamp", "UTC", 900, "provider-specific", True, "unknown", ProviderQuality.UNAVAILABLE),
        ProviderFeed("cross_asset_provider_required", "EXTERNAL_API", (DataDomain.CROSS_ASSET_MARKET_DATA,), ("DXY", "XAUUSD", "SPX", "VIX"), True, True, "market timestamp", "UTC", 300, "provider-specific", True, "unknown", ProviderQuality.UNAVAILABLE),
        ProviderFeed("fx_futures_provider_required", "EXTERNAL_API", (DataDomain.FX_FUTURES,), ("6E",), True, True, "exchange timestamp", "UTC", 900, "provider-specific", True, "unknown", ProviderQuality.UNAVAILABLE),
        ProviderFeed("cot_provider_required", "EXTERNAL_API", (DataDomain.COT_POSITIONING,), ("EUR", "USD"), True, False, "release availability timestamp", "UTC", 604800, "weekly", True, "unknown", ProviderQuality.UNAVAILABLE),
        ProviderFeed("value_macro_provider_required", "EXTERNAL_API", (DataDomain.VALUE_MACRO,), ("USD", "EUR"), True, False, "source publication timestamp", "UTC", 2592000, "provider-specific", True, "unknown", ProviderQuality.UNAVAILABLE),
    ]
    return [{**asdict(feed), "data_domains": [d.value for d in feed.data_domains], "provenance_quality": feed.provenance_quality.value, "priority": PRIORITY[feed.data_domains[0]]} for feed in feeds]


def readiness_report() -> list[dict[str, Any]]:
    report = []
    for item in provider_manifest():
        priority = item["priority"]
        real = bool(item["real_data_connected"])
        quality = item["provenance_quality"]
        report.append(
            {
                "domain": item["data_domains"][0],
                "provider_id": item["provider_id"],
                "ENGINE_READY": True,
                "REAL_DATA_CONNECTED": real,
                "QUALITY": quality,
                "P0/P1_BLOCKER": priority in {"P0", "P1"} and not real,
                "priority": priority,
            }
        )
    return report


def fail_closed_check(record: dict[str, Any]) -> bool:
    if record.get("state") == "UNAVAILABLE":
        return record.get("quality") == ProviderQuality.UNAVAILABLE.value
    return True
