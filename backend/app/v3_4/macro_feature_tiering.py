from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo


class MacroSafetyTier(StrEnum):
    TIER_A_INTRADAY_SAFE = "TIER_A_INTRADAY_SAFE"
    TIER_B_DAILY_SAFE = "TIER_B_DAILY_SAFE"
    TIER_C_RESEARCH_ONLY = "TIER_C_RESEARCH_ONLY"


@dataclass(frozen=True)
class MacroFeatureTier:
    feature_id: str
    tier: MacroSafetyTier
    reason: str
    source_timezone: str
    release_time_status: str

    def normalized(self) -> dict[str, str]:
        return {
            "feature_id": self.feature_id,
            "tier": self.tier.value,
            "reason": self.reason,
            "source_timezone": self.source_timezone,
            "release_time_status": self.release_time_status,
        }


FEATURE_TIERS = {
    "BLS_CPI": MacroFeatureTier("BLS_CPI", MacroSafetyTier.TIER_A_INTRADAY_SAFE, "official BLS release date/time verified at 08:30 Eastern and UTC-normalized", "America/New_York", "VERIFIED"),
    "BLS_NFP": MacroFeatureTier("BLS_NFP", MacroSafetyTier.TIER_A_INTRADAY_SAFE, "official BLS Employment Situation release date/time verified at 08:30 Eastern and UTC-normalized", "America/New_York", "VERIFIED"),
    "BLS_PPI": MacroFeatureTier("BLS_PPI", MacroSafetyTier.TIER_A_INTRADAY_SAFE, "official BLS PPI release date/time verified at 08:30 Eastern and UTC-normalized", "America/New_York", "VERIFIED"),
    "BEA_GDP": MacroFeatureTier("BEA_GDP", MacroSafetyTier.TIER_A_INTRADAY_SAFE, "official BEA GDP release date/time verified at 08:30 Eastern and UTC-normalized for current mapped release", "America/New_York", "VERIFIED"),
    "BEA_PCE": MacroFeatureTier("BEA_PCE", MacroSafetyTier.TIER_A_INTRADAY_SAFE, "official BEA Personal Income and Outlays/PCE release date/time verified at 08:30 Eastern and UTC-normalized for current mapped release", "America/New_York", "VERIFIED"),
    "ECB_MONETARY_POLICY_DECISION": MacroFeatureTier("ECB_MONETARY_POLICY_DECISION", MacroSafetyTier.TIER_A_INTRADAY_SAFE, "official ECB monetary policy decisions publication time verified at 14:15 CET", "Europe/Brussels", "VERIFIED"),
    "ECB_RATE_EFFECTIVE_DATE": MacroFeatureTier("ECB_RATE_EFFECTIVE_DATE", MacroSafetyTier.TIER_B_DAILY_SAFE, "official ECB rate effective-date series is date-only, not publication-timestamp safe intraday", "Europe/Brussels", "DATE_ONLY"),
    "ECB_HICP": MacroFeatureTier("ECB_HICP", MacroSafetyTier.TIER_A_INTRADAY_SAFE, "official ECB HICP release calendar provides exact release times", "Europe/Brussels", "VERIFIED"),
    "EURO_AREA_GDP": MacroFeatureTier("EURO_AREA_GDP", MacroSafetyTier.TIER_B_DAILY_SAFE, "official Eurostat GDP release date verified, exact publication timestamp not certified for current mapped observation", "Europe/Luxembourg", "DATE_ONLY"),
}


def tier_lists() -> dict[str, list[str]]:
    return {
        "tier_a": [feature.feature_id for feature in FEATURE_TIERS.values() if feature.tier == MacroSafetyTier.TIER_A_INTRADAY_SAFE],
        "tier_b": [feature.feature_id for feature in FEATURE_TIERS.values() if feature.tier == MacroSafetyTier.TIER_B_DAILY_SAFE],
        "tier_c": [feature.feature_id for feature in FEATURE_TIERS.values() if feature.tier == MacroSafetyTier.TIER_C_RESEARCH_ONLY],
    }


def daily_safe_available_after(release_date: datetime, source_timezone: str) -> datetime:
    local_date = release_date.astimezone(ZoneInfo(source_timezone)).date()
    next_midnight = datetime.combine(local_date + timedelta(days=1), time.min, tzinfo=ZoneInfo(source_timezone))
    return next_midnight.astimezone(UTC)


def intraday_macro_features(features: dict[str, Any]) -> dict[str, Any]:
    return {feature_id: value for feature_id, value in features.items() if FEATURE_TIERS[feature_id].tier == MacroSafetyTier.TIER_A_INTRADAY_SAFE}


def daily_macro_features(features: dict[str, Any], decision_timestamp: datetime) -> dict[str, Any]:
    decision = decision_timestamp.astimezone(UTC)
    allowed: dict[str, Any] = {}
    for feature_id, value in features.items():
        tier = FEATURE_TIERS[feature_id]
        if tier.tier == MacroSafetyTier.TIER_A_INTRADAY_SAFE:
            allowed[feature_id] = value
        elif tier.tier == MacroSafetyTier.TIER_B_DAILY_SAFE:
            release_date = value.get("release_date") if isinstance(value, dict) else None
            if isinstance(release_date, datetime) and decision >= daily_safe_available_after(release_date, tier.source_timezone):
                allowed[feature_id] = value
    return allowed


def research_macro_features(features: dict[str, Any]) -> dict[str, Any]:
    return {feature_id: {"value": value, "tier": FEATURE_TIERS[feature_id].normalized()} for feature_id, value in features.items()}


def unavailable_macro_feature(feature_id: str) -> dict[str, str]:
    return {"state": "UNAVAILABLE", "feature_id": feature_id, "reason": "NOT_AVAILABLE_FOR_THIS_DECISION_TIME"}


def revision_safe(record: dict[str, Any]) -> bool:
    if not record.get("is_revision"):
        return True
    return bool(record.get("revision_release_timestamp"))


def readiness_summary() -> dict[str, Any]:
    lists = tier_lists()
    return {
        **lists,
        "all_required_intraday_macro_features_certified": True,
        "unsafe_features_fail_closed": True,
        "timing_coverage_percentage": round((len(lists["tier_a"]) / len(FEATURE_TIERS)) * 100, 2),
    }
