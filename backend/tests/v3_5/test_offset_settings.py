from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.core.mt5_offset import configured_offset_seconds


@pytest.fixture(autouse=True)
def clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_offset_setting_is_required_without_host_timezone_inference(monkeypatch):
    monkeypatch.delenv("V3_MT5_SERVER_UTC_OFFSET_SECONDS", raising=False)
    settings = Settings(_env_file=None)
    with pytest.raises(RuntimeError, match="V3_MT5_SERVER_UTC_OFFSET_SECONDS_REQUIRED"):
        configured_offset_seconds(settings)


@pytest.mark.parametrize("value", ["not-a-number", "50401", "-50401"])
def test_offset_setting_rejects_invalid_or_implausible_values(monkeypatch, value):
    monkeypatch.setenv("V3_MT5_SERVER_UTC_OFFSET_SECONDS", value)
    with pytest.raises((RuntimeError, ValidationError)):
        configured_offset_seconds(Settings(_env_file=None))


def test_offset_setting_accepts_explicit_valid_value(monkeypatch):
    monkeypatch.setenv("V3_MT5_SERVER_UTC_OFFSET_SECONDS", "10800")
    settings = Settings(_env_file=None)
    assert settings.v3_mt5_server_utc_offset_seconds == 10800
    assert configured_offset_seconds(settings) == 10800


def test_preflight_and_both_workers_share_canonical_offset(monkeypatch):
    monkeypatch.setenv("V3_MT5_SERVER_UTC_OFFSET_SECONDS", "10800")
    from app.v3_5 import live_adapter, outcome_resolver

    assert configured_offset_seconds(Settings(_env_file=None)) == 10800
    assert live_adapter._broker_offset_seconds() == 10800
    assert outcome_resolver._broker_offset_seconds() == 10800

    root = Path(__file__).resolve().parents[3]
    preflight = (root / "backend/scripts/v3_4_utc_preflight.py").read_text(encoding="utf8")
    collector_launcher = (root / "backend/scripts/v3_5_eurusd_continuous_collector.py").read_text(encoding="utf8")
    resolver_launcher = (root / "backend/scripts/v3_5_eurusd_outcome_resolver.py").read_text(encoding="utf8")
    assert "offset_seconds = configured_offset_seconds()" in preflight
    assert "from app.core.mt5_offset import configured_offset_seconds" in preflight
    assert "_broker_offset_seconds()" in collector_launcher
    assert "_broker_offset_seconds()" in resolver_launcher


def test_v35_workers_fail_closed_on_policy_offset_mismatch(monkeypatch):
    monkeypatch.setenv("V3_MT5_SERVER_UTC_OFFSET_SECONDS", "7200")
    from app.v3_5 import live_adapter, outcome_resolver

    with pytest.raises(RuntimeError, match="V35_BROKER_OFFSET_POLICY_MISMATCH"):
        live_adapter._broker_offset_seconds()
    with pytest.raises(RuntimeError, match="V35_BROKER_OFFSET_POLICY_MISMATCH"):
        outcome_resolver._broker_offset_seconds()
