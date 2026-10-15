"""Research-only V3 loss diagnostics. Does not alter policies or send orders."""
from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OBSERVATIONS = ROOT / "certified_data" / "v3" / "V3_PROSPECTIVE_DATASET.jsonl"
OUTCOMES = ROOT / "certified_data" / "v3" / "V3_AUTOMATIC_OUTCOMES_v3_outcome_r1.jsonl"
DIAGNOSTICS = ROOT / "certified_data" / "v3" / "V3_LOSS_DIAGNOSTICS_v3_outcome_r1.jsonl"
REPORT = ROOT / "certified_data" / "v3" / "V3_LOSS_DIAGNOSTIC_REPORT_v3_outcome_r1.json"
PRIMARY_RR = 1
HORIZONS = {
    "5M": timedelta(minutes=5),
    "15M": timedelta(minutes=15),
    "30M": timedelta(minutes=30),
    "60M": timedelta(minutes=60),
    "3H": timedelta(hours=3),
    "6H": timedelta(hours=6),
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf8").splitlines() if line.strip()]


def _dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _tick_time(raw: Any) -> datetime:
    return datetime.fromtimestamp(int(raw["time"]), UTC)


def _tick_bid(raw: Any) -> float:
    return float(raw["bid"])


def _tick_ask(raw: Any) -> float:
    return float(raw["ask"])


def _exit_price(raw: Any, direction: str) -> float:
    return _tick_bid(raw) if direction == "BUY" else _tick_ask(raw)


def _move_r(price: float, entry: float, stop_distance: float, direction: str) -> float:
    return (price - entry) / stop_distance if direction == "BUY" else (entry - price) / stop_distance


def _window_ticks(ticks: list[Any], start: datetime, end: datetime) -> list[Any]:
    return [tick for tick in ticks if start <= _tick_time(tick) <= end]


def _nearest_after(ticks: list[Any], target: datetime) -> Any | None:
    for tick in ticks:
        if _tick_time(tick) >= target:
            return tick
    return None


def diagnose(outcome: dict[str, Any], observation: dict[str, Any], ticks: list[Any]) -> dict[str, Any]:
    entry_time = _dt(outcome["entry_timestamp"])
    end_time = _dt(outcome.get("max_hold_timestamp") or outcome["exit_timestamp"])
    direction = outcome["direction"]
    entry = float(outcome["entry_price"])
    stop_distance = float(outcome["stop_distance"])
    rr = float(outcome["rr"])
    window = _window_ticks(ticks, entry_time, end_time)
    moves = [_move_r(_exit_price(tick, direction), entry, stop_distance, direction) for tick in window]
    mfe = max(moves) if moves else None
    mae = min(moves) if moves else None
    tags: list[str] = []
    if outcome["exit_reason"] == "SL_HIT":
        if mfe is not None and mfe >= rr:
            tags.append("STOP_BEFORE_CORRECT_MOVE")
        elif mfe is not None and mfe > 0:
            tags.append("REVERSAL")
        else:
            tags.append("WRONG_DIRECTION")
    elif outcome["exit_reason"] == "TIME_EXIT":
        tags.append("INSUFFICIENT_MOVE")
    elif outcome["realized_r"] is not None and float(outcome["realized_r"]) <= 0:
        tags.append("OTHER")
    payload = observation.get("strategy_input_payload") or {}
    states = payload.get("states") or {}
    alignment = payload.get("alignment") or {}
    if alignment.get("GLOBAL_ALIGNMENT_SCORE", 0) <= 1:
        tags.append("MTF_CONFLICT")
    families = set(outcome.get("families") or [])
    regime = payload.get("market_regime") or payload.get("regime")
    if len(families) > 1 and {"MEAN_REVERSION", "BREAKOUT"} & families and {"TREND", "PULLBACK"} & families:
        tags.append("STRATEGY_CONFLICT")
    if ("MEAN_REVERSION" in families and regime not in {"RANGE", "LOW_VOL"}) or (
        "BREAKOUT" in families and not payload.get("breakout_up_state") and not payload.get("breakout_down_state")
    ):
        tags.append("REGIME_MISMATCH")
    if float(outcome.get("entry_spread") or 0) / stop_distance >= 0.05:
        tags.append("SPREAD_SENSITIVE")
    if not tags:
        tags.append("OTHER")
    direction_accuracy = {}
    for label, delta in HORIZONS.items():
        tick = _nearest_after(ticks, entry_time + delta)
        direction_accuracy[label] = None if tick is None else _move_r(_exit_price(tick, direction), entry, stop_distance, direction) > 0
    return {
        "diagnostic_id": f"{outcome['outcome_id']}|loss-diagnostic-r1",
        "observation_id": outcome["observation_id"],
        "outcome_id": outcome["outcome_id"],
        "rr": outcome["rr"],
        "direction": direction,
        "exit_reason": outcome["exit_reason"],
        "realized_r": outcome["realized_r"],
        "tags": sorted(set(tags)),
        "mfe_r": mfe,
        "mae_r": mae,
        "direction_accuracy": direction_accuracy,
        "families": outcome.get("families") or [],
        "session": (payload.get("session") or {}).get("session"),
        "market_regime": regime,
        "states": states,
        "alignment": alignment,
    }


def _metrics(items: list[dict[str, Any]]) -> dict[str, Any]:
    vals = [float(item["realized_r"]) for item in items if item.get("realized_r") is not None]
    wins = [v for v in vals if v > 0]
    losses = [v for v in vals if v < 0]
    return {
        "sample": len(items),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": None if not vals else len(wins) / len(vals) * 100,
        "net_r": sum(vals),
        "expectancy": None if not vals else sum(vals) / len(vals),
        "pf": None if not losses else sum(wins) / abs(sum(losses)),
        "median_mfe_r": _median([item["mfe_r"] for item in items if item.get("mfe_r") is not None]),
        "median_mae_r": _median([item["mae_r"] for item in items if item.get("mae_r") is not None]),
    }


def _median(values: list[float]) -> float | None:
    return None if not values else statistics.median(values)


def build_report(diagnostics: list[dict[str, Any]]) -> dict[str, Any]:
    primary = [item for item in diagnostics if float(item["rr"]) == PRIMARY_RR]
    wins = [item for item in primary if float(item["realized_r"]) > 0]
    losses = [item for item in primary if float(item["realized_r"]) < 0]
    tag_counts = Counter(tag for item in losses for tag in item["tags"])
    by_side = {side: _metrics([item for item in primary if item["direction"] == side]) for side in ("BUY", "SELL")}
    by_family = {
        family: _metrics([item for item in primary if family in item.get("families", [])])
        for family in ("TREND", "BREAKOUT", "PULLBACK", "MEAN_REVERSION", "VOLATILITY")
    }
    direction = {}
    for label in HORIZONS:
        vals = [item["direction_accuracy"].get(label) for item in primary if item["direction_accuracy"].get(label) is not None]
        direction[label] = None if not vals else sum(vals) / len(vals) * 100
    stop_then = {
        "+1R": sum(1 for item in losses if (item.get("mfe_r") or 0) >= 1),
        "+1.5R": sum(1 for item in losses if (item.get("mfe_r") or 0) >= 1.5),
        "+2R": sum(1 for item in losses if (item.get("mfe_r") or 0) >= 2),
    }
    session = defaultdict(list)
    regime = defaultdict(list)
    for item in primary:
        session[item.get("session") or "UNKNOWN"].append(item)
        regime[item.get("market_regime") or "UNKNOWN"].append(item)
    report = {
        "resolved_clean_setups": len(primary),
        "wins": len(wins),
        "losses": len(losses),
        "tag_counts": dict(tag_counts),
        "buy": by_side["BUY"],
        "sell": by_side["SELL"],
        "family": by_family,
        "direction_accuracy": direction,
        "stop_then": stop_then,
        "session": {key: _metrics(value) for key, value in session.items()},
        "regime": {key: _metrics(value) for key, value in regime.items()},
        "sample_sufficient_for_policy_change": False,
    }
    return report


def main() -> int:
    from app.v3.prospective import MANIFEST
    if MANIFEST.exists():
        raise RuntimeError('PROSPECTIVE_CAMPAIGN_ACTIVE_USE_BLINDED_HEALTH_REPORT_ONLY')
    observations = {row["observation_id"]: row for row in read_jsonl(OBSERVATIONS)}
    outcomes = [row for row in read_jsonl(OUTCOMES) if row.get("status") == "RESOLVED"]
    if not outcomes:
        REPORT.write_text(json.dumps({"resolved_clean_setups": 0}, indent=2), encoding="utf8")
        return 0
    start = min(_dt(row["entry_timestamp"]) for row in outcomes)
    end = max(_dt(row.get("max_hold_timestamp") or row["exit_timestamp"]) for row in outcomes)
    import MetaTrader5 as mt5

    if not mt5.initialize():
        raise RuntimeError("MT5 initialize failed")
    try:
        raw = mt5.copy_ticks_range("EURUSD", start, end, mt5.COPY_TICKS_ALL)
    finally:
        mt5.shutdown()
    ticks = sorted(
        [tick for tick in ([] if raw is None else list(raw)) if _tick_bid(tick) > 0 and _tick_ask(tick) >= _tick_bid(tick)],
        key=_tick_time,
    )
    diagnostics = [diagnose(row, observations[row["observation_id"]], ticks) for row in outcomes if row["observation_id"] in observations]
    existing = {row.get("diagnostic_id") for row in read_jsonl(DIAGNOSTICS)}
    new = [row for row in diagnostics if row["diagnostic_id"] not in existing]
    if new:
        with DIAGNOSTICS.open("a", encoding="utf8") as handle:
            for row in new:
                handle.write(json.dumps(row, sort_keys=True) + "\n")
    report = build_report(diagnostics)
    report["diagnostic_rows"] = len(diagnostics)
    report["new_diagnostic_rows"] = len(new)
    report["ticks"] = len(ticks)
    REPORT.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
