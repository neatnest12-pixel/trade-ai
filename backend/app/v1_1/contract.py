"""Frozen paper-only V1.1 trade-economics contract."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from decimal import ROUND_DOWN, Decimal
from typing import Any

CAMPAIGN_ID = "NAVORA-QUANT-V1.1-PAPER"
STARTING_BALANCE = Decimal("2500")
RISK_PERCENTAGE = Decimal("0.01")
MAX_RISK_RUPEES = Decimal("25")
EURUSD_UNITS_PER_LOT = Decimal("100000")
INR_PER_USD = Decimal("84")
MIN_STOP_DISTANCE = Decimal("0.00050")
TIME_EXIT_CANDLES = 5
POLICY = {"entry": "completed_signal_candle_close", "stop": "max(atr_14_price, 0.00050)",
          "target": "2.0R", "time_exit": "fifth subsequent M1 close",
          "intrabar": "AMBIGUOUS_INTRABAR excluded from profitability",
          "risk_percentage": "0.01", "max_risk_rupees": "25",
          "eurusd_units_per_lot": "100000", "inr_per_usd": "84"}
CONFIG_HASH = hashlib.sha256(json.dumps(POLICY, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
SNAPSHOT_ID = f"v1.1-paper-{CONFIG_HASH[:16]}"


@dataclass(frozen=True)
class TradeContract:
    prediction_id: str
    prediction_hash: str
    snapshot_id: str
    config_hash: str
    symbol: str
    timeframe: str
    prediction_timestamp: str
    candle_timestamp: str
    decision: str
    entry_price: Decimal | None
    stop_loss: Decimal | None
    target_price: Decimal | None
    risk_distance: Decimal | None
    reward_distance: Decimal | None
    planned_rr: Decimal | None
    risk_percentage: Decimal
    risk_rupees: Decimal | None
    position_size_simulated: Decimal | None
    balance_before_trade: Decimal
    confidence: Decimal

    def record(self) -> dict[str, str | None]:
        return {key: str(value) if value is not None else None for key, value in asdict(self).items()}


@dataclass(frozen=True)
class TradeOutcome:
    prediction_hash: str
    status: str
    exit_price: Decimal | None
    exit_timestamp: str | None
    exit_reason: str
    realized_r: Decimal | None
    realized_pnl_rupees: Decimal | None
    balance_after_trade: Decimal | None
    peak_balance: Decimal | None
    drawdown_rupees: Decimal | None
    drawdown_percentage: Decimal | None


def freeze_trade(*, prediction_id: str, symbol: str, timeframe: str, prediction_timestamp: str,
                 candle_timestamp: str, decision: str, entry_price: Decimal, atr_14_price: Decimal,
                 balance_before_trade: Decimal, confidence: Decimal) -> TradeContract:
    if decision not in {"BUY", "SELL", "NO_TRADE"}:
        raise ValueError("invalid decision")
    identity: dict[str, Any] = {"prediction_id": prediction_id, "snapshot_id": SNAPSHOT_ID,
        "config_hash": CONFIG_HASH, "symbol": symbol, "timeframe": timeframe,
        "candle_timestamp": candle_timestamp, "decision": decision}
    prediction_hash = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if decision == "NO_TRADE":
        return TradeContract(prediction_id, prediction_hash, SNAPSHOT_ID, CONFIG_HASH, symbol, timeframe, prediction_timestamp, candle_timestamp, decision, None, None, None, None, None, None, RISK_PERCENTAGE, None, None, balance_before_trade, confidence)
    distance = max(atr_14_price, MIN_STOP_DISTANCE)
    if distance <= 0 or entry_price <= 0 or balance_before_trade <= 0:
        raise ValueError("positive prices and balance required")
    sign = Decimal("1") if decision == "BUY" else Decimal("-1")
    risk = min(balance_before_trade * RISK_PERCENTAGE, MAX_RISK_RUPEES)
    size = (risk / (distance * EURUSD_UNITS_PER_LOT * INR_PER_USD)).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
    return TradeContract(prediction_id, prediction_hash, SNAPSHOT_ID, CONFIG_HASH, symbol, timeframe, prediction_timestamp, candle_timestamp, decision, entry_price, entry_price - sign * distance, entry_price + sign * distance * 2, distance, distance * 2, Decimal("2"), RISK_PERCENTAGE, risk, size, balance_before_trade, confidence)


def close_trade(contract: TradeContract, reason: str, exit_price: Decimal, exit_timestamp: str, peak_balance: Decimal) -> TradeOutcome:
    assert contract.entry_price is not None and contract.risk_distance is not None and contract.risk_rupees is not None
    sign = Decimal("1") if contract.decision == "BUY" else Decimal("-1")
    realized_r = sign * (exit_price - contract.entry_price) / contract.risk_distance
    pnl = realized_r * contract.risk_rupees
    balance = contract.balance_before_trade + pnl
    peak = max(peak_balance, balance)
    drawdown = peak - balance
    return TradeOutcome(contract.prediction_hash, reason, exit_price, exit_timestamp, reason, realized_r, pnl, balance, peak, drawdown, drawdown / peak * 100)


def evaluate_candle(contract: TradeContract, *, high: Decimal, low: Decimal, close: Decimal, exit_timestamp: str, elapsed_candles: int, peak_balance: Decimal) -> TradeOutcome:
    if contract.decision == "NO_TRADE":
        return TradeOutcome(contract.prediction_hash, "INVALID", None, None, "NO_TRADE", None, None, None, None, None, None)
    assert contract.stop_loss is not None and contract.target_price is not None
    stop = low <= contract.stop_loss if contract.decision == "BUY" else high >= contract.stop_loss
    target = high >= contract.target_price if contract.decision == "BUY" else low <= contract.target_price
    if stop and target:
        return TradeOutcome(contract.prediction_hash, "AMBIGUOUS_INTRABAR", None, exit_timestamp, "AMBIGUOUS_INTRABAR", None, None, None, None, None, None)
    if target:
        return close_trade(contract, "TAKE_PROFIT", contract.target_price, exit_timestamp, peak_balance)
    if stop:
        return close_trade(contract, "STOP_LOSS", contract.stop_loss, exit_timestamp, peak_balance)
    if elapsed_candles >= TIME_EXIT_CANDLES:
        return close_trade(contract, "TIME_EXIT", close, exit_timestamp, peak_balance)
    return TradeOutcome(contract.prediction_hash, "OPEN", None, None, "OPEN", None, None, None, None, None, None)



