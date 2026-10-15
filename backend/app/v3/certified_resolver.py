"""Streaming tick-only outcome resolution with a bounded, disk-backed tick cache."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime, timedelta

from .certification import POLICY_HASH, POLICY_VERSION, contract_id, utc

RR = (1, 1.5, 2, 3, 5)
WINDOW_SECONDS = 900
MAX_TICKS_PER_WINDOW = 100000


class CoverageError(RuntimeError):
    pass


class MT5Ticks:
    """Read-only MT5 surface: never exposes trading methods to the resolver."""
    def __init__(self, mt5, db, heartbeat=lambda: None):
        self._mt5, self.db, self.heartbeat = mt5, db, heartbeat
        self.max_window_ticks = 0
        self.calls = 0

    def ticks(self, start: datetime, end: datetime, offset: int):
        first = int(start.timestamp()) // WINDOW_SECONDS * WINDOW_SECONDS
        last = int(end.timestamp()) // WINDOW_SECONDS * WINDOW_SECONDS
        previous = None
        for window in range(first, last + 1, WINDOW_SECONDS):
            key = f"ICMarketsSC-Demo|EURUSD|{offset}|{window}|{WINDOW_SECONDS}"
            cached = self.db.execute("SELECT payload FROM tick_windows WHERE window_key=?", (key,)).fetchone()
            if cached:
                rows = json.loads(cached[0])
            else:
                raw_start = datetime.fromtimestamp(window + offset, UTC)
                raw_end = raw_start + timedelta(seconds=WINDOW_SECONDS)
                raw = self._mt5.copy_ticks_range("EURUSD", raw_start, raw_end, self._mt5.COPY_TICKS_ALL)
                self.calls += 1
                self.heartbeat()
                if raw is None:
                    raise RuntimeError(f"MT5_TICKS_UNAVAILABLE:{self._mt5.last_error()}")
                if len(raw) > MAX_TICKS_PER_WINDOW:
                    raise RuntimeError("TICK_WINDOW_LIMIT_EXCEEDED")
                rows = []
                for tick in raw:
                    stamp = int(tick["time_msc"])
                    if stamp <= 0:
                        raise CoverageError("MILLISECOND_TIMESTAMP_UNAVAILABLE")
                    t = stamp - offset * 1000
                    # Half-open windows prevent duplicate boundary ticks.
                    if window * 1000 <= t < (window + WINDOW_SECONDS) * 1000:
                        rows.append([t, float(tick["bid"]), float(tick["ask"])])
                # Cache only closed historical windows; empty windows expire by not caching.
                if rows and window + WINDOW_SECONDS < datetime.now(UTC).timestamp():
                    self.db.execute("INSERT OR IGNORE INTO tick_windows VALUES(?,?)", (key, json.dumps(rows)))
                    self.db.commit()
            self.max_window_ticks = max(self.max_window_ticks, len(rows))
            for t, bid, ask in rows:
                if previous is not None and t < previous:
                    raise CoverageError("NON_MONOTONIC_TICKS")
                previous = t
                if start.timestamp() * 1000 <= t <= end.timestamp() * 1000:
                    yield t, bid, ask


def evaluate(setup: dict, source, now: datetime | None = None) -> tuple[str, str | None, list[dict]]:
    if setup["timing_status"] != "CERTIFIED":
        return "TIMING_UNCERTIFIABLE", setup.get("timing_reason"), []
    decision = utc(setup["decision_timestamp"])
    now = now or datetime.now(UTC)
    # Entry is restricted to the first minute; horizon is six hours from actual entry.
    if now < decision + timedelta(hours=6, minutes=2):
        return "INSUFFICIENT_FUTURE_DATA", "IMMATURE_HORIZON", []
    entry = None
    previous = decision.timestamp() * 1000
    resolved = {}
    trace = hashlib.sha256()
    seen = 0
    mfe = mae = 0.0
    mfe_at = mae_at = None
    try:
        for t, bid, ask in source.ticks(decision, decision + timedelta(hours=6, minutes=2), setup["offset_seconds"]):
            if t <= decision.timestamp() * 1000:
                continue
            if not (math.isfinite(bid) and math.isfinite(ask) and 0 < bid <= ask):
                raise CoverageError("INVALID_FUTURE_QUOTE")
            if t < previous:
                raise CoverageError("NON_MONOTONIC_TICKS")
            gap = (t - previous) / 1000
            if gap > (60 if entry is None else 300):
                raise CoverageError("ENTRY_GAP" if entry is None else "FUTURE_TICK_GAP")
            previous = t
            trace.update(json.dumps([t, bid, ask], separators=(",", ":")).encode())
            seen += 1
            if entry is None:
                entry = (t, ask if setup["direction"] == "BUY" else bid, bid, ask)
                mfe_at = mae_at = t
                continue
            if t <= entry[0]:
                continue
            horizon = entry[0] + 21600000
            if t > horizon + 60000:
                raise CoverageError("TIMEOUT_TICK_GAP")
            price = bid if setup["direction"] == "BUY" else ask
            sign = 1 if setup["direction"] == "BUY" else -1
            realized = sign * (price-entry[1]) / setup["stop_distance"]
            if realized > mfe:
                mfe, mfe_at = realized, t
            if -realized > mae:
                mae, mae_at = -realized, t
            for rr in RR:
                if rr in resolved:
                    continue
                reason = None
                policy_r = realized
                if t <= horizon and realized <= -1:
                    reason, policy_r = "SL_HIT", -1.0
                elif t <= horizon and realized >= rr:
                    reason, policy_r = "TP_HIT", float(rr)
                elif t >= horizon:
                    reason = "TIME_EXIT"
                if reason:
                    iso = lambda milliseconds: datetime.fromtimestamp(milliseconds/1000, UTC).isoformat()
                    resolved[rr] = {
                        "outcome_id": contract_id(setup["observation_id"], rr), "observation_id": setup["observation_id"],
                        "rr": rr, "outcome_policy_version": POLICY_VERSION, "outcome_policy_hash": POLICY_HASH,
                        "certification": "CERTIFIED", "status": "RESOLVED", "direction": setup["direction"],
                        "families": setup["families"], "decision_timestamp": setup["decision_timestamp"],
                        "signal_timestamp": setup["decision_timestamp"], "bar_timestamp": setup["bar_timestamp"],
                        "entry_timestamp": iso(entry[0]), "entry_price": entry[1], "entry_bid": entry[2], "entry_ask": entry[3],
                        "entry_spread": entry[3]-entry[2], "entry_provenance": "MT5_TICK_EXPLICIT_OFFSET",
                        "exit_timestamp": iso(t), "exit_price": price, "exit_bid": bid, "exit_ask": ask,
                        "exit_reason": reason, "max_hold_timestamp": iso(horizon),
                        "atr_value": setup["atr_value"], "atr_timestamp": setup["atr_timestamp"],
                        "atr_timeframe": "M15", "atr_period": 14, "stop_multiplier": 1.5,
                        "stop_distance": setup["stop_distance"], "sl": entry[1]-sign*setup["stop_distance"],
                        "tp": entry[1]+sign*setup["stop_distance"]*rr,
                        "realized_r": realized, "policy_r": policy_r,
                        "mfe_r": mfe, "mae_r": mae, "mfe_timestamp": iso(mfe_at), "mae_timestamp": iso(mae_at),
                        "commission_status": "UNAVAILABLE", "commission_cost_r": None,
                        "slippage_status": "UNAVAILABLE_ACTUAL_FILL", "swap_status": "UNAVAILABLE",
                        "net_r": None, "cost_status": "PRE_COMMISSION_QUOTE_BASED", "results_before_commission": True,
                        "tick_prefix_sha256": trace.hexdigest(), "ticks_examined": seen,
                        "offset_seconds": setup["offset_seconds"],
                    }
            if len(resolved) == len(RR):
                return "RESOLVABLE", None, list(resolved.values())
    except CoverageError as exc:
        return "INSUFFICIENT_FUTURE_DATA", str(exc), list(resolved.values())
    return "INSUFFICIENT_FUTURE_DATA", "NO_ENTRY_TICK" if entry is None else "INCOMPLETE_HORIZON", list(resolved.values())
