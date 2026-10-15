"""Operational clock diagnostics and bounded recertification; no strategy inputs."""
from __future__ import annotations

import json
import os
import re
import statistics
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

MAX_RECERTIFICATION_SECONDS = 180.0
MAX_RECERTIFICATION_ATTEMPTS = 36
RECERTIFICATION_INTERVAL_SECONDS = 5.0


def sleep_until(until: float, *, monotonic=time.monotonic, sleep=time.sleep,
                max_sleep: float) -> bool:
    """Sleep only for positive remaining time; return False once deadline elapsed."""
    remaining = until - monotonic()
    if remaining <= 0:
        return False
    sleep(min(max_sleep, remaining))
    return True


class ClockHealthError(RuntimeError):
    def __init__(self, reason: str, diagnostics: dict):
        super().__init__(reason)
        self.diagnostics = diagnostics


class PersistentClockFailure(ClockHealthError):
    pass


class ClockQueryError(RuntimeError):
    pass


def certify(query, now, validate):
    diagnostics = {"checked_at_utc": now().isoformat(), "checks": {}, "commands": {}}

    def fail(reason):
        diagnostics["failed_subcheck"] = reason
        raise ClockHealthError(reason, diagnostics)

    def run(label, *args):
        try:
            result = query(*args)
        except ClockQueryError as exc:
            diagnostics["commands"][label] = {"error_type": type(exc).__name__, "error": str(exc)}
            fail(label + "_QUERY_FAILED")
        diagnostics["commands"][label] = {"output": result}
        return result

    status = run("WINDOWS_TIME_STATUS", "w32tm", "/query", "/status")
    service = run("W32TIME_SERVICE", "sc.exe", "query", "W32Time")
    running = bool(re.search(r"STATE\s*:\s*4\s+RUNNING\b", service))
    diagnostics["checks"]["service_running"] = running
    if not running:
        fail("W32TIME_SERVICE_NOT_RUNNING")
    leap = re.search(r"^Leap Indicator:\s*(\d+)", status, re.MULTILINE)
    stratum = re.search(r"^Stratum:\s*(\d+)", status, re.MULTILINE)
    source_match = re.search(r"^Source:\s*(.+)$", status, re.MULTILINE)
    diagnostics["checks"].update(leap_indicator=int(leap[1]) if leap else None,
                                 stratum=int(stratum[1]) if stratum else None)
    if leap is None:
        fail("WINDOWS_LEAP_INDICATOR_PARSE_FAILED")
    if int(leap[1]) != 0:
        fail("WINDOWS_CLOCK_UNSYNCHRONIZED_LEAP")
    if stratum is None:
        fail("WINDOWS_STRATUM_PARSE_FAILED")
    if int(stratum[1]) != 2:
        fail("WINDOWS_STRATUM_NOT_CERTIFIED")
    source = source_match[1].strip() if source_match else ""
    diagnostics["checks"]["ntp_source"] = source
    if source.split(",")[0].lower() != "time.google.com":
        fail("WINDOWS_NTP_SOURCE_NOT_CERTIFIED")
    sync = re.search(r"^Last Successful Sync Time:\s*(.+)$", status, re.MULTILINE)
    if sync is None:
        fail("WINDOWS_LAST_SYNC_PARSE_FAILED")
    try:
        sync_local = datetime.strptime(sync[1].strip(), "%d-%m-%Y %H:%M:%S").replace(tzinfo=now().astimezone().tzinfo)
    except ValueError:
        fail("WINDOWS_LAST_SYNC_PARSE_FAILED")
    age = (now() - sync_local.astimezone(UTC)).total_seconds()
    diagnostics["checks"]["last_sync_age_seconds"] = age
    if age < 0 or age > 3600:
        fail("WINDOWS_TIME_SYNC_STALE")
    strip = run("INDEPENDENT_NTP", "w32tm", "/stripchart", "/computer:time.google.com", "/samples:5", "/dataonly")
    offsets = [float(v) for v in re.findall(r"([+-]\d+\.\d+)s", strip)]
    diagnostics["checks"]["ntp_samples_seconds"] = offsets
    if len(offsets) < 3:
        fail("INDEPENDENT_NTP_SAMPLES_UNAVAILABLE")
    mean = statistics.mean(offsets)
    spread = max(offsets) - min(offsets)
    diagnostics["checks"].update(ntp_mean_offset_seconds=mean, ntp_sample_spread_seconds=spread)
    if max(abs(v) for v in offsets) > 5.0:
        fail("INDEPENDENT_NTP_OFFSET_OUTSIDE_CONTRACT")
    if spread > 0.25:
        fail("INDEPENDENT_NTP_SPREAD_OUTSIDE_CONTRACT")
    local = now()
    evidence = validate(sampled_utc=local + timedelta(seconds=mean), system_utc=local)
    if not evidence["clock_safe"]:
        fail("VALIDATED_CLOCK_SKEW_OUTSIDE_CONTRACT")
    diagnostics["status"] = "PASS"
    return {**evidence, "ntp_source": source, "ntp_samples_seconds": offsets,
            "ntp_mean_offset_seconds": mean, "ntp_sample_spread_seconds": spread,
            "windows_time_status": status, "windows_time_service_running": True,
            "last_sync_age_seconds": age, "clock_safe": bool(evidence["clock_safe"] and abs(mean) <= 5.0)}, diagnostics


def log_event(root: Path, event: dict):
    """Distinct process log prevents collector/resolver append collisions."""
    now = datetime.now(UTC)
    directory = root / "runtime/v35_provenance/lifecycle"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"clock-{now.date().isoformat()}-{os.getpid()}.jsonl"
    with path.open("a", encoding="utf8") as log:
        log.write(json.dumps({"recorded_at_utc": now.isoformat(), "pid": os.getpid(), **event}, sort_keys=True) + "\n")
        log.flush()
        os.fsync(log.fileno())


def recertify(check, initial_error, publish, *, monotonic=time.monotonic, sleep=time.sleep,
              window=MAX_RECERTIFICATION_SECONDS, attempts=MAX_RECERTIFICATION_ATTEMPTS):
    """Pause with a hard deadline, bounded attempts, and no data callbacks."""
    start = monotonic()
    deadline = start + window
    last = initial_error
    for attempt in range(1, attempts + 1):
        remaining = deadline - monotonic()
        if remaining <= 0:
            break
        publish("CLOCK_UNCERTIFIED_TRANSIENT", last, attempt, deadline)
        # Every command receives the remaining deadline through check().
        try:
            result = check(deadline)
            if monotonic() > deadline:
                break
            publish("HEALTHY", None, attempt, deadline)
            return result
        except ClockHealthError as exc:
            last = exc
        if attempt == attempts:
            break
        until = min(monotonic() + RECERTIFICATION_INTERVAL_SECONDS, deadline)
        while monotonic() < until:
            publish("CLOCK_UNCERTIFIED_TRANSIENT", last, attempt, deadline)
            # Publishing state can take long enough to cross the deadline (or
            # monotonic time can jump after system suspend), so recompute now.
            if not sleep_until(until, monotonic=monotonic, sleep=sleep, max_sleep=1.0):
                break
    publish("FAILED_PERSISTENT", last, attempts, deadline)
    raise PersistentClockFailure("CLOCK_RECERTIFICATION_EXHAUSTED",
                                 {"initial_failure": initial_error.diagnostics, "last_failure": last.diagnostics,
                                  "window_seconds": window, "max_attempts": attempts,
                                  "elapsed_seconds": monotonic() - start})
