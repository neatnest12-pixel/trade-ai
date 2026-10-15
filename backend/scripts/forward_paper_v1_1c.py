"""Single-worker, read-only V1.1C continuous paper collector."""
from __future__ import annotations

import json
import os
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pandas as pd

from app.services.worker_lock import SingleWorkerLock
from app.v1_1.contract import TradeContract, evaluate_candle
from app.v1_1.mt5_adapter import MT5Adapter
from app.v1_1c.campaign import JOURNAL_PATH, LOCK_PATH, WORKER_STATE_PATH
from app.v1_1c.contract import CAMPAIGN_ID, CONFIG_HASH, SNAPSHOT_ID, freeze_trade
from app.v1_1c.in_memory import predict_v11c
from app.v1_1c.journal import append_v11c


def guard() -> None:
    if os.getenv("MT5_READ_ONLY", "").lower() != "true":
        raise RuntimeError("MT5_READ_ONLY must remain true")
    if os.getenv("TRADING_EXECUTION_ENABLED", "").lower() != "false":
        raise RuntimeError("TRADING_EXECUTION_ENABLED must remain false")


def write_state(state: dict[str, object]) -> None:
    WORKER_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    WORKER_STATE_PATH.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")


def read_state() -> dict[str, object]:
    if WORKER_STATE_PATH.exists():
        return json.loads(WORKER_STATE_PATH.read_text(encoding="utf-8"))
    return {"balance": "2500", "peak_balance": "2500", "open_trades": [], "closed_trades": 0,
            "wins": 0, "losses": 0}


def prediction_candles() -> set[str]:
    if not JOURNAL_PATH.exists():
        return set()
    rows = [json.loads(line) for line in JOURNAL_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    return {str(row["candle_timestamp"]) for row in rows if row.get("type") == "prediction"}


def contract_from_record(record: dict[str, str | None]) -> TradeContract:
    decimal_fields = {"entry_price", "stop_loss", "target_price", "risk_distance", "reward_distance",
                      "planned_rr", "risk_percentage", "risk_rupees", "position_size_simulated",
                      "balance_before_trade", "confidence"}
    values = {key: Decimal(value) if key in decimal_fields and value is not None else value
              for key, value in record.items()}
    return TradeContract(**values)  # type: ignore[arg-type]


def frame_for(candles: list[object]) -> pd.DataFrame:
    return pd.DataFrame([{"timestamp": candle.timestamp_utc, "open": candle.open,
                          "high": candle.high, "low": candle.low, "close": candle.close,
                          "tick_volume": candle.tick_volume, "spread": candle.spread,
                          "real_volume": candle.real_volume} for candle in candles])


def make_prediction(candles: list[object], balance_before_trade: Decimal) -> tuple[dict[str, object], TradeContract | None]:
    response, snapshot = predict_v11c(frame_for(candles))
    candle = candles[-1]
    contract = freeze_trade(
        prediction_id=f"v1.1c-{candle.timestamp_utc.isoformat()}", symbol="EURUSD", timeframe="M1",
        prediction_timestamp=datetime.now(UTC).isoformat(), candle_timestamp=candle.timestamp_utc.isoformat(),
        decision=response.decision, entry_price=Decimal(str(response.entry_reference)),
        atr_14_price=Decimal(str(snapshot["atr_14"])), balance_before_trade=balance_before_trade,
        confidence=Decimal(str(response.confidence)),
    )
    ratio = snapshot["atr_14"] / response.entry_reference
    record = {"type": "prediction", "campaign_id": CAMPAIGN_ID,
              "predicted_direction": "UP" if response.probability_up >= .5 else "DOWN",
              "reason_codes": list(response.no_trade_reasons), "atr_ratio": ratio,
              "volatility_gate": "PASS" if .00008 <= ratio <= .004 else "FAIL",
              "strategy_reasons": [vote.model_dump(mode="json") for vote in response.strategy_votes],
              **contract.record()}
    return record, contract if response.decision in {"BUY", "SELL"} else None


def evaluate_open_trades(state: dict[str, object], candle: object) -> None:
    remaining = []
    balance = Decimal(str(state["balance"]))
    peak = Decimal(str(state["peak_balance"]))
    for item in state["open_trades"]:  # type: ignore[index]
        contract = contract_from_record(item["contract"])
        elapsed = int(item["elapsed_candles"]) + 1
        outcome = evaluate_candle(contract, high=Decimal(str(candle.high)), low=Decimal(str(candle.low)),
                                  close=Decimal(str(candle.close)), exit_timestamp=candle.timestamp_utc.isoformat(),
                                  elapsed_candles=elapsed, peak_balance=peak)
        if outcome.status == "OPEN":
            remaining.append({"contract": item["contract"], "elapsed_candles": elapsed})
            continue
        outcome_record = {"type": "outcome", "campaign_id": CAMPAIGN_ID, "snapshot_id": SNAPSHOT_ID,
                          "config_hash": CONFIG_HASH, "prediction_hash": outcome.prediction_hash,
                          "status": outcome.status, "exit_timestamp": outcome.exit_timestamp,
                          "exit_price": str(outcome.exit_price) if outcome.exit_price is not None else None,
                          "exit_reason": outcome.exit_reason,
                          "realized_r": str(outcome.realized_r) if outcome.realized_r is not None else None,
                          "realized_pnl_rupees": str(outcome.realized_pnl_rupees) if outcome.realized_pnl_rupees is not None else None,
                          "balance_after_trade": str(outcome.balance_after_trade) if outcome.balance_after_trade is not None else None,
                          "peak_balance": str(outcome.peak_balance) if outcome.peak_balance is not None else None,
                          "drawdown_rupees": str(outcome.drawdown_rupees) if outcome.drawdown_rupees is not None else None}
        append_v11c(JOURNAL_PATH, outcome_record)
        state["closed_trades"] = int(state["closed_trades"]) + 1
        if outcome.realized_r is not None:
            balance = outcome.balance_after_trade or balance
            peak = outcome.peak_balance or peak
            state["wins"] = int(state["wins"]) + int(outcome.realized_r > 0)
            state["losses"] = int(state["losses"]) + int(outcome.realized_r < 0)
    state["open_trades"] = remaining
    state["balance"] = str(balance)
    state["peak_balance"] = str(peak)


def run_continuous() -> None:
    guard()
    import MetaTrader5 as mt5

    worker_id = os.getenv("V1_1C_WORKER_ID", str(uuid.uuid4()))
    lock = SingleWorkerLock(LOCK_PATH, worker_id, 30, lock_name="navora-v1-1c-paper-validation")
    if not lock.acquire():
        raise RuntimeError("V1.1C worker already owns isolated lock")
    state = read_state()
    state["activation_timestamp"] = state.get("activation_timestamp", datetime.now(UTC).isoformat())
    state["worker_id"] = worker_id
    known = prediction_candles()
    adapter = MT5Adapter(mt5, now=lambda: datetime.now(UTC))
    try:
        if adapter.connect() != "CONNECTED":
            state.update({"status": adapter.status, "campaign_id": CAMPAIGN_ID})
            write_state(state)
            return
        while True:
            if not lock.refresh():
                raise RuntimeError("V1.1C lease lost")
            candle = adapter.latest_completed_candle("M1")
            state.update({"status": "RUNNING", "campaign_id": CAMPAIGN_ID, "snapshot_id": SNAPSHOT_ID,
                          "config_hash": CONFIG_HASH, "last_heartbeat": datetime.now(UTC).isoformat(),
                          "last_completed_m1": candle.timestamp_utc.isoformat() if candle else None})
            if candle:
                candle_key = candle.timestamp_utc.isoformat()
                if state.get("last_evaluated_candle") != candle_key:
                    evaluate_open_trades(state, candle)
                    state["last_evaluated_candle"] = candle_key
                if candle.timestamp_utc.isoformat() not in known:
                    candles = adapter.recent_completed_candles("M1", 500)
                    if len(candles) >= 210:
                        record, contract = make_prediction(candles, Decimal(str(state["balance"])))
                        append_v11c(JOURNAL_PATH, record)
                        known.add(candle.timestamp_utc.isoformat())
                        if contract:
                            state["open_trades"].append({"contract": contract.record(), "elapsed_candles": 0})
            write_state(state)
            time.sleep(5)
    finally:
        mt5.shutdown()
        lock.release()


if __name__ == "__main__":
    run_continuous()
