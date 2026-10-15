from __future__ import annotations
from app.mt5_connection import initialize_checked, shutdown as shutdown_mt5
from app.core.mt5_offset import configured_offset_seconds

import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from app.v3_4.mt5_time import (
    TIMEFRAME_SECONDS,
    normalize_mt5_epoch,
    require_not_future,
    require_recent,
)
from app.v3_4.providers.adapters.bea import BeaApiAdapter
from app.v3_4.providers.adapters.bls import BlsApiAdapter
from app.v3_4.providers.adapters.bundesbank import BundesbankYieldAdapter
from app.v3_4.providers.adapters.ecb import EcbApiAdapter
from app.v3_4.providers.adapters.fred import FredApiAdapter

EXPECTED_POLICY_VERSION = "v3.4-complete-fx-intelligence-r1"
EXPECTED_POLICY_HASH = "c056fac9dca4cf331dc94c52ea3e46dc7e6257e9fd0009506fbf808ce795ee58"


def _provider_ok(record: dict[str, Any]) -> bool:
    return record.get("state") != "UNAVAILABLE" and not str(
        record.get("reason", "")
    ).endswith(("FETCH_FAILED", "KEY_MISSING"))


def main() -> int:
    import MetaTrader5 as mt5

    from app.v3_4.fx_intelligence import POLICY_HASH, POLICY_VERSION

    now = datetime.now(UTC)
    epoch = time.time()
    offset_seconds = configured_offset_seconds()
    report: dict[str, Any] = {
        "checked_at": now.isoformat(),
        "system_clock": {
            "datetime_utc": now.isoformat(),
            "time_time": epoch,
            "difference_seconds": now.timestamp() - epoch,
        },
        "configured_mt5_server_utc_offset_seconds": offset_seconds,
        "policy": {
            "version": POLICY_VERSION,
            "hash": POLICY_HASH,
            "pass": POLICY_VERSION == EXPECTED_POLICY_VERSION
            and POLICY_HASH == EXPECTED_POLICY_HASH,
        },
    }
    try:
        report['connection'] = initialize_checked(mt5)
        initialized = True
    except RuntimeError as exc:
        report.update(mt5_initialized=False, status='FAIL', connection_error=str(exc))
        print(json.dumps(report, indent=2))
        return 2
    report["mt5_initialized"] = initialized
    mt5_pass = initialized
    if initialized:
        try:
            account = mt5.account_info()
            terminal = mt5.terminal_info()
            report["mt5"] = {
                "server": getattr(account, "server", None),
                "currency": getattr(account, "currency", None),
                "trade_mode": getattr(account, "trade_mode", None),
                "connected": bool(getattr(terminal, "connected", False)),
            }
            mt5_pass = bool(
                account
                and account.server == "ICMarketsSC-Demo"
                and account.currency == "USD"
                and account.trade_mode == 0
                and getattr(terminal, "connected", False)
            )
            mapping = {
                "M1": mt5.TIMEFRAME_M1,
                "M5": mt5.TIMEFRAME_M5,
                "M15": mt5.TIMEFRAME_M15,
                "H1": mt5.TIMEFRAME_H1,
                "H6": mt5.TIMEFRAME_H6,
                "H12": mt5.TIMEFRAME_H12,
            }
            symbols: dict[str, Any] = {}
            for symbol_name in ("EURUSD", "XAUUSD"):
                info = mt5.symbol_info(symbol_name)
                tick = mt5.symbol_info_tick(symbol_name)
                symbol_report: dict[str, Any] = {
                    "available": info is not None,
                    "bid_ask_valid": False,
                }
                if tick is not None:
                    raw_time_msc = getattr(tick, "time_msc", None)
                    tick_epoch = (
                        float(raw_time_msc) / 1000
                        if raw_time_msc
                        else float(tick.time)
                    )
                    normalized_tick = normalize_mt5_epoch(
                        tick_epoch,
                        offset_seconds=offset_seconds,
                        source=f"{symbol_name}_SYMBOL_INFO_TICK",
                    )
                    require_not_future(
                        normalized_tick.timestamp,
                        system_utc=datetime.now(UTC),
                        source=normalized_tick.source,
                    )
                    tick_age_seconds = (
                        datetime.now(UTC) - normalized_tick.timestamp
                    ).total_seconds()
                    tick_fresh = True
                    try:
                        require_recent(
                            normalized_tick.timestamp,
                            system_utc=datetime.now(UTC),
                            source=normalized_tick.source,
                        )
                    except RuntimeError:
                        tick_fresh = False
                    symbol_report.update(
                        {
                            "bid_ask_valid": float(tick.bid) > 0
                            and float(tick.ask) >= float(tick.bid),
                            "raw_tick_timestamp": normalized_tick.raw_timestamp.isoformat(),
                            "normalized_tick_timestamp": normalized_tick.timestamp.isoformat(),
                            "tick_skew_seconds": (
                                normalized_tick.timestamp - datetime.now(UTC)
                            ).total_seconds(),
                            "tick_age_seconds": tick_age_seconds,
                            "tick_fresh": tick_fresh,
                        }
                    )
                timeframes: dict[str, Any] = {}
                for label, timeframe in mapping.items():
                    rates = mt5.copy_rates_from_pos(symbol_name, timeframe, 1, 1)
                    available = rates is not None and len(rates) == 1
                    detail: dict[str, Any] = {"available": available, "pass": False}
                    if available:
                        stamp = normalize_mt5_epoch(
                            float(rates[0]["time"]),
                            offset_seconds=offset_seconds,
                            source=f"{symbol_name}_{label}_BAR",
                        )
                        completed_at = stamp.timestamp + timedelta(
                            seconds=TIMEFRAME_SECONDS[label]
                        )
                        detail.update(
                            {
                                "raw_timestamp": stamp.raw_timestamp.isoformat(),
                                "normalized_timestamp": stamp.timestamp.isoformat(),
                                "completed_at": completed_at.isoformat(),
                                "pass": completed_at <= datetime.now(UTC),
                            }
                        )
                    timeframes[label] = detail
                symbol_report["timeframes"] = timeframes
                symbol_report["pass"] = bool(
                    symbol_report["available"]
                    and symbol_report["bid_ask_valid"]
                    and symbol_report.get("tick_fresh", False)
                    and all(item["pass"] for item in timeframes.values())
                )
                symbols[symbol_name] = symbol_report
            report["symbols"] = symbols
            mt5_pass = mt5_pass and all(item["pass"] for item in symbols.values())
        finally:
            shutdown_mt5(mt5)

    providers = {
        "FRED": FredApiAdapter().normalized_rate("US_10Y"),
        "BLS": BlsApiAdapter().fetch_latest("CPI"),
        "BEA": BeaApiAdapter().fetch_latest("GDP"),
        "ECB": EcbApiAdapter().fetch_latest("ECB_RATE"),
        "Bundesbank_DE_2Y": BundesbankYieldAdapter().normalized_yield("DE_2Y"),
        "Bundesbank_DE_5Y": BundesbankYieldAdapter().normalized_yield("DE_5Y"),
    }
    report["providers"] = {
        name: {
            "pass": _provider_ok(record),
            "state": record.get("state", "AVAILABLE"),
            "quality": record.get("quality") or record.get("data_quality"),
            "decision_time_safe": record.get(
                "decision_time_safe",
                record.get("no_lookahead_safe_for_decision_join"),
            ),
            "timestamp": record.get("source_timestamp")
            or record.get("timestamp")
            or record.get("effective_or_release_timestamp"),
            "retrieved_at": record.get("retrieved_at"),
            "reason": record.get("reason"),
        }
        for name, record in providers.items()
    }
    providers_pass = all(item["pass"] for item in report["providers"].values())
    system_pass = abs(float(report["system_clock"]["difference_seconds"])) < 0.5
    report["checks"] = {
        "system_clock": system_pass,
        "mt5": mt5_pass,
        "providers": providers_pass,
        "policy": report["policy"]["pass"],
    }
    report["pass"] = all(report["checks"].values())
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
