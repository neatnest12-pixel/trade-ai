from datetime import UTC, datetime, timedelta

import pytest

from app.v3_5.trusted_clock_experiment import (
    BROKER_OFFSET_SECONDS,
    NtpMeasurement,
    TrustedClock,
    TrustedClockError,
    production_age_status,
)

T = datetime(2026, 10, 5, 14, 0, 0, tzinfo=UTC)


def ntp(*, offset=.39, measured=T, samples=None, network=.003, dispersion=.003,
        sign="trusted_minus_host"):
    return NtpMeasurement.from_samples(
        source="fixture-ntp", measured_at_utc=measured,
        sample_offsets_seconds=samples or (offset-.001, offset, offset+.001),
        network_uncertainty_seconds=network,
        root_dispersion_seconds=dispersion,
        offset_definition=sign,
    )


def raw_for(normalized):
    return normalized.timestamp() + BROKER_OFFSET_SECONDS


def validate(*, normalized, clock=None, measurement=None, host=T, now=T, **kwargs):
    return (clock or TrustedClock()).validate_tick(
        raw_market_epoch_seconds=raw_for(normalized),
        host_acquisition_utc=host, now_host_utc=now,
        ntp=measurement or ntp(measured=now), **kwargs,
    )


def test_host_skew_false_future_is_accepted_after_one_acquisition_correction():
    market = T + timedelta(seconds=.10)
    result = validate(normalized=market)
    assert production_age_status(result.production_host_age_seconds) == "FUTURE_TICK"
    assert result.production_host_age_seconds == pytest.approx(-.10)
    assert result.trusted_age_seconds == pytest.approx(.29)
    assert result.status == "ACCEPT"
    assert result.normalized_market_utc == market  # NTP never changes market time
    assert (result.acquisition.trusted_acquisition_utc
            - result.acquisition.raw_host_acquisition_utc).total_seconds() == pytest.approx(.39)


def test_broker_normalization_is_once_and_wrong_offset_fails_closed():
    market = T - timedelta(seconds=2)
    result = validate(normalized=market)
    assert result.normalized_market_utc == market
    assert result.raw_market_epoch_seconds == pytest.approx(raw_for(market))
    with pytest.raises(TrustedClockError, match="BROKER_OFFSET_MISMATCH"):
        validate(normalized=market, broker_offset_seconds=10800.001)


def test_genuine_future_tick_still_rejected():
    result = validate(normalized=T + timedelta(seconds=.40), measurement=ntp(offset=.39))
    assert result.trusted_age_seconds < 0
    assert result.status == "FUTURE_TICK"
    assert production_age_status(result.production_host_age_seconds) == "FUTURE_TICK"


def test_tick_older_than_five_seconds_is_stale():
    result = validate(normalized=T - timedelta(seconds=5.01), measurement=ntp(offset=0))
    assert result.status == "STALE_TICK"
    assert production_age_status(result.production_host_age_seconds) == "STALE_TICK"


def test_uncertainty_interval_touching_future_or_stale_boundary_fails_closed():
    future_boundary = validate(normalized=T+timedelta(seconds=.39),
                               measurement=ntp(offset=.39))
    assert future_boundary.status == "TICK_TIME_UNCERTAIN"
    stale_boundary = validate(normalized=T-timedelta(seconds=5),
                              measurement=ntp(offset=0))
    assert stale_boundary.status == "TICK_FRESHNESS_UNCERTAIN"


@pytest.mark.parametrize("samples", [(), (float("nan"),)*3, (0.0, 0.0)])
def test_ntp_unavailable_timeout_or_invalid_samples_fail_closed(samples):
    with pytest.raises(TrustedClockError):
        NtpMeasurement.from_samples(
            source="", measured_at_utc=T, sample_offsets_seconds=samples,
            network_uncertainty_seconds=None, root_dispersion_seconds=None,
        )


def test_dns_or_timeout_without_a_measurement_fails_closed():
    with pytest.raises(TrustedClockError, match="NTP_UNAVAILABLE_OR_INVALID"):
        validate(normalized=T, measurement=NtpMeasurement(
            "", T, 0.0, (), None, None))


def test_stale_measurement_fails_closed():
    with pytest.raises(TrustedClockError, match="NTP_MEASUREMENT_STALE"):
        validate(normalized=T, now=T, measurement=ntp(measured=T-timedelta(seconds=16)))


def test_excessive_spread_fails_closed():
    measure = ntp(samples=(.30, .39, .50))
    with pytest.raises(TrustedClockError, match="NTP_SPREAD_EXCESSIVE"):
        validate(normalized=T, measurement=measure)


def test_root_dispersion_or_network_delay_uncertainty_fails_closed():
    with pytest.raises(TrustedClockError, match="NTP_UNCERTAINTY_EXCESSIVE"):
        validate(normalized=T, measurement=ntp(dispersion=8.2))
    with pytest.raises(TrustedClockError, match="NTP_UNCERTAINTY_EXCESSIVE"):
        validate(normalized=T, measurement=ntp(network=.05))


def test_offset_step_fails_closed():
    clock = TrustedClock()
    clock.acquire(host_acquisition_utc=T, now_host_utc=T, ntp=ntp(offset=.39), monotonic_seconds=1)
    with pytest.raises(TrustedClockError, match="NTP_OFFSET_JUMP"):
        clock.acquire(host_acquisition_utc=T+timedelta(seconds=1),
                      now_host_utc=T+timedelta(seconds=1), ntp=ntp(offset=.51, measured=T+timedelta(seconds=1)),
                      monotonic_seconds=2)


def test_offset_sign_contract_is_explicit():
    with pytest.raises(TrustedClockError, match="NTP_OFFSET_SIGN_CONTRACT_INVALID"):
        validate(normalized=T+timedelta(seconds=.1),
                 measurement=ntp(sign="host_minus_trusted"))


def test_offset_value_must_match_samples_and_absolute_bound():
    mismatch = NtpMeasurement("fixture", T, .39, (.1, .1, .1), .001, .001)
    with pytest.raises(TrustedClockError, match="NTP_OFFSET_SAMPLE_MISMATCH"):
        validate(normalized=T, measurement=mismatch)
    extreme = NtpMeasurement("fixture", T, 6.0, (6.0, 6.0, 6.0), .001, .001)
    with pytest.raises(TrustedClockError, match="NTP_OFFSET_OUTSIDE_BOUND"):
        validate(normalized=T, measurement=extreme)


def test_system_wall_clock_jump_detected_against_monotonic_time():
    clock = TrustedClock()
    clock.acquire(host_acquisition_utc=T, now_host_utc=T, ntp=ntp(), monotonic_seconds=100)
    with pytest.raises(TrustedClockError, match="HOST_CLOCK_JUMP"):
        clock.acquire(host_acquisition_utc=T+timedelta(seconds=1),
                      now_host_utc=T+timedelta(seconds=1), ntp=ntp(measured=T+timedelta(seconds=1)),
                      monotonic_seconds=100.1)


def test_replayed_tick_identity_is_rejected():
    clock = TrustedClock()
    identity = (1791208800000, 1.1, 1.1001)
    validate(normalized=T-timedelta(seconds=1), clock=clock,
             tick_identity=identity)
    with pytest.raises(TrustedClockError, match="REPLAYED_TICK"):
        validate(normalized=T-timedelta(seconds=1), clock=clock,
                 tick_identity=identity)


def test_production_rule_remains_strict_and_unmodified():
    assert production_age_status(-.000001) == "FUTURE_TICK"
    assert production_age_status(0.0) == "ACCEPT"
    assert production_age_status(5.0) == "ACCEPT"
    assert production_age_status(5.000001) == "STALE_TICK"
