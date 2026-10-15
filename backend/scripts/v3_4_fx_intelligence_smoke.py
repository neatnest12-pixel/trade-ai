from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.v3_2.confluence import Candle, stable_hash
from app.v3_3.system_intelligence import Tick
from app.v3_4.fx_intelligence import POLICY_HASH, POLICY_VERSION, build_fx_intelligence_snapshot

ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = ROOT / "certified_data" / "v3_4" / "V3_4_FX_INTELLIGENCE_SMOKE.jsonl"


def run_smoke() -> dict[str, object]:
    base = datetime(2026, 8, 20, 8, 0, tzinfo=UTC)
    candles = [Candle(base + timedelta(minutes=i), 1.1, 1.1005, 1.0995, 1.1 + (i % 5) * 0.0001, 100 + i, 2) for i in range(120)]
    decision = candles[-1].timestamp + timedelta(minutes=1)
    ticks = [Tick(decision - timedelta(seconds=60 - i), 1.101 + i * 0.000001, 1.1012 + i * 0.000001) for i in range(60)]
    snapshot = build_fx_intelligence_snapshot(candles, ticks, decision)
    record = {"record_type": "V3_4_FX_INTELLIGENCE_SMOKE", "created_at": datetime.now(UTC).isoformat(), "policy_version": POLICY_VERSION, "policy_hash": POLICY_HASH, "snapshot": snapshot}
    record["record_hash"] = stable_hash(record)
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    with ARTIFACT.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    return record


if __name__ == "__main__":
    print(json.dumps(run_smoke(), indent=2, sort_keys=True))
