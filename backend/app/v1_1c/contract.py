"""Frozen V1.1C campaign identity and unchanged paper-risk economics."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from decimal import Decimal

from app.core.volatility import V1_1C_VOLATILITY_BOUNDS
from app.v1_1.contract import TradeContract
from app.v1_1.contract import freeze_trade as _freeze_v11_trade

CAMPAIGN_ID = "NAVORA-QUANT-V1.1C-PAPER"
POLICY = {
    "entry": "completed_signal_candle_close", "stop": "max(atr_14_price, 0.00050)",
    "target": "2.0R", "time_exit": "fifth subsequent M1 close",
    "intrabar": "AMBIGUOUS_INTRABAR excluded from profitability",
    "risk_percentage": "0.01", "max_risk_rupees": "25",
    "eurusd_units_per_lot": "100000", "inr_per_usd": "84",
    "min_atr_pct": str(V1_1C_VOLATILITY_BOUNDS.min_atr_pct),
    "max_atr_pct": str(V1_1C_VOLATILITY_BOUNDS.max_atr_pct),
}
CONFIG_HASH = hashlib.sha256(json.dumps(POLICY, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
SNAPSHOT_ID = f"v1.1c-paper-{CONFIG_HASH[:16]}"


def freeze_trade(*, prediction_id: str, symbol: str, timeframe: str, prediction_timestamp: str,
                 candle_timestamp: str, decision: str, entry_price: Decimal, atr_14_price: Decimal,
                 balance_before_trade: Decimal, confidence: Decimal) -> TradeContract:
    """Apply unchanged V1.1 paper economics under the isolated V1.1C identity."""
    legacy = _freeze_v11_trade(
        prediction_id=prediction_id, symbol=symbol, timeframe=timeframe,
        prediction_timestamp=prediction_timestamp, candle_timestamp=candle_timestamp,
        decision=decision, entry_price=entry_price, atr_14_price=atr_14_price,
        balance_before_trade=balance_before_trade, confidence=confidence,
    )
    identity = {"prediction_id": prediction_id, "snapshot_id": SNAPSHOT_ID,
                "config_hash": CONFIG_HASH, "symbol": symbol, "timeframe": timeframe,
                "candle_timestamp": candle_timestamp, "decision": decision}
    prediction_hash = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return replace(legacy, prediction_hash=prediction_hash, snapshot_id=SNAPSHOT_ID,
                   config_hash=CONFIG_HASH)
