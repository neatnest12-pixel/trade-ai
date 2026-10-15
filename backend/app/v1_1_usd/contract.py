from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any

PARENT_SNAPSHOT = "v1-paper-r4-0d891a2356e35464"
PARENT_CONFIG = "0d891a2356e35464dbe3ba9f51c50fe240e40feff84fe4008d8b1564b55f9eac"
VERSION = "v1.1-paper-trade-usd-01"
FROZEN_AT = "2026-08-18T14:45:00+00:00"
REFERENCE_CAPITAL_USD = Decimal("30.00")
REFERENCE_RISK_USD = Decimal("0.30")
POLICY: dict[str, Any] = {"entry": "NEXT_M1_OPEN_APPROXIMATION", "atr_period": 14, "atr_multiplier": "1.5", "max_hold_m1": 60, "rr": [2, 3, 5, 10], "intrabar": "STOP_FIRST", "reference_currency": "USD", "reference_capital_usd": "30.00", "reference_risk_usd": "0.30", "costs": {"IDEALIZED": "0", "BASE": "observed_spread+0.05R", "STRESS": "observed_spread*1.5+0.15R"}, "start": FROZEN_AT}
HASH = hashlib.sha256(json.dumps(POLICY, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def eligible(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    official: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        if row.get("type") != "prediction" or row.get("v1_snapshot_id") != PARENT_SNAPSHOT or row.get("v1_config_hash") != PARENT_CONFIG or row.get("decision") not in {"BUY", "SELL"} or row.get("candle_timestamp", "") <= FROZEN_AT:
            continue
        identity = (row.get("symbol"), row.get("timeframe"), row.get("candle_timestamp"))
        if identity not in official or row.get("prediction_time", "") < official[identity].get("prediction_time", ""):
            official[identity] = row
    return list(official.values())


def journal_fields() -> dict[str, str]:
    return {"trade_contract_version": VERSION, "trade_contract_hash": HASH, "trade_contract_frozen_at": FROZEN_AT, "reference_currency": "USD", "reference_capital_usd": "30.00", "reference_risk_usd": "0.30"}
