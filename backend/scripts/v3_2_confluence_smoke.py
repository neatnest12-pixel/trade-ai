from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.v3_2.confluence import (
    POLICY_HASH,
    POLICY_VERSION,
    TIMEFRAME_MINUTES,
    Candle,
    TickQuote,
    build_confluence_vector,
    stable_hash,
)

ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = ROOT / "certified_data" / "v3_2" / "V3_2_CONFLUENCE_SMOKE.jsonl"
STATE = ROOT / "runtime" / "v3_2_confluence_smoke_state.json"
LOCK = ROOT / "runtime" / "v3_2_confluence_smoke.lock"
SYMBOL = "EURUSD"


MT5_TIMEFRAMES = {
    "M1": "TIMEFRAME_M1",
    "M5": "TIMEFRAME_M5",
    "M15": "TIMEFRAME_M15",
    "H1": "TIMEFRAME_H1",
    "H6": "TIMEFRAME_H6",
    "H12": "TIMEFRAME_H12",
}


def _assert_research_only() -> None:
    if os.environ.get("TRADING_EXECUTION_ENABLED", "false").lower() != "false":
        raise RuntimeError("TRADING_EXECUTION_ENABLED must be false")
    if os.environ.get("DEMO_EXECUTION_ENABLED", "false").lower() != "false":
        raise RuntimeError("DEMO_EXECUTION_ENABLED must be false")
    if os.environ.get("REAL_EXECUTION_ENABLED", "false").lower() != "false":
        raise RuntimeError("REAL_EXECUTION_ENABLED must be false")
    if os.environ.get("MT5_READ_ONLY", "true").lower() != "true":
        raise RuntimeError("MT5_READ_ONLY must be true")


def _load_rates(mt5: Any, timeframe: str, count: int = 240) -> list[Candle]:
    tf = getattr(mt5, MT5_TIMEFRAMES[timeframe])
    rates = mt5.copy_rates_from_pos(SYMBOL, tf, 0, count)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"no {timeframe} rates")
    candles = [
        Candle(
            timestamp=datetime.fromtimestamp(int(row["time"]), tz=UTC),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            tick_volume=float(row["tick_volume"]),
            spread=float(row["spread"]) if "spread" in row.dtype.names else None,
            real_volume=float(row["real_volume"]) if "real_volume" in row.dtype.names else 0,
        )
        for row in rates
    ]
    # Drop the current forming candle; the engine will still enforce completion.
    return candles[:-1]


def _latest_decision_timestamp(candles_by_tf: dict[str, list[Candle]]) -> datetime:
    latest_m1 = candles_by_tf["M1"][-1].timestamp
    return latest_m1 + timedelta(minutes=TIMEFRAME_MINUTES["M1"])


def run_smoke() -> dict[str, Any]:
    _assert_research_only()
    if LOCK.exists():
        raise RuntimeError("v3.2 smoke lock already exists")
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    LOCK.write_text(json.dumps({"policy_version": POLICY_VERSION, "acquired_at": datetime.now(UTC).isoformat()}))
    try:
        import MetaTrader5 as mt5  # type: ignore

        if not mt5.initialize():
            raise RuntimeError("MT5 initialize failed")
        try:
            symbol_info = mt5.symbol_info(SYMBOL)
            tick_info = mt5.symbol_info_tick(SYMBOL)
            if symbol_info is None or tick_info is None:
                raise RuntimeError("EURUSD unavailable")
            candles_by_tf = {tf: _load_rates(mt5, tf) for tf in MT5_TIMEFRAMES}
            decision_timestamp = _latest_decision_timestamp(candles_by_tf)
            tick = TickQuote(
                timestamp=datetime.fromtimestamp(int(tick_info.time), tz=UTC),
                bid=float(tick_info.bid),
                ask=float(tick_info.ask),
                provenance="MT5_SYMBOL_INFO_TICK",
            )
            if tick.timestamp > decision_timestamp:
                tick = TickQuote(timestamp=decision_timestamp, bid=tick.bid, ask=tick.ask, provenance="MT5_SYMBOL_INFO_TICK_CAPPED_TO_DECISION")
            vector = build_confluence_vector(candles_by_tf, decision_timestamp, tick)
            record = {
                "record_type": "V3_2_CONFLUENCE_SMOKE",
                "created_at": datetime.now(UTC).isoformat(),
                "symbol": SYMBOL,
                "policy_version": POLICY_VERSION,
                "policy_hash": POLICY_HASH,
                "decision_timestamp": vector["decision_timestamp"],
                "confluence_vector": vector,
            }
            record["record_hash"] = stable_hash(record)
            ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
            with ARTIFACT.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
            STATE.parent.mkdir(parents=True, exist_ok=True)
            STATE.write_text(
                json.dumps(
                    {
                        "status": "ONCE_COMPLETE",
                        "last_smoke_at": datetime.now(UTC).isoformat(),
                        "policy_version": POLICY_VERSION,
                        "policy_hash": POLICY_HASH,
                        "artifact": str(ARTIFACT),
                        "last_error": None,
                    },
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            return record
        finally:
            mt5.shutdown()
    finally:
        try:
            LOCK.unlink()
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    try:
        print(json.dumps(run_smoke(), indent=2, sort_keys=True))
    except Exception as exc:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(
            json.dumps(
                {
                    "status": "FAILED",
                    "last_smoke_at": datetime.now(UTC).isoformat(),
                    "policy_version": POLICY_VERSION,
                    "policy_hash": POLICY_HASH,
                    "last_error": str(exc),
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        print(f"V3.2 smoke failed: {exc}", file=sys.stderr)
        sys.exit(1)
