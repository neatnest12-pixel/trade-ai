"""Launch the independent read-only V3.5 EURUSD outcome resolver."""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.v3_5.outcome_resolver import _broker_offset_seconds, run_resolver

if __name__ == "__main__":
    _broker_offset_seconds()
    os.environ.update({"APP_ENV":"paper","MT5_READ_ONLY":"true","TRADING_EXECUTION_ENABLED":"false",
        "DEMO_EXECUTION_ENABLED":"false","REAL_EXECUTION_ENABLED":"false","FIT_MODEL":"0","ORDER_SEND":"0"})
    try:
        run_resolver()
    except Exception as exc:
        print(json.dumps({"status":"FAILED","reason":f"{type(exc).__name__}:{exc}",
            "fit_model":0,"order_send":0},indent=2),flush=True)
        raise SystemExit(2)
