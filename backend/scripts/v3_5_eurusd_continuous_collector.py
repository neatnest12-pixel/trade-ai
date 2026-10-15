"""Start the single-instance, read-only EURUSD V3.5 prospective collector."""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.v3_5.live_adapter import _broker_offset_seconds, run_continuous_collection


if __name__ == "__main__":
    _broker_offset_seconds()
    os.environ.update({"APP_ENV": "paper", "MT5_READ_ONLY": "true", "TRADING_EXECUTION_ENABLED": "false",
                       "DEMO_EXECUTION_ENABLED": "false", "REAL_EXECUTION_ENABLED": "false",
                       "FIT_MODEL": "0", "ORDER_SEND": "0"})
    try:
        result = run_continuous_collection()
        if result is not None:
            print(json.dumps(result, indent=2, sort_keys=True, default=str), flush=True)
    except Exception as exc:
        print(json.dumps({"status": "FAILED", "reason": f"{type(exc).__name__}:{exc}",
                          "fit_model": 0, "order_send": 0}, indent=2), flush=True)
        raise SystemExit(2)
