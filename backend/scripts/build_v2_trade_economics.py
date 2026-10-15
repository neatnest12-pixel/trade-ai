"""Build the isolated V2 offline trade-economics dataset.

Reads certified V1 predictions and certified candles only. It never opens MT5,
trains a model, or writes any V1/V1.1 journal.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
from bisect import bisect_left
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREDICTIONS = ROOT / "certified_data" / "FORWARD_PAPER_JOURNAL.jsonl"
CANDLES = Path(os.environ.get("V2_M1_PATH", ROOT / "certified_data" / "datasets" / "EURUSD-1m-bdee314433d1" / "data.csv"))
OUTPUT = ROOT / "certified_data" / "V2_TRADE_ECONOMICS_DATASET.jsonl"
POLICY = "v2-offline-stop-atr14x1.5-stopfirst-hold60-spread-observed"
RRS = (1, 1.5, 2, 3, 5)

def iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)

def sha(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def main() -> None:
    rows = [json.loads(line) for line in PREDICTIONS.read_text(encoding="utf-8").splitlines() if line.strip()]
    predictions = [r for r in rows if r.get("type") == "prediction" and r.get("decision") in {"BUY", "SELL"}]
    with CANDLES.open(newline="", encoding="utf-8") as handle:
        candles = sorted(list(csv.DictReader(handle)), key=lambda r: r["timestamp"])
    candle_times = [iso(r["timestamp"]) for r in candles]
    valid = []
    for prediction in predictions:
        signal = iso(prediction["candle_timestamp"])
        index = bisect_left(candle_times, signal)
        if index >= len(candles):
            index = None
        if index is None or index + 60 >= len(candles) or index < 15:
            continue
        history = candles[index - 14:index]
        trs = []
        for j, row in enumerate(history):
            previous = float(candles[index - 15 + j]["close"])
            high, low = float(row["high"]), float(row["low"])
            trs.append(max(high - low, abs(high - previous), abs(low - previous)))
        atr = sum(trs) / len(trs)
        entry_row = candles[index + 1]
        entry = float(entry_row["open"])
        side = prediction["decision"]
        stop = entry - atr * 1.5 if side == "BUY" else entry + atr * 1.5
        spread = float(entry_row.get("spread") or 0) / 100000
        base = {"parent_prediction_id": prediction["record_hash"], "parent_record_hash": prediction["record_hash"],
                "v1_snapshot_id": prediction.get("v1_snapshot_id"), "v1_config_hash": prediction.get("v1_config_hash"),
                "signal_timestamp": prediction["prediction_time"], "candle_timestamp": prediction["candle_timestamp"],
                "decision": side, "buy_probability": prediction.get("probability_up"), "sell_probability": prediction.get("probability_down"),
                "entry": entry, "stop": stop, "atr14": atr, "atr_ratio": atr / entry, "spread": spread,
                "bid": entry, "ask": entry + spread, "utc_hour": signal.hour, "weekday": signal.weekday(),
                "session": "ASIA" if signal.hour < 8 else "LONDON" if signal.hour < 13 else "NEW_YORK",
                "m1_trend": "UP" if float(history[-1]["close"]) > float(history[0]["close"]) else "DOWN",
                "m5_context": "UNAVAILABLE_FROM_M1_ONLY", "m15_context": "UNAVAILABLE_FROM_M1_ONLY",
                "evaluation_policy_hash": hashlib.sha256(POLICY.encode()).hexdigest()}
        for rr in RRS:
            target = entry + (atr * 1.5 * rr if side == "BUY" else -atr * 1.5 * rr)
            outcome, exit_price, hold = "EXPIRED", float(candles[index + 60]["close"]), 60
            for offset, future in enumerate(candles[index + 1:index + 61], 1):
                high, low = float(future["high"]), float(future["low"])
                hit_stop = low <= stop if side == "BUY" else high >= stop
                hit_target = high >= target if side == "BUY" else low <= target
                if hit_stop or hit_target:
                    outcome = "SL_HIT" if hit_stop else "TP_HIT"
                    exit_price, hold = (stop, offset) if outcome == "SL_HIT" else (target, offset)
                    break
            gross = ((exit_price - entry) / (entry - stop)) if side == "BUY" else ((entry - exit_price) / (stop - entry))
            record = {**base, "rr": rr, "tp": target, "exit": exit_price, "holding_candles": hold,
                      "outcome": outcome, "gross_r": gross, "spread_cost_r": 0.0, "fixed_cost_r": 0.0,
                      "cost_r": 0.0, "net_r": gross, "input_source": "certified_m1_only"}
            record["row_hash"] = sha(record)
            valid.append(record)
    OUTPUT.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in valid), encoding="utf-8")
    print(json.dumps({"rows": len(valid), "parents": len({r['parent_prediction_id'] for r in valid}), "buy": sum(r['decision']=='BUY' for r in valid), "sell": sum(r['decision']=='SELL' for r in valid), "output": str(OUTPUT)}))

if __name__ == "__main__": main()
