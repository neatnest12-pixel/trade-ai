from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.v3_4.cross_asset_alignment import (
    asof_join,
    build_cross_asset_snapshot,
    coverage_audit,
    daily_market_session,
    gold_market_session,
    make_observation,
    mixed_frequency_feature_names,
)

BASE = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)


def test_asof_join_rejects_future_observation() -> None:
    future = make_observation("GOLD", "TEST", BASE + timedelta(seconds=1), 1.0, retrieved_at=BASE, native_frequency="TICK", market_session="FX_SESSION")
    joined = asof_join(BASE, [future])
    assert joined["state"] == "UNAVAILABLE"
    assert joined["reason"] == "NO_ASOF_OBSERVATION"


def test_asof_join_uses_latest_backward_observation_only() -> None:
    old = make_observation("GOLD", "TEST", BASE - timedelta(seconds=20), 1.0, retrieved_at=BASE, native_frequency="TICK", market_session="FX_SESSION")
    latest = make_observation("GOLD", "TEST", BASE - timedelta(seconds=5), 2.0, retrieved_at=BASE, native_frequency="TICK", market_session="FX_SESSION")
    joined = asof_join(BASE, [old, latest])
    assert joined["price"] == 2.0
    assert joined["no_lookahead"] is True
    assert joined["alignment_quality"] == "HIGH"


def test_staleness_marks_old_observation_unavailable() -> None:
    stale = make_observation("GOLD", "TEST", BASE - timedelta(hours=1), 1.0, retrieved_at=BASE, native_frequency="TICK", market_session="FX_SESSION")
    joined = asof_join(BASE, [stale])
    assert joined["state"] == "UNAVAILABLE"
    assert joined["alignment_quality"] == "UNAVAILABLE"


def test_mixed_frequency_features_are_separate() -> None:
    features = mixed_frequency_feature_names()
    assert "gold_return_5m" in features["intraday"]
    assert "usd_proxy_daily_change" in features["daily"]


def test_snapshot_requires_every_component_available() -> None:
    observations = {
        "USD_PROXY": [make_observation("USD_PROXY", "FRED", BASE - timedelta(days=1), 100.0, retrieved_at=BASE, native_frequency="DAILY", market_session="DAILY_CLOSE")],
        "GOLD": [make_observation("GOLD", "MT5", BASE - timedelta(seconds=10), 2000.0, retrieved_at=BASE, native_frequency="TICK", market_session="FX_SESSION")],
        "EQUITY_PROXY": [make_observation("EQUITY_PROXY", "FRED", BASE - timedelta(days=1), 5000.0, retrieved_at=BASE, native_frequency="DAILY", market_session="DAILY_CLOSE")],
        "VOLATILITY_PROXY": [make_observation("VOLATILITY_PROXY", "FRED", BASE - timedelta(days=1), 15.0, retrieved_at=BASE, native_frequency="DAILY", market_session="DAILY_CLOSE")],
    }
    snapshot = build_cross_asset_snapshot(BASE, observations)
    assert snapshot["cross_asset_complete"] is True


def test_coverage_audit_reports_quality_counts() -> None:
    observations = {"GOLD": [make_observation("GOLD", "MT5", BASE - timedelta(seconds=10), 2000.0, retrieved_at=BASE, native_frequency="TICK", market_session="FX_SESSION")]}
    report = coverage_audit([BASE], observations)
    assert report["total_decisions"] == 1
    assert report["instruments"]["GOLD"]["available"] == 1
    assert report["instruments"]["GOLD"]["HIGH"] == 1


def test_market_calendar_classification() -> None:
    assert daily_market_session(datetime(2026, 8, 22, 0, 0, tzinfo=UTC)) == "CLOSED_WEEKEND"
    assert gold_market_session(BASE) == "FX_SESSION"
