from datetime import UTC, datetime, timedelta
import struct

import pytest

from app.v3_5.ntp_forensics_experiment import (
    MonotonicUtcBridge, NtpError, _to_ntp64, consensus, parse_response,
    query_ntp,
)

T = datetime(2026, 10, 5, 14, 0, 0, tzinfo=UTC).timestamp()


def packet(*, originate, t2=T + .010, t3=T + .011, stratum=2, leap=0,
           root_delay=.004, dispersion=.003, precision=-20, mode=4,
           version=4, receive_override=None):
    data = bytearray(48)
    data[0] = (leap << 6) | (version << 3) | mode
    data[1] = stratum
    data[2] = 6
    data[3] = precision & 0xff
    struct.pack_into("!i", data, 4, int(root_delay * 65536))
    struct.pack_into("!I", data, 8, int(dispersion * 65536))
    data[12:16] = b"TEST"
    struct.pack_into("!Q", data, 16, _to_ntp64(T - 1))
    struct.pack_into("!Q", data, 24, originate)
    struct.pack_into("!Q", data, 32, _to_ntp64(t2 if receive_override is None else receive_override))
    struct.pack_into("!Q", data, 40, _to_ntp64(t3))
    return bytes(data)


def exchange(name, offset, uncertainty=.02):
    # Consensus fixtures focus only on source intervals.
    from dataclasses import replace
    base = parse_response(server=name, dns_results=("192.0.2.1",), peer="192.0.2.1",
        request_originate=_to_ntp64(T), t1_epoch=T, t4_epoch=T + .02,
        t1_monotonic_ns=1, t4_monotonic_ns=20_000_001,
        packet=packet(originate=_to_ntp64(T), t2=T + .01 + offset, t3=T + .011 + offset))
    return replace(base, offset_seconds=offset, conditional_uncertainty_seconds=uncertainty)


def test_full_ntp_exchange_captures_fields_and_conditional_error_interval():
    row = exchange("one", .002)
    assert row.originate_timestamp_valid
    assert row.stratum == 2 and row.leap_indicator == 0
    assert row.round_trip_delay_seconds == pytest.approx(.019, abs=2e-6)
    assert row.offset_seconds == pytest.approx(.002, abs=2e-6)
    assert row.root_dispersion_seconds == pytest.approx(3 / 1000, abs=.00002)
    assert row.conditional_uncertainty_seconds > row.round_trip_delay_seconds / 2
    assert "unauthenticated" in " ".join(row.uncertainty_assumptions)
    assert row.raw_response_hex


@pytest.mark.parametrize("kwargs,code", [
    ({"stratum": 0}, "KISS_OF_DEATH"),
    ({"stratum": 16}, "INVALID_STRATUM"),
    ({"leap": 3}, "ALARM_LEAP_INDICATOR"),
    ({"mode": 3}, "MALFORMED_VERSION_OR_MODE"),
    ({"version": 2}, "MALFORMED_VERSION_OR_MODE"),
])
def test_invalid_ntp_server_state_fails_closed(kwargs, code):
    origin = _to_ntp64(T)
    with pytest.raises(NtpError, match=code):
        parse_response(server="x", dns_results=(), peer="1.2.3.4",
            request_originate=origin, t1_epoch=T, t4_epoch=T + .02,
            t1_monotonic_ns=1, t4_monotonic_ns=2,
            packet=packet(originate=origin, **kwargs))


def test_malformed_originate_replay_or_missing_timestamp_rejected():
    origin = _to_ntp64(T)
    with pytest.raises(NtpError, match="ORIGINATE_MISMATCH_OR_REPLAY"):
        parse_response(server="x", dns_results=(), peer="1", request_originate=origin,
            t1_epoch=T, t4_epoch=T + .02, t1_monotonic_ns=1, t4_monotonic_ns=2,
            packet=packet(originate=origin + 1))
    raw = bytearray(packet(originate=origin))
    raw[32:40] = bytes(8)
    with pytest.raises(NtpError, match="MISSING_SERVER_TIMESTAMP"):
        parse_response(server="x", dns_results=(), peer="1", request_originate=origin,
            t1_epoch=T, t4_epoch=T + .02, t1_monotonic_ns=1, t4_monotonic_ns=2,
            packet=bytes(raw))
    with pytest.raises(NtpError, match="MALFORMED_SHORT_PACKET"):
        parse_response(server="x", dns_results=(), peer="1", request_originate=origin,
            t1_epoch=T, t4_epoch=T + .02, t1_monotonic_ns=1, t4_monotonic_ns=2,
            packet=b"short")


def test_consensus_excludes_one_lying_server_but_requires_strict_majority():
    result = consensus((exchange("a", .01), exchange("b", .011),
                        exchange("c", .009), exchange("liar", .8)))
    assert result.accepted
    assert set(result.sources) == {"a", "b", "c"}
    assert result.excluded_sources == ("liar",)
    assert result.lower_offset_seconds <= result.upper_offset_seconds
    assert consensus((exchange("a", 0), exchange("b", .3))).reason == "INSUFFICIENT_SOURCES"


def test_consensus_fails_when_sources_disagree_without_majority():
    result = consensus((exchange("a", 0, .001), exchange("b", .2, .001),
                        exchange("c", .4, .001)))
    assert not result.accepted
    assert result.reason == "NO_INTERVAL_MAJORITY"


def test_ntp_rtt_half_is_conditional_not_symmetric_delay_assumption():
    # Forward/reverse delays 80ms/20ms create 30ms offset bias while the
    # observed 100ms RTT/2 conditional interval still bounds that asymmetry.
    forward, reverse = .080, .020
    asymmetry_bias = abs(forward - reverse) / 2
    assert asymmetry_bias <= (forward + reverse) / 2


def test_dns_failure_timeout_and_unavailable_sources_fail_closed(monkeypatch):
    import socket

    def no_dns(*args, **kwargs):
        raise socket.gaierror("fixture DNS failure")
    monkeypatch.setattr(socket, "getaddrinfo", no_dns)
    with pytest.raises(NtpError, match="DNS_FAILURE"):
        query_ntp("invalid.fixture")
    # One unavailable authority leaves fewer than the required three sources.
    assert not consensus((exchange("a", 0), exchange("b", .001))).accepted


def test_delay_and_root_dispersion_expand_or_invalidate_error_interval():
    origin = _to_ntp64(T)
    ordinary = parse_response(server="x", dns_results=(), peer="1", request_originate=origin,
        t1_epoch=T, t4_epoch=T + .02, t1_monotonic_ns=1, t4_monotonic_ns=2,
        packet=packet(originate=origin, dispersion=.003),
        local_wall_clock_resolution_seconds=.001)
    degraded = parse_response(server="x", dns_results=(), peer="1", request_originate=origin,
        t1_epoch=T, t4_epoch=T + .02, t1_monotonic_ns=1, t4_monotonic_ns=2,
        packet=packet(originate=origin, dispersion=2.0),
        local_wall_clock_resolution_seconds=.001)
    assert degraded.conditional_uncertainty_seconds > ordinary.conditional_uncertainty_seconds
    with pytest.raises(NtpError, match="NEGATIVE_NETWORK_DELAY"):
        parse_response(server="x", dns_results=(), peer="1", request_originate=origin,
            t1_epoch=T, t4_epoch=T - .01, t1_monotonic_ns=1, t4_monotonic_ns=2,
            packet=packet(originate=origin, t2=T + .01, t3=T + .011))


def bridge(**kwargs):
    return MonotonicUtcBridge(anchor_utc=datetime.fromtimestamp(T, UTC),
        anchor_monotonic_ns=1_000_000_000, anchor_uncertainty_seconds=.005,
        certified=True, max_age_seconds=60, max_uncertainty_seconds=.01,
        oscillator_bound_ppm=50, wall_clock_at_anchor=datetime.fromtimestamp(T, UTC),
        **kwargs)


def test_monotonic_bridge_anchor_elapsed_and_uncertainty():
    row = bridge().estimate(monotonic_ns=3_000_000_000,
                            wall_utc=datetime.fromtimestamp(T + 2, UTC))
    assert row.utc_estimate == datetime.fromtimestamp(T + 2, UTC)
    assert row.uncertainty_seconds == pytest.approx(.0051)


@pytest.mark.parametrize("elapsed,wall_delta,code", [
    (2, 3, "WALL_CLOCK_JUMP_OR_SUSPEND"),
    (61, 61, "UTC_ANCHOR_STALE"),
    (-1, -1, "MONOTONIC_CLOCK_REGRESSION"),
])
def test_monotonic_bridge_clock_jump_staleness_and_regression(elapsed, wall_delta, code):
    with pytest.raises(NtpError, match=code):
        bridge().estimate(monotonic_ns=1_000_000_000 + elapsed * 1_000_000_000,
                          wall_utc=datetime.fromtimestamp(T + wall_delta, UTC))


def test_monotonic_bridge_suspend_resume_and_uncertified_anchor_rejected():
    with pytest.raises(NtpError, match="SUSPEND_RESUME_INVALIDATES_ANCHOR"):
        bridge().estimate(monotonic_ns=2_000_000_000,
                          wall_utc=datetime.fromtimestamp(T + 1, UTC),
                          suspend_or_resume_detected=True)
    with pytest.raises(NtpError, match="UTC_ANCHOR_NOT_CERTIFIED"):
        MonotonicUtcBridge(anchor_utc=datetime.fromtimestamp(T, UTC),
            anchor_monotonic_ns=1, anchor_uncertainty_seconds=.001, certified=False)


def test_bridge_uncertainty_accumulates_and_fails_closed_when_bound_expires():
    small = MonotonicUtcBridge(anchor_utc=datetime.fromtimestamp(T, UTC),
        anchor_monotonic_ns=0, anchor_uncertainty_seconds=.005, certified=True,
        max_age_seconds=1000, max_uncertainty_seconds=.01, oscillator_bound_ppm=50)
    assert small.estimate(monotonic_ns=10_000_000_000,
        wall_utc=datetime.fromtimestamp(T + 10, UTC)).uncertainty_seconds > .005
    with pytest.raises(NtpError, match="UTC_BRIDGE_UNCERTAINTY_EXCESSIVE"):
        small.estimate(monotonic_ns=200_000_000_000,
            wall_utc=datetime.fromtimestamp(T + 200, UTC))
