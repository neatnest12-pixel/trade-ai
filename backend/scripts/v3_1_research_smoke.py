from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.v3_1.strategy_library import POLICY_HASH, POLICY_VERSION, build_research_snapshot
from app.v3_2.confluence import Candle, TickQuote, stable_hash

ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = ROOT / "certified_data" / "v3_1" / "V3_1_RESEARCH_SMOKE.jsonl"


def _candles(minutes: int, count: int = 80) -> list[Candle]:
    base = datetime(2026, 8, 20, 0, 0, tzinfo=UTC)
    price = 1.10
    rows = []
    for idx in range(count):
        direction = 1 if idx % 8 < 5 else -1
        open_price = price
        close = open_price + direction * 0.0002
        rows.append(
            Candle(
                timestamp=base + timedelta(minutes=idx * minutes),
                open=open_price,
                high=max(open_price, close) + 0.0003,
                low=min(open_price, close) - 0.0003,
                close=close,
                tick_volume=100 + idx,
                spread=2,
            )
        )
        price = close
    return rows


def run_smoke() -> dict[str, object]:
    candles_by_tf = {
        "M1": _candles(1),
        "M5": _candles(5),
        "M15": _candles(15),
        "H1": _candles(60),
        "H6": _candles(360),
        "H12": _candles(720),
    }
    decision = candles_by_tf["H12"][-1].timestamp + timedelta(hours=12)
    snapshot = build_research_snapshot(candles_by_tf, decision, TickQuote(decision - timedelta(seconds=1), 1.101, 1.1012))
    record = {
        "record_type": "V3_1_RESEARCH_SMOKE",
        "created_at": datetime.now(UTC).isoformat(),
        "policy_version": POLICY_VERSION,
        "policy_hash": POLICY_HASH,
        "snapshot": snapshot,
    }
    record["record_hash"] = stable_hash(record)
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    with ARTIFACT.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    return record


if __name__ == "__main__":
    print(json.dumps(run_smoke(), indent=2, sort_keys=True))
