from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.v3_2.confluence import Candle, stable_hash
from app.v3_3.system_intelligence import POLICY_HASH, POLICY_VERSION, Tick, build_system_snapshot

ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = ROOT / "certified_data" / "v3_3" / "V3_3_SYSTEM_SMOKE.jsonl"


def _candles(count: int = 90) -> list[Candle]:
    base = datetime(2026, 8, 20, 8, 0, tzinfo=UTC)
    price = 1.10
    rows = []
    for idx in range(count):
        direction = 1 if idx % 9 < 6 else -1
        open_price = price
        close = open_price + direction * 0.0002
        rows.append(Candle(base + timedelta(minutes=idx), open_price, max(open_price, close) + 0.00025, min(open_price, close) - 0.00025, close, 100 + idx, 2))
        price = close
    return rows


def run_smoke() -> dict[str, object]:
    candles = _candles()
    decision = candles[-1].timestamp + timedelta(minutes=1)
    ticks = [Tick(decision - timedelta(seconds=60 - idx), 1.1010 + idx * 0.000001, 1.1012 + idx * 0.000001) for idx in range(60)]
    snapshot = build_system_snapshot({"M1": candles, "M5": candles, "M15": candles, "H1": candles, "H6": candles, "H12": candles}, ticks, decision)
    record = {"record_type": "V3_3_SYSTEM_SMOKE", "created_at": datetime.now(UTC).isoformat(), "policy_version": POLICY_VERSION, "policy_hash": POLICY_HASH, "snapshot": snapshot}
    record["record_hash"] = stable_hash(record)
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    with ARTIFACT.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    return record


if __name__ == "__main__":
    print(json.dumps(run_smoke(), indent=2, sort_keys=True))
