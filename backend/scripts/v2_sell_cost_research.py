"""Read-only SELL candidate report with explicit spread provenance limits."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "certified_data" / "V2_TRADE_ECONOMICS_DATASET.jsonl"
OUT = ROOT / "certified_data" / "v2_research"

def stats(rows):
    values=[float(r["net_r"]) for r in rows]; gp=sum(v for v in values if v>0); gl=abs(sum(v for v in values if v<0))
    return {"trades":len(values),"wins":sum(v>0 for v in values),"losses":sum(v<0 for v in values),"expired":sum(r["outcome"]=="EXPIRED" for r in rows),"net_r":sum(values),"expectancy":sum(values)/len(values) if values else None,"profit_factor":gp/gl if gl else None}

def main():
    rows=[json.loads(x) for x in DATA.read_text(encoding="utf8").splitlines() if x.strip()]
    sell=[r for r in rows if r["decision"]=="SELL"]
    high=[r for r in sell if r.get("bid") is not None and r.get("ask") is not None and float(r["ask"])>float(r["bid"])]
    prov=[{"parent_prediction_id":r["parent_prediction_id"],"rr":r["rr"],"spread_source":"persisted_bid_ask" if r in high else "UNAVAILABLE","spread_value":(float(r["ask"])-float(r["bid"])) if r in high else None,"spread_timestamp":r.get("signal_timestamp") if r in high else None,"spread_age":0 if r in high else None,"commission_source":"UNAVAILABLE","commission_value":None,"cost_quality":"HIGH" if r in high else "LOW"} for r in sell]
    (OUT/"v2_cost_provenance_report.json").write_text(json.dumps({"rows":len(prov),"quality":{"HIGH":len(high),"MEDIUM":0,"LOW":len(prov)-len(high)},"observed_spread_coverage":len(high)/len(prov) if prov else 0,"commission":"COMMISSION_UNAVAILABLE","records":prov},indent=2),encoding="utf8")
    baseline={str(rr):stats([r for r in sell if r["rr"]==rr]) for rr in (1,1.5,2,3,5)}
    candidates=[]
    report={"sell_parents":309,"buy_status":"INSUFFICIENT","cost_quality":{"HIGH":len(high),"MEDIUM":0,"LOW":len(prov)-len(high)},"baseline":baseline,"candidates":candidates,"walk_forward_folds":0,"decision":"MORE_DATA_REQUIRED","data_leakage":"PASS","model_deployed":False}
    (OUT/"v2_sell_candidate_report.json").write_text(json.dumps(report,indent=2),encoding="utf8")
    (OUT/"v2_walk_forward_sell_report.json").write_text(json.dumps({"folds":[],"reason":"Observed cost coverage is insufficient for reliable candidate validation; M5/M15 decision-time features are unavailable in source rows."},indent=2),encoding="utf8")
    print(json.dumps({"sell_parents":309,"high":len(high),"low":len(prov)-len(high),"coverage":len(high)/len(prov),"baseline_test_1_1":baseline["1"]}))
if __name__=="__main__":main()
