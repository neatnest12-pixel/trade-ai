"""Isolated NTP and monotonic-clock experiments; never imported by production.

The uncertainty interval is conditional on correct server timestamps and on
the responding server being honest. Plain NTP is unauthenticated. RTT bounds
the asymmetry contribution by RTT/2 only if both one-way delays are
non-negative; it does not establish a cryptographic or absolute UTC bound.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import math
import ipaddress
import socket
import statistics
import struct
import time
from typing import Iterable

NTP_EPOCH = 2_208_988_800
NTP_PACKET_SIZE = 48
DEFAULT_AUTHORITIES = (
    "time.google.com",
    "time.cloudflare.com",
    "time.windows.com",
    "time.nist.gov",
    "pool.ntp.org",
)


class NtpError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _to_ntp64(unix_seconds: float) -> int:
    whole = math.floor(unix_seconds)
    fraction = unix_seconds - whole
    return (((whole + NTP_EPOCH) & 0xFFFFFFFF) << 32) | int(fraction * (1 << 32))


def _from_ntp64(value: int, near_unix_seconds: float) -> float:
    seconds = (value >> 32) & 0xFFFFFFFF
    fraction = (value & 0xFFFFFFFF) / float(1 << 32)
    ntp_near = near_unix_seconds + NTP_EPOCH
    era = round((ntp_near - seconds) / float(1 << 32))
    return seconds + era * (1 << 32) + fraction - NTP_EPOCH


def _signed_16_16(value: int) -> float:
    return struct.unpack("!i", struct.pack("!I", value))[0] / 65536.0


def _fixed_16_16(value: int) -> float:
    return value / 65536.0


@dataclass(frozen=True)
class NtpExchange:
    server: str
    dns_results: tuple[str, ...]
    peer: str
    t1_utc: str
    t2_utc: str
    t3_utc: str
    t4_utc: str
    t1_epoch: float
    t2_epoch: float
    t3_epoch: float
    t4_epoch: float
    t1_monotonic_ns: int
    t4_monotonic_ns: int
    offset_seconds: float
    round_trip_delay_seconds: float
    stratum: int
    leap_indicator: int
    version: int
    mode: int
    precision_seconds: float
    reference_id_hex: str
    reference_id_text: str
    root_delay_seconds: float
    root_dispersion_seconds: float
    local_wall_clock_resolution_seconds: float
    reference_timestamp_utc: str | None
    originate_timestamp_valid: bool
    raw_response_hex: str
    conditional_uncertainty_seconds: float
    uncertainty_assumptions: tuple[str, ...]


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat()


def _make_request(t1_epoch: float) -> tuple[bytes, int]:
    packet = bytearray(NTP_PACKET_SIZE)
    packet[0] = (0 << 6) | (4 << 3) | 3  # LI=0, VN=4, client mode
    stamp = _to_ntp64(t1_epoch)
    struct.pack_into("!Q", packet, 40, stamp)
    return bytes(packet), stamp


def parse_response(*, server: str, dns_results: Iterable[str], peer: str,
                   request_originate: int, t1_epoch: float, t4_epoch: float,
                   t1_monotonic_ns: int, t4_monotonic_ns: int,
                   packet: bytes,
                   local_wall_clock_resolution_seconds: float | None = None) -> NtpExchange:
    if len(packet) < NTP_PACKET_SIZE:
        raise NtpError("MALFORMED_SHORT_PACKET")
    (first, stratum, poll, precision, root_delay_raw, root_dispersion_raw,
     refid, reference, originate, receive, transmit) = struct.unpack_from("!BBbbII4sQQQQ", packet)
    leap, version, mode = first >> 6, (first >> 3) & 7, first & 7
    if version < 3 or mode != 4:
        raise NtpError("MALFORMED_VERSION_OR_MODE")
    if leap == 3:
        raise NtpError("ALARM_LEAP_INDICATOR")
    if stratum == 0:
        raise NtpError("KISS_OF_DEATH")
    if stratum > 15:
        raise NtpError("INVALID_STRATUM")
    if originate != request_originate:
        raise NtpError("ORIGINATE_MISMATCH_OR_REPLAY")
    if receive == 0 or transmit == 0:
        raise NtpError("MISSING_SERVER_TIMESTAMP")
    t2 = _from_ntp64(receive, t4_epoch)
    t3 = _from_ntp64(transmit, t4_epoch)
    if t3 < t2:
        raise NtpError("SERVER_TIMESTAMP_ORDER_INVALID")
    delay = (t4_epoch - t1_epoch) - (t3 - t2)
    if delay < -0.005:
        raise NtpError("NEGATIVE_NETWORK_DELAY")
    delay = max(0.0, delay)
    offset = ((t2 - t1_epoch) + (t3 - t4_epoch)) / 2.0
    root_delay = _signed_16_16(root_delay_raw)
    root_dispersion = _fixed_16_16(root_dispersion_raw)
    precision_seconds = 2.0 ** precision
    wall_resolution = (time.get_clock_info("time").resolution
                       if local_wall_clock_resolution_seconds is None
                       else local_wall_clock_resolution_seconds)
    if not math.isfinite(wall_resolution) or wall_resolution < 0:
        raise NtpError("LOCAL_CLOCK_RESOLUTION_INVALID")
    # Conditional worst-case: any nonnegative one-way-delay asymmetry is at
    # most half RTT; server root distance plus timestamp quantization included.
    uncertainty = (delay / 2.0 + abs(root_delay) / 2.0 + root_dispersion
                   + precision_seconds + wall_resolution)
    ref_text = refid.decode("ascii", errors="replace").strip("\x00 ")
    reference_epoch = _from_ntp64(reference, t4_epoch) if reference else None
    return NtpExchange(
        server, tuple(dns_results), peer, _iso(t1_epoch), _iso(t2), _iso(t3), _iso(t4_epoch),
        t1_epoch, t2, t3, t4_epoch, t1_monotonic_ns, t4_monotonic_ns,
        offset, delay, stratum, leap, version, mode, precision_seconds,
        refid.hex(), ref_text, root_delay, root_dispersion, wall_resolution,
        _iso(reference_epoch) if reference_epoch is not None else None,
        True, packet.hex(), uncertainty,
        ("conditional on honest/correct server timestamps", "plain NTP response is unauthenticated",
         "one-way delays are nonnegative, so asymmetry contribution is bounded by RTT/2",
         "server root delay/dispersion and server precision are valid bounds",
         "client wall-clock resolution is added conservatively",
         "client T1/T4 are wall-clock timestamps bracketed by monotonic clock"),
    )


def query_ntp(server: str, timeout_seconds: float = 2.0) -> NtpExchange:
    """Perform one direct NTPv4 exchange and preserve both wall and monotonic stamps."""
    try:
        infos = socket.getaddrinfo(server, 123, type=socket.SOCK_DGRAM)
    except OSError as exc:
        raise NtpError("DNS_FAILURE") from exc
    addresses = tuple(dict.fromkeys(info[4][0] for info in infos))
    if not addresses:
        raise NtpError("DNS_NO_RESULTS")
    last_error: Exception | None = None
    for info in infos:
        family, socktype, proto, _, sockaddr = info
        sock = socket.socket(family, socktype, proto)
        try:
            sock.settimeout(timeout_seconds)
            t1_mono = time.perf_counter_ns()
            t1 = time.time_ns() / 1e9
            request, originate = _make_request(t1)
            sock.sendto(request, sockaddr)
            packet, source = sock.recvfrom(2048)
            t4 = time.time_ns() / 1e9
            t4_mono = time.perf_counter_ns()
            if t4_mono < t1_mono:
                raise NtpError("MONOTONIC_CLOCK_REGRESSION")
            peer_ip = ipaddress.ip_address(source[0].split("%", 1)[0])
            if peer_ip not in {ipaddress.ip_address(x.split("%", 1)[0]) for x in addresses}:
                raise NtpError("UNEXPECTED_NTP_RESPONSE_SOURCE")
            return parse_response(server=server, dns_results=addresses,
                                  peer=source[0], request_originate=originate,
                                  t1_epoch=t1, t4_epoch=t4,
                                  t1_monotonic_ns=t1_mono,
                                  t4_monotonic_ns=t4_mono, packet=packet)
        except (OSError, NtpError) as exc:
            last_error = exc
        finally:
            sock.close()
    if isinstance(last_error, NtpError):
        raise last_error
    if isinstance(last_error, TimeoutError) or isinstance(last_error, socket.timeout):
        raise NtpError("NTP_TIMEOUT") from last_error
    raise NtpError("NTP_NETWORK_ERROR") from last_error


@dataclass(frozen=True)
class Consensus:
    accepted: bool
    reason: str
    sources: tuple[str, ...]
    excluded_sources: tuple[str, ...]
    median_offset_seconds: float | None
    lower_offset_seconds: float | None
    upper_offset_seconds: float | None
    uncertainty_seconds: float | None
    source_disagreement_seconds: float | None


def consensus(exchanges: Iterable[NtpExchange], *, minimum_sources: int = 3,
              interval_overlap_tolerance_seconds: float = 0.0) -> Consensus:
    """Require a strict source majority with overlapping conditional intervals."""
    rows = tuple(exchanges)
    if len(rows) < minimum_sources:
        return Consensus(False, "INSUFFICIENT_SOURCES", (), (), None, None, None, None, None)
    identities = [r.server.lower() for r in rows]
    if len(set(identities)) != len(identities):
        return Consensus(False, "DUPLICATE_AUTHORITY", (), (), None, None, None, None, None)
    n = len(rows)
    required = max(minimum_sources, n // 2 + 1)
    candidates: list[tuple[tuple[NtpExchange, ...], float, float]] = []
    # Search all subsets (expected source count is small) and require a unique
    # largest strict-majority set whose conditional intervals intersect.
    import itertools
    for size in range(required, n + 1):
        for group in itertools.combinations(rows, size):
            lo = max(x.offset_seconds - x.conditional_uncertainty_seconds for x in group)
            hi = min(x.offset_seconds + x.conditional_uncertainty_seconds for x in group)
            if lo <= hi + interval_overlap_tolerance_seconds:
                candidates.append((group, lo, hi))
    if not candidates:
        return Consensus(False, "NO_INTERVAL_MAJORITY", (), (), None, None, None, None, None)
    max_size = max(len(c[0]) for c in candidates)
    best = [c for c in candidates if len(c[0]) == max_size]
    source_sets = {frozenset(x.server for x in c[0]) for c in best}
    if len(source_sets) != 1:
        return Consensus(False, "AMBIGUOUS_MAJORITY", (), (), None, None, None, None, None)
    group, lo, hi = best[0]
    accepted_names = tuple(x.server for x in group)
    excluded = tuple(x.server for x in rows if x.server not in accepted_names)
    offsets = [x.offset_seconds for x in group]
    med = statistics.median(offsets)
    radius = max(abs(med - lo), abs(hi - med))
    return Consensus(True, "CONDITIONAL_INTERVAL_CONSENSUS", accepted_names,
                     excluded, med, lo, hi, radius,
                     max(offsets) - min(offsets))


@dataclass(frozen=True)
class BridgeReading:
    utc_estimate: datetime
    uncertainty_seconds: float
    elapsed_seconds: float


class MonotonicUtcBridge:
    """Experimental UTC anchor + monotonic elapsed time; caller must certify anchor."""
    def __init__(self, *, anchor_utc: datetime, anchor_monotonic_ns: int,
                 anchor_uncertainty_seconds: float, certified: bool,
                 max_age_seconds: float = 30.0,
                 max_uncertainty_seconds: float = 0.05,
                 oscillator_bound_ppm: float = 50.0,
                 wall_clock_at_anchor: datetime | None = None,
                 wall_jump_tolerance_seconds: float = 0.05):
        if not certified:
            raise NtpError("UTC_ANCHOR_NOT_CERTIFIED")
        if anchor_utc.tzinfo is None or not math.isfinite(anchor_uncertainty_seconds) or anchor_uncertainty_seconds < 0:
            raise NtpError("UTC_ANCHOR_INVALID")
        if anchor_uncertainty_seconds > max_uncertainty_seconds:
            raise NtpError("UTC_ANCHOR_UNCERTAINTY_EXCESSIVE")
        self.anchor_utc = anchor_utc.astimezone(UTC)
        self.anchor_mono = anchor_monotonic_ns
        self.anchor_uncertainty = anchor_uncertainty_seconds
        self.max_age = max_age_seconds
        self.max_uncertainty = max_uncertainty_seconds
        self.oscillator_ppm = oscillator_bound_ppm
        self.wall_anchor = wall_clock_at_anchor.astimezone(UTC) if wall_clock_at_anchor else None
        self.jump_tolerance = wall_jump_tolerance_seconds

    def estimate(self, *, monotonic_ns: int, wall_utc: datetime,
                 suspend_or_resume_detected: bool = False) -> BridgeReading:
        if suspend_or_resume_detected:
            raise NtpError("SUSPEND_RESUME_INVALIDATES_ANCHOR")
        elapsed = (monotonic_ns - self.anchor_mono) / 1e9
        if elapsed < 0:
            raise NtpError("MONOTONIC_CLOCK_REGRESSION")
        if elapsed > self.max_age:
            raise NtpError("UTC_ANCHOR_STALE")
        if wall_utc.tzinfo is None:
            raise NtpError("WALL_CLOCK_INVALID")
        if self.wall_anchor is not None:
            wall_elapsed = (wall_utc.astimezone(UTC) - self.wall_anchor).total_seconds()
            if abs(wall_elapsed - elapsed) > self.jump_tolerance:
                raise NtpError("WALL_CLOCK_JUMP_OR_SUSPEND")
        uncertainty = self.anchor_uncertainty + elapsed * self.oscillator_ppm / 1e6
        if uncertainty > self.max_uncertainty:
            raise NtpError("UTC_BRIDGE_UNCERTAINTY_EXCESSIVE")
        return BridgeReading(self.anchor_utc + timedelta(seconds=elapsed), uncertainty, elapsed)
