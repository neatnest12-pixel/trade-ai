"""Read-only V1.1 performance reporter."""
from __future__ import annotations

import json
from collections.abc import Iterable
from decimal import Decimal
from pathlib import Path
from statistics import median, pstdev
from typing import Any

from .contract import HASH, PARENT_CONFIG, PARENT_SNAPSHOT, VERSION
from .journal import PaperTradeJournalError

NOT_ENOUGH_DATA = "NOT_ENOUGH_DATA"
RR_PROFILES = ("RR_2", "RR_3", "RR_5", "RR_10")
COST_PROFILES = ("IDEALIZED", "BASE", "STRESS")


def read_report_rows(path: Path = Path("certified_data/PAPER_TRADE_JOURNAL.jsonl")) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PaperTradeJournalError("MALFORMED_PAPER_TRADE_JOURNAL") from error


def official_contracts(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, Any]] = set(); result = []
    for row in rows:
        key = (row.get("parent_prediction_id"), row.get("trade_contract_hash"))
        if row.get("record_type") != "TRADE_CONTRACT" or row.get("trade_contract_version") != VERSION:
            continue
        if row.get("trade_contract_hash") != HASH or row.get("parent_v1_snapshot_id") != PARENT_SNAPSHOT or row.get("parent_v1_config_hash") != PARENT_CONFIG or key in seen:
            continue
        seen.add(key); result.append(row)
    return result


def _metrics(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    values = [Decimal(str(row["net_r"])) for row in outcomes if row.get("net_r") is not None]
    if not values:
        return {"status": NOT_ENOUGH_DATA, "resolved": 0, "unresolved": len(outcomes)}
    wins = [v for v in values if v > 0]; losses = [v for v in values if v < 0]
    equity = peak = drawdown = Decimal("0"); win_streak = loss_streak = max_win = max_loss = 0
    for value in values:
        equity += value; peak = max(peak, equity); drawdown = max(drawdown, peak - equity)
        win_streak = win_streak + 1 if value > 0 else 0; loss_streak = loss_streak + 1 if value < 0 else 0
        max_win = max(max_win, win_streak); max_loss = max(max_loss, loss_streak)
    negative = -sum(losses, Decimal("0"))
    return {"status": "OK", "contracts": len(outcomes), "resolved": len(values), "unresolved": len(outcomes) - len(values),
            "wins": len(wins), "losses": len(losses), "tp_hit": sum(r.get("outcome") == "TP_HIT" for r in outcomes),
            "sl_hit": sum(r.get("outcome") == "SL_HIT" for r in outcomes), "expired": sum(r.get("outcome") == "EXPIRED" for r in outcomes),
            "ambiguous": sum(bool(r.get("ambiguous_intrabar")) for r in outcomes), "win_rate": Decimal(len(wins)) / len(values),
            "loss_rate": Decimal(len(losses)) / len(values), "gross_r": sum((Decimal(str(r.get("gross_r", 0))) for r in outcomes), Decimal()),
            "cost_r": sum((Decimal(str(r.get("cost_r", 0))) for r in outcomes), Decimal()), "net_r": equity,
            "expectancy": equity / len(values), "average_win_r": sum(wins, Decimal()) / len(wins) if wins else None,
            "average_loss_r": sum(losses, Decimal()) / len(losses) if losses else None,
            "profit_factor": sum(wins, Decimal()) / negative if negative else "INFINITE", "max_drawdown_r": drawdown,
            "max_drawdown_inr": drawdown * 25, "max_winning_streak": max_win, "max_losing_streak": max_loss,
            "ending_simulated_balance": Decimal("2500") + equity * 25, "median_net_r": Decimal(str(median(values))),
            "stddev_net_r": Decimal(str(pstdev(values))) if len(values) > 1 else Decimal("0")}


def report(rows: list[dict[str, Any]], rr_profile: str, cost_profile: str) -> dict[str, Any]:
    ids = {r.get("paper_trade_id") for r in official_contracts(rows)}
    outcomes = [r for r in rows if r.get("record_type") == "RR_OUTCOME" and r.get("paper_trade_id") in ids and r.get("rr_profile") == rr_profile and r.get("cost_profile") == cost_profile]
    return _metrics(outcomes)


def data_quality(rows: list[dict[str, Any]]) -> dict[str, Any]:
    contracts = official_contracts(rows); ids = {r.get("paper_trade_id") for r in contracts}; outcomes = [r for r in rows if r.get("record_type") == "RR_OUTCOME"]
    orphan = sum(r.get("paper_trade_id") not in ids for r in outcomes)
    duplicate_contracts = len(contracts) - len({(r.get("parent_prediction_id"), r.get("trade_contract_hash")) for r in contracts})
    duplicate_outcomes = len(outcomes) - len({(r.get("paper_trade_id"), r.get("rr_profile"), r.get("cost_profile")) for r in outcomes})
    return {"status": "FAIL" if orphan or duplicate_contracts or duplicate_outcomes else "PASS", "orphan_outcomes": orphan, "duplicate_contracts": duplicate_contracts, "duplicate_outcomes": duplicate_outcomes}


def skipped_summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    skipped = [row for row in rows if row.get("record_type") == "TRADE_SKIPPED"]
    return {"total": len(skipped), "RISK_METADATA_UNAVAILABLE": sum(row.get("reason") == "RISK_METADATA_UNAVAILABLE" for row in skipped),
            "RISK_TOO_SMALL_FOR_MIN_LOT": sum(row.get("reason") == "RISK_TOO_SMALL_FOR_MIN_LOT" for row in skipped),
            "other": sum(row.get("reason") not in {"RISK_METADATA_UNAVAILABLE", "RISK_TOO_SMALL_FOR_MIN_LOT"} for row in skipped)}


def sample_gate(resolved: int) -> str:
    return "INSUFFICIENT" if resolved < 20 else "EARLY" if resolved < 50 else "PRELIMINARY" if resolved < 100 else "MEANINGFUL_FORWARD_SAMPLE"


def verdict(metrics: dict[str, Any]) -> str:
    if metrics.get("status") != "OK" or metrics.get("resolved", 0) < 20:
        return "INSUFFICIENT_DATA"
    if metrics["expectancy"] <= 0:
        return "NEGATIVE_EXPECTANCY"
    return "POSITIVE_EXPECTANCY_CANDIDATE" if metrics.get("profit_factor") not in (None, "INFINITE") and metrics["profit_factor"] > 1 else "INCONCLUSIVE"
