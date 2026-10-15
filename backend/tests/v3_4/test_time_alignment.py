from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.v3_4.mt5_time import (
    TIME_NORMALIZATION_VERSION,
    ClockSkewError,
    CompletedCandleError,
    MarketDataStaleError,
    normalize_mt5_epoch,
    require_completed_candle,
    require_not_future,
    require_recent,
)
from scripts.v3_prospective_collector import validate_observation_time_safety


def test_mt5_server_epoch_is_normalized_by_explicit_certified_offset() -> None:
    trusted = datetime(2026, 9, 4, 14, 11, 24, tzinfo=UTC)
    raw_epoch = trusted.timestamp() + 10_800

    result = normalize_mt5_epoch(
        raw_epoch,
        offset_seconds=10_800,
        source="EURUSD_SYMBOL_INFO_TICK",
    )

    assert result.timestamp == trusted
    assert result.raw_timestamp == trusted + timedelta(seconds=10_800)
    assert result.normalization == TIME_NORMALIZATION_VERSION


def test_market_closed_stale_tick_does_not_change_explicit_offset() -> None:
    trusted_now = datetime(2026, 9, 7, 9, tzinfo=UTC)
    friday_tick = datetime(2026, 9, 4, 20, 59, tzinfo=UTC)
    raw_epoch = friday_tick.timestamp() + 10_800

    result = normalize_mt5_epoch(
        raw_epoch,
        offset_seconds=10_800,
        source="EURUSD_SYMBOL_INFO_TICK",
    )

    assert result.timestamp == friday_tick
    assert (trusted_now - result.timestamp).total_seconds() > 48 * 60 * 60
    with pytest.raises(MarketDataStaleError):
        require_recent(
            result.timestamp,
            system_utc=trusted_now,
            source="EURUSD_SYMBOL_INFO_TICK",
        )


def test_clock_skew_guard_rejects_future_market_time() -> None:
    trusted = datetime(2026, 9, 4, 14, tzinfo=UTC)
    with pytest.raises(ClockSkewError) as caught:
        require_not_future(
            trusted + timedelta(seconds=6),
            system_utc=trusted,
            source="EURUSD_M1_COMPLETION",
        )
    assert caught.value.as_dict()["code"] == "CLOCK_SKEW_DETECTED"


@pytest.mark.parametrize("timeframe,minutes", [
    ("M1", 1),
    ("M5", 5),
    ("M15", 15),
    ("H1", 60),
    ("H6", 360),
    ("H12", 720),
])
def test_completed_candle_safety(timeframe: str, minutes: int) -> None:
    decision = datetime(2026, 9, 4, 14, tzinfo=UTC)
    require_completed_candle(
        decision - timedelta(minutes=minutes),
        timeframe=timeframe,
        decision_timestamp=decision,
    )
    with pytest.raises(CompletedCandleError):
        require_completed_candle(
            decision - timedelta(minutes=minutes) + timedelta(seconds=1),
            timeframe=timeframe,
            decision_timestamp=decision,
        )


def test_observation_guard_rejects_future_context_and_accepts_safe_row() -> None:
    decision = datetime(2026, 9, 4, 14, tzinfo=UTC)
    row = {
        "decision_timestamp": decision.isoformat(),
        "completed_m1_timestamp": (decision - timedelta(minutes=1)).isoformat(),
        "tick_timestamp": decision.isoformat(),
        "completed_context": {
            "M1": {
                "available": True,
                "completion_at_or_before": (decision - timedelta(minutes=1)).isoformat(),
            },
            "H1": {
                "available": True,
                "completion_at_or_before": (decision - timedelta(hours=1)).isoformat(),
            },
        },
    }
    validate_observation_time_safety(row, trusted_utc=decision)
    row["completed_context"]["H1"]["completion_at_or_before"] = (
        decision - timedelta(minutes=59)
    ).isoformat()
    with pytest.raises(CompletedCandleError):
        validate_observation_time_safety(row, trusted_utc=decision)


def test_restart_procedure_sets_safety_and_single_collector_guards() -> None:
    source = Path("ops/windows/v3-4-collector-restart.ps1").read_text(encoding="utf8")
    for required in (
        'PYTHONPATH = Join-Path $repoRoot "backend"',
        'APP_ENV = "paper"',
        'MT5_READ_ONLY = "true"',
        'TRADING_EXECUTION_ENABLED = "false"',
        'DEMO_EXECUTION_ENABLED = "false"',
        'REAL_EXECUTION_ENABLED = "false"',
        'V3_COLLECTOR_ONCE = if',
        'V3_MT5_SERVER_UTC_OFFSET_SECONDS',
        'COLLECTOR_ALREADY_RUNNING',
    ):
        assert required in source
    assert "order_send(" not in source
    assert "fit_model(" not in source


def test_collector_retains_heartbeat_and_no_lookahead_guards() -> None:
    source = Path("backend/scripts/v3_prospective_collector.py").read_text(encoding="utf8")
    assert 'last_heartbeat=now().isoformat()' in source
    assert "validate_observation_time_safety(row, trusted_utc=now())" in source
    assert "_fetch_decision_tick" in source
    assert "order_send(" not in source
    assert "fit_model(" not in source
