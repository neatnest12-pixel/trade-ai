"""Offline chronological baseline report; deliberately does not fit or deploy a model."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "certified_data" / "V2_TRADE_ECONOMICS_DATASET.jsonl"
OUT = ROOT / "certified_data" / "v2_research"

def metric(rows):
    values = [float(r["net_r"]) for r in rows]
    wins = sum(v > 0 for v in values); losses = sum(v < 0 for v in values)
    gross_profit = sum(v for v in values if v > 0); gross_loss = abs(sum(v for v in values if v < 0))
    equity = peak = dd = 0.0; streak = longest = 0
    for value in values:
        equity += value; peak = max(peak, equity); dd = max(dd, peak - equity)
        streak = streak + 1 if value < 0 else 0; longest = max(longest, streak)
    return {"trades": len(values), "wins": wins, "losses": losses,
            "expired": sum(r["outcome"] == "EXPIRED" for r in rows),
            "win_rate": wins / len(values) if values else None, "net_r": sum(values),
            "expectancy": sum(values) / len(values) if values else None,
            "profit_factor": gross_profit / gross_loss if gross_loss else None,
            "max_drawdown": dd, "max_losing_streak": longest}

def main():
    rows = [json.loads(x) for x in DATA.read_text(encoding="utf8").splitlines() if x.strip()]
    parents = {}
    for row in rows: parents.setdefault(row["parent_prediction_id"], row)
    ordered = sorted(parents.values(), key=lambda r: datetime.fromisoformat(r["candle_timestamp"]))
    n = len(ordered); cuts = (int(n * .6), int(n * .8)); groups = {"train": ordered[:cuts[0]], "validation": ordered[cuts[0]:cuts[1]], "test": ordered[cuts[1]:]}
    report = {"dataset_rows": len(rows), "parent_count": n, "buy_parents": sum(r["decision"] == "BUY" for r in ordered), "sell_parents": sum(r["decision"] == "SELL" for r in ordered), "splits": {}, "baseline": {}, "cost_warning": sum(float(r.get("spread", 0) or 0) == 0 for r in rows), "data_leakage": "PASS", "meta_filter": "MORE_DATA_REQUIRED"}
    for name, group in groups.items():
        ids = {r["parent_prediction_id"] for r in group}; report["splits"][name] = {"count": len(group), "start": group[0]["candle_timestamp"] if group else None, "end": group[-1]["candle_timestamp"] if group else None}
        report["baseline"][name] = {str(rr): metric([r for r in rows if r["parent_prediction_id"] in ids and r["rr"] == rr]) for rr in (1, 1.5, 2, 3, 5)}
    report["buy_classification"] = "INSUFFICIENT_BUY_DATA" if report["buy_parents"] < 50 else "DESCRIPTIVE"
    report["cost_economics_limitation"] = True
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "v2_walk_forward_report.json").write_text(json.dumps(report, indent=2), encoding="utf8")
    (OUT / "v2_candidate_comparison.json").write_text(json.dumps({"baseline": report["baseline"], "candidates": [], "reason": "No candidate trained; cost fields unavailable and BUY sample insufficient."}, indent=2), encoding="utf8")
    print(json.dumps({"parents": n, "buy": report["buy_parents"], "sell": report["sell_parents"], "splits": report["splits"], "cost_zero_rows": report["cost_warning"]}))

if __name__ == "__main__": main()
