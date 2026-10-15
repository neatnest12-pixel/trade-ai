"""Manual-only entry point for the separately-owned demo execution worker.

It deliberately does not start itself and defaults to PAPER / kill-switch-on.
An operations owner must separately authorize and schedule it after the smoke
phase.  It never imports or changes ``forward_paper.py``.
"""
from app.demo_execution import ExecutionConfig


def main() -> int:
    config = ExecutionConfig.from_env()
    if config.execution_mode != "DEMO_EXECUTION" or config.kill_switch:
        print("Demo execution is disabled; no MT5 operation performed.")
        return 0
    raise RuntimeError("A separately authorized worker integration is required before execution.")


if __name__ == "__main__":
    raise SystemExit(main())
