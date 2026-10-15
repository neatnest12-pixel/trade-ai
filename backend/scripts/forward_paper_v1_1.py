"""LEGACY_NON_COMPLIANT_V1_1. Audit-only; never use for production collection."""
from __future__ import annotations

import os

if __name__ == "__main__" and os.getenv("NAVORA_V1_1_LEGACY_AUDIT_ONLY") != "true":
    raise SystemExit("LEGACY_NON_COMPLIANT_V1_1_DISABLED")

import argparse
import json
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd

from app.services.prediction import predict
from app.services.worker_lock import SingleWorkerLock
from app.v1_1.contract import CAMPAIGN_ID, CONFIG_HASH, SNAPSHOT_ID, freeze_trade
from app.v1_1.journal import append_v11
from app.v1_1.mt5_adapter import MT5Adapter

STATE = Path("runtime/v1_1_worker_state.json")
LOCK = Path("runtime/v1_1_forward_paper.lock")
JOURNAL = Path("certified_data/FORWARD_PAPER_JOURNAL_V1_1.jsonl")


def guard() -> None:
    if os.getenv("MT5_READ_ONLY", "").lower() != "true":
        raise RuntimeError("MT5_READ_ONLY must remain true")
    if os.getenv("TRADING_EXECUTION_ENABLED", "").lower() != "false":
        raise RuntimeError("TRADING_EXECUTION_ENABLED must remain false")


def write_state(values: dict[str, object]) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(values, sort_keys=True), encoding="utf-8")


def decision_record(adapter: MT5Adapter) -> tuple[dict[str, object], object]:
    candles = adapter.recent_completed_candles("M1", 500)
    if len(candles) < 210:
        raise RuntimeError("insufficient completed candles for canonical predictor")
    frame = pd.DataFrame([{
        "timestamp": c.timestamp_utc, "open": c.open, "high": c.high, "low": c.low,
        "close": c.close, "tick_volume": c.tick_volume, "spread": c.spread,
        "real_volume": c.real_volume,
    } for c in candles])
    response, snapshot = predict(frame, "EURUSD", "1m")
    candle = candles[-1]
    decision = response.decision
    confidence = Decimal(str(response.confidence))
    kwargs = dict(prediction_id=f"v1.1-{candle.timestamp_utc.isoformat()}",
                  symbol="EURUSD", timeframe="M1",
                  prediction_timestamp=datetime.now(UTC).isoformat(),
                  candle_timestamp=candle.timestamp_utc.isoformat(),
                  decision=decision, entry_price=Decimal(str(response.entry_reference)),
                  atr_14_price=Decimal(str(snapshot["atr_14"])),
                  balance_before_trade=Decimal("2500"), confidence=confidence)
    contract = freeze_trade(**kwargs)
    record = {"type": "prediction", "campaign_id": CAMPAIGN_ID,
              "predicted_direction": "UP" if response.probability_up >= 0.5 else "DOWN",
              "reason_codes": list(response.no_trade_reasons),
              "strategy_reasons": [vote.model_dump(mode="json") for vote in response.strategy_votes],
              **contract.record()}
    return record, response


def run_once() -> dict[str, object]:
    guard()
    import MetaTrader5 as mt5
    adapter = MT5Adapter(mt5, now=lambda: datetime.now(UTC))
    try:
        if adapter.connect() != "CONNECTED":
            raise RuntimeError(f"adapter status {adapter.status}")
        record, response = decision_record(adapter)
        append_v11(JOURNAL, record)
        return {"decision": response.decision, "confidence": response.confidence,
                "reasons": response.no_trade_reasons, "candle_timestamp": record["candle_timestamp"]}
    finally:
        mt5.shutdown()


def run_continuous() -> None:
    guard()
    import MetaTrader5 as mt5
    worker_id = os.getenv("V1_1_WORKER_ID", str(uuid.uuid4()))
    lock = SingleWorkerLock(LOCK, worker_id, 30, lock_name="navora-v1-1-paper-validation")
    if not lock.acquire():
        raise RuntimeError("V1.1 worker already owns isolated lock")
    adapter = MT5Adapter(mt5, now=lambda: datetime.now(UTC))
    predicted: set[str] = set()
    try:
        if adapter.connect() != "CONNECTED":
            write_state({"status": adapter.status, "worker_id": worker_id, "campaign_id": CAMPAIGN_ID})
            return
        while True:
            if not lock.refresh():
                raise RuntimeError("V1.1 lease lost")
            candle = adapter.latest_completed_candle("M1")
            write_state({"status": "RUNNING", "worker_id": worker_id, "campaign_id": CAMPAIGN_ID,
                         "snapshot_id": SNAPSHOT_ID, "config_hash": CONFIG_HASH,
                         "last_heartbeat": datetime.now(UTC).isoformat(),
                         "last_completed_m1": candle.timestamp_utc.isoformat() if candle else None})
            if candle and candle.timestamp_utc.isoformat() not in predicted:
                record, _ = decision_record(adapter)
                append_v11(JOURNAL, record)
                predicted.add(candle.timestamp_utc.isoformat())
            time.sleep(5)
    finally:
        mt5.shutdown()
        lock.release()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run_once() if args.once else run_continuous(), default=str))

