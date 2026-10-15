"""Certified V1.2 prospective $50K paper/shadow collector; never executes broker orders."""

# ruff: noqa: E402
from __future__ import annotations

import json
import os
import signal
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.worker_lock import SingleWorkerLock
from app.v1_2.demo_shadow import HASH, RISK_PROFILES, VERSION, apply_net_r, require_demo_usd
from app.v1_2.shadow import (
    JOURNAL,
    PROSPECTIVE_START,
    append,
    branch_state,
    contract,
    eligible,
    priced,
    read,
)

PARENT = ROOT / "certified_data/FORWARD_PAPER_JOURNAL.jsonl"
STATE = ROOT / "runtime/v1_2_50k_shadow_state.json"
LOCK = ROOT / "runtime/v1_2_50k_shadow.lock"
LOCK_NAME = "navora-v1-2-50k-shadow"


def now() -> str:
    return datetime.now(UTC).isoformat()


class Runtime:
    def __init__(self, mt5: Any | None = None):
        self.mt5 = mt5
        self.id = str(uuid.uuid4())
        self.stop = False
        self.lock = SingleWorkerLock(LOCK, self.id, 60, LOCK_NAME)
        self.started = now()

    def state(self, status: str, **extra: Any) -> None:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(
            json.dumps(
                {
                    "status": status,
                    "processor_id": self.id,
                    "startup_timestamp": self.started,
                    "last_heartbeat": now(),
                    "policy_version": VERSION,
                    "policy_hash": HASH,
                    "prospective_start": PROSPECTIVE_START,
                    "journal": str(JOURNAL),
                    "last_error": None,
                    **extra,
                },
                sort_keys=True,
            ),
            encoding="utf8",
        )

    def preflight(self) -> None:
        if (
            any(
                os.getenv(k, "false").lower() != "false"
                for k in (
                    "TRADING_EXECUTION_ENABLED",
                    "DEMO_EXECUTION_ENABLED",
                    "REAL_EXECUTION_ENABLED",
                )
            )
            or os.getenv("MT5_READ_ONLY", "").lower() != "true"
        ):
            raise RuntimeError("UNSAFE_EXECUTION_FLAGS")
        if (
            VERSION != "v1.2-demo-50k-01"
            or HASH != "f7acfe4b33b1cc1d2ecfa7d7d3d084dd0cf57b4ca27ea4cffdf63f3f355dbf7e"
        ):
            raise RuntimeError("POLICY_MISMATCH")
        [json.loads(x) for x in PARENT.read_text().splitlines() if x.strip()]
        read(JOURNAL)
        if self.mt5 is None:
            self.mt5 = mt5
        if not self.mt5.initialize():
            raise RuntimeError("MT5_INIT_FAILED")
        account = self.mt5.account_info()
        require_demo_usd(account)
        if account.server != "ICMarketsSC-Demo" or self.mt5.symbol_info("EURUSD") is None:
            raise RuntimeError("MT5_PREFLIGHT_FAILED")

    def bars(self) -> list[dict[str, Any]]:
        raw = self.mt5.copy_rates_from_pos("EURUSD", self.mt5.TIMEFRAME_M1, 1, 2000)
        if raw is None:
            raise ConnectionError("PROVIDER_UNAVAILABLE")
        return [
            {
                "timestamp": datetime.fromtimestamp(float(x["time"]), UTC).isoformat(),
                "open": str(x["open"]),
                "high": str(x["high"]),
                "low": str(x["low"]),
                "close": str(x["close"]),
                "spread": str(x["spread"]),
            }
            for x in raw
        ]

    def cycle(self) -> None:
        if not self.lock.refresh():
            raise RuntimeError("LEASE_LOST")
        parents = eligible([json.loads(x) for x in PARENT.read_text().splitlines() if x.strip()])
        rows = read(JOURNAL)
        known = {
            r.get("parent_prediction_id")
            for r in rows
            if r.get("record_type") in {"SHADOW_CONTRACT", "SHADOW_SKIPPED"}
        }
        bars = self.bars()
        info = self.mt5.symbol_info("EURUSD")
        meta = {
            k: str(getattr(info, k))
            for k in (
                "point",
                "trade_tick_size",
                "trade_tick_value",
                "volume_min",
                "volume_step",
                "volume_max",
            )
        }
        for p in parents:
            if p["record_hash"] in known:
                continue
            t = (
                datetime.fromisoformat(p["candle_timestamp"].replace("Z", "+00:00"))
                + timedelta(minutes=1)
            ).isoformat()
            i = next((n for n, b in enumerate(bars) if b["timestamp"] == t), None)
            if i is None or i < 15:
                continue
            h = bars[i - 15 : i]
            atr = sum(
                (
                    max(
                        Decimal(x["high"]) - Decimal(x["low"]),
                        abs(Decimal(x["high"]) - Decimal(q["close"])),
                        abs(Decimal(x["low"]) - Decimal(q["close"])),
                    )
                    for q, x in zip(h[:-1], h[1:], strict=True)
                ),
                Decimal(),
            ) / Decimal(14)
            c = contract(
                p,
                Decimal(bars[i]["open"]),
                atr,
                Decimal(bars[i]["spread"]) * Decimal(meta["point"]),
                meta,
            )
            c["created_at"] = now()
            c["entry_timestamp"] = t
            append(c)
            rows.append(c)
        for c in [r for r in rows if r.get("record_type") == "SHADOW_CONTRACT"]:
            future = [b for b in bars if b["timestamp"] >= c["entry_timestamp"]]
            for rr in ("RR_2", "RR_3", "RR_5", "RR_10"):
                for cost in ("IDEALIZED", "BASE", "STRESS"):
                    outcome = priced(c, rr, cost, future)
                    if outcome is None:
                        continue
                    for profile in RISK_PROFILES:
                        state = branch_state(read(JOURNAL), profile, rr, cost)
                        try:
                            new, performance = apply_net_r(
                                state, profile, Decimal(str(outcome["net_r"]))
                            )
                        except Exception as error:
                            append(
                                {
                                    "record_type": "SHADOW_GUARD",
                                    "created_at": now(),
                                    "shadow_trade_id": c["shadow_trade_id"],
                                    "profile": profile,
                                    "rr_profile": rr,
                                    "cost_profile": cost,
                                    "reason": str(error),
                                }
                            )
                            continue
                        append(
                            {
                                "record_type": "SHADOW_OUTCOME",
                                "created_at": now(),
                                "shadow_trade_id": c["shadow_trade_id"],
                                "parent_prediction_id": c["parent_prediction_id"],
                                "profile": profile,
                                "rr_profile": rr,
                                "balance_before": state.balance,
                                "balance_after": new.balance,
                                "risk_usd": performance["risk_usd"],
                                "net_pnl_usd": performance["net_pnl_usd"],
                                **outcome,
                            }
                        )
        self.state("RUNNING")

    def run(self, once: bool = False) -> int:
        try:
            self.preflight()
        except Exception as e:
            self.state("FAILED", last_error=str(e))
            return 2
        if not self.lock.acquire():
            self.state("ALREADY_RUNNING")
            self.mt5.shutdown()
            return 0
        try:
            while not self.stop:
                try:
                    self.cycle()
                except ConnectionError as e:
                    self.state("RUNNING", last_error=str(e))
                if once:
                    break
                time.sleep(5)
            return 0
        except Exception as e:
            self.state("FAILED", last_error=str(e))
            return 1
        finally:
            self.lock.release()
            self.mt5.shutdown()
            self.state("STOPPED")


def main() -> int:
    r = Runtime()
    signal.signal(signal.SIGINT, lambda *_: setattr(r, "stop", True))
    return r.run(os.getenv("V1_2_50K_PROCESSOR_ONCE", "false").lower() == "true")


if __name__ == "__main__":
    raise SystemExit(main())
