"""Run exactly two prospective V3.5 EURUSD completed-M1 smoke cycles."""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.v3_5.live_adapter import PreflightError, run_two_cycle_smoke


if __name__ == "__main__":
    try:
        result = run_two_cycle_smoke()
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
    except PreflightError as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc), "safe_to_train": False,
                          "safe_to_continue_certified_collection": False}, indent=2))
        raise SystemExit(2)
