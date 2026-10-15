from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.v3_5 import live_adapter as live
from app.v3_5.health_dimensions import (
    CollectorHealth,
    FeedHealth,
    PipelineEligibility,
    QuoteAgeState,
    RejectionReason,
    ResolverHealth,
    SymbolActivity,
    TerminalHealth,
    assess_quote,
)
from app.v3_5.provenance import POLICY

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
LIMIT = float(POLICY["tick_max_age_seconds"])


def _assess(age: float, **kwargs):
    return assess_quote(observed_at=NOW, quote_timestamp=NOW - timedelta(seconds=age),
        bid=1.1, ask=1.1001, max_age_seconds=LIMIT, **kwargs)


def test_fresh_quote_is_quote_age_eligible_and_other_pipeline_stages_are_separate():
    result = _assess(1)
    assert result.quote_age_state is QuoteAgeState.FRESH
    assert result.quote_age_eligible is True
    assert result.rejection_reason is None
    assert result.signal_eligibility is PipelineEligibility.NOT_EVALUATED
    assert result.risk_eligibility is PipelineEligibility.NOT_EVALUATED
    assert result.execution_eligibility is PipelineEligibility.NOT_EVALUATED
    assert result.eurusd_activity is SymbolActivity.UNKNOWN


def test_exactly_five_seconds_preserves_inclusive_boundary():
    result = _assess(5)
    assert result.quote_age_state is QuoteAgeState.FRESH
    assert result.quote_age_eligible is True


def test_live_fresh_tick_keeps_exactly_five_second_quote(monkeypatch):
    events = []
    monkeypatch.setattr(live, "_utc_now", lambda: events.append("utc") or NOW)

    class Tick:
        time = 0
        time_msc = int((NOW.timestamp() + 10800 - 5) * 1000)
        bid = 1.1
        ask = 1.1001

    class Source:
        @staticmethod
        def symbol_info_tick(symbol):
            events.append("mt5_tick")
            return Tick()

    tick, acquired, normalized = live._fresh_tick(Source(), wait_seconds=0)
    assert tick is not None and acquired == NOW
    assert (acquired - normalized).total_seconds() == 5
    assert events == ["mt5_tick", "utc"]


def test_live_stale_rejection_emits_structured_diagnostic(monkeypatch, caplog):
    monkeypatch.setattr(live, "_utc_now", lambda: NOW)

    class Tick:
        time = 0
        time_msc = int((NOW.timestamp() + 10800 - 5.001) * 1000)
        bid = 1.1
        ask = 1.1001

    class Source:
        @staticmethod
        def symbol_info_tick(symbol):
            return Tick()

    with caplog.at_level("WARNING", logger="navora.v3_5.market_data_freshness"):
        try:
            live._fresh_tick(Source(), wait_seconds=0)
        except live.PreflightError as exc:
            assert exc.reason_code == "QUOTE_STALE"
            assert exc.diagnostic["quote_age_limit_seconds"] == 5
            assert exc.diagnostic["terminal_state"] == "UNKNOWN"
            assert exc.diagnostic["feed_state"] == "UNKNOWN"
            assert exc.diagnostic["collector_state"] == "UNKNOWN"
        else:
            raise AssertionError("stale quote unexpectedly passed the 5-second gate")
    records = [record for record in caplog.records if record.message == "market_data_quote_rejected"]
    assert len(records) == 1
    assert records[0].market_data_health["rejection_reason"] == "QUOTE_STALE"


def test_age_over_five_seconds_is_stale():
    result = _assess(5.001)
    assert result.quote_age_state is QuoteAgeState.STALE
    assert result.quote_age_eligible is False
    assert result.rejection_reason is RejectionReason.QUOTE_STALE


def test_ten_second_quote_stays_ineligible_when_controls_advance():
    result = _assess(10, terminal_state=TerminalHealth.HEALTHY,
        collector_state=CollectorHealth.HEALTHY,
        resolver_state=ResolverHealth.STOPPED,
        control_symbol_advancement={"GBPUSD": True, "USDJPY": True, "XAUUSD": True},
        eurusd_advanced=False)
    assert result.quote_age_state is QuoteAgeState.STALE
    assert result.quote_age_eligible is False
    assert result.feed_state is FeedHealth.CONTROL_ADVANCING
    assert result.eurusd_activity is SymbolActivity.QUIET
    assert result.terminal_state is TerminalHealth.HEALTHY
    assert result.collector_state is CollectorHealth.HEALTHY


def test_fresh_quote_does_not_hide_terminal_disconnect():
    result = _assess(1, terminal_state=TerminalHealth.DISCONNECTED)
    assert result.quote_age_state is QuoteAgeState.FRESH
    assert result.terminal_state is TerminalHealth.DISCONNECTED
    # This is quote-age eligibility only; existing terminal preflight remains
    # an independent fail-closed gate (covered in test_continuous_collector).
    assert result.signal_eligibility is PipelineEligibility.NOT_EVALUATED


def test_future_quote_is_rejected():
    result = assess_quote(observed_at=NOW, quote_timestamp=NOW + timedelta(milliseconds=1),
        bid=1.1, ask=1.1001, max_age_seconds=LIMIT)
    assert result.quote_age_state is QuoteAgeState.FUTURE
    assert result.quote_age_eligible is False
    assert result.rejection_reason is RejectionReason.QUOTE_FUTURE


def test_missing_quote_is_rejected():
    result = assess_quote(observed_at=NOW, quote_timestamp=None, bid=None, ask=None,
        max_age_seconds=LIMIT, quote_present=False)
    assert result.quote_age_state is QuoteAgeState.MISSING
    assert result.rejection_reason is RejectionReason.QUOTE_MISSING


def test_invalid_quote_is_rejected():
    result = assess_quote(observed_at=NOW, quote_timestamp=NOW, bid=1.2, ask=1.1,
        max_age_seconds=LIMIT)
    assert result.quote_age_state is QuoteAgeState.INVALID
    assert result.rejection_reason is RejectionReason.QUOTE_INVALID


def test_all_controls_quiet_is_diagnostic_only():
    result = _assess(10, control_symbol_advancement={
        "GBPUSD": False, "USDJPY": False, "XAUUSD": False})
    assert result.feed_state is FeedHealth.GLOBAL_QUIET
    assert result.quote_age_eligible is False
    assert result.rejection_reason is RejectionReason.QUOTE_STALE


def test_health_snapshot_serializes_without_losing_independent_states():
    result = _assess(10, control_symbol_advancement={"GBPUSD": True})
    value = result.to_dict()
    assert value["quote_age_state"] == "STALE"
    assert value["rejection_reason"] == "QUOTE_STALE"
    assert value["feed_state"] == "CONTROL_ADVANCING"
    assert value["control_symbol_advancement"] == {"GBPUSD": True}
    assert value["terminal_state"] == "UNKNOWN"


def test_preflight_reason_codes_keep_quote_and_system_failures_distinct():
    cases = {
        "EURUSD_TICK_UNAVAILABLE": "QUOTE_MISSING",
        "EURUSD_FUTURE_TICK": "QUOTE_FUTURE",
        "EURUSD_INVALID_BID_ASK": "QUOTE_INVALID",
        "EURUSD_TICK_STALE": "QUOTE_STALE",
        "CLOCK_UNSAFE_AT_STARTUP": "CLOCK_INVALID",
        "MT5_DISCONNECTED_AT_STARTUP": "TERMINAL_DISCONNECTED",
        "MT5_TICK_API_ERROR:RuntimeError": "TERMINAL_API_ERROR",
        "EURUSD_SYMBOL_NOT_VISIBLE": "SYMBOL_UNAVAILABLE",
        "SESSION_INVALID": "SESSION_INVALID",
        "OTHER_GUARD": "OTHER_EXISTING_SAFETY_REASON",
    }
    for message, expected in cases.items():
        assert live.PreflightError(message).reason_code == expected


def test_r6_analyzer_never_bridges_a_sampling_interruption(tmp_path):
    repo = Path(__file__).resolve().parents[3]
    analyzer_path = repo / "runtime/diagnostics/r6_analyze.py"
    spec = importlib.util.spec_from_file_location("r6_analyzer_test", analyzer_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)

    raw = tmp_path / "segmented.jsonl"
    header = {"record": "HEADER", "utc": NOW.isoformat(), "mono": 0,
              "duration_target_seconds": 1}

    def poll(stamp, mono, time_msc):
        symbols = {}
        for name in ("EURUSD", "GBPUSD", "USDJPY", "XAUUSD"):
            symbols[name] = {"error": None, "api_ms": 1.0, "age_seconds": 0.1,
                "replayed": False, "bid": 1.1, "ask": 1.1001,
                "advanced": True, "mono": mono, "time_msc": time_msc}
        return {"record": "POLL", "utc": stamp, "poll": int(mono), "mono": mono,
                "terminal_connected": True, "eurusd_unique": True, "symbols": symbols}

    rows = [header,
        poll(NOW.isoformat(), 1.0, 1_000),
        {"record": "SEGMENT_BREAK", "classification": "RECORDER_SAMPLING_INTERRUPTION_UNRESOLVED"},
        poll((NOW + timedelta(seconds=100)).isoformat(), 101.0, 101_000),
        {"record": "END", "utc": (NOW + timedelta(seconds=100)).isoformat(),
         "duration_seconds": 100, "status": "COMPLETED"}]
    raw.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    result = module.analyze(raw, tmp_path / "analysis")
    assert result["overall"]["COUNT"] == 0
    assert result["gaps_over_5"] == []
    assert result["stream_exceptions"]["sampling_discontinuities"] == 1
