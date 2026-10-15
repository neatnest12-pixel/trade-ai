from __future__ import annotations

from datetime import UTC, datetime

from app.v3_4.macro_feature_tiering import (
    daily_macro_features,
    daily_safe_available_after,
    intraday_macro_features,
    readiness_summary,
    research_macro_features,
    revision_safe,
    tier_lists,
)


def test_tier_lists_classify_current_macro_features() -> None:
    tiers = tier_lists()
    assert "BLS_CPI" in tiers["tier_a"]
    assert "ECB_RATE_EFFECTIVE_DATE" in tiers["tier_b"]
    assert "EURO_AREA_GDP" in tiers["tier_b"]
    assert tiers["tier_c"] == []


def test_intraday_features_include_only_tier_a() -> None:
    features = {"BLS_CPI": 1, "ECB_RATE_EFFECTIVE_DATE": 2, "EURO_AREA_GDP": 3}
    assert intraday_macro_features(features) == {"BLS_CPI": 1}


def test_daily_safe_boundary_is_after_source_date_completes() -> None:
    release = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
    boundary = daily_safe_available_after(release, "Europe/Brussels")
    assert boundary > release
    features = {"ECB_RATE_EFFECTIVE_DATE": {"release_date": release}, "BLS_CPI": 1}
    assert "ECB_RATE_EFFECTIVE_DATE" not in daily_macro_features(features, release)
    assert "ECB_RATE_EFFECTIVE_DATE" in daily_macro_features(features, boundary)


def test_research_features_keep_all_tiers_with_labels() -> None:
    features = {"BLS_CPI": 1, "ECB_RATE_EFFECTIVE_DATE": 2}
    research = research_macro_features(features)
    assert research["ECB_RATE_EFFECTIVE_DATE"]["tier"]["tier"] == "TIER_B_DAILY_SAFE"


def test_revision_safety_requires_revision_release_timestamp() -> None:
    assert revision_safe({"is_revision": False}) is True
    assert revision_safe({"is_revision": True}) is False
    assert revision_safe({"is_revision": True, "revision_release_timestamp": "2026-08-21T12:00:00+00:00"}) is True


def test_readiness_summary_replaces_artificial_coverage_gate() -> None:
    summary = readiness_summary()
    assert summary["all_required_intraday_macro_features_certified"] is True
    assert summary["unsafe_features_fail_closed"] is True
    assert summary["timing_coverage_percentage"] < 90
