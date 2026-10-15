"""Compatibility entrypoint for the bounded, certified research-only resolver."""
from __future__ import annotations
import os
from v34_recertify import main as certified_main


def main() -> int:
    # One-shot feasibility does not bypass the deterministic pilot commit gate.
    mode = "feasibility" if os.getenv("V3_OUTCOME_RESOLVER_ONCE", "false").lower() == "true" else "serve"
    return certified_main([mode])


if __name__ == "__main__":
    raise SystemExit(main())
