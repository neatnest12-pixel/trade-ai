"""Independent descriptive analysis of immutable outcomes; never changes labels."""
from __future__ import annotations
import hashlib
import json
import sqlite3
from collections import defaultdict, Counter
from pathlib import Path
from contextlib import closing
from .market_context import context, Config, VERSION


def metrics(rows, minimum=30):
    wins = [r["gross_r"] for r in rows if r["status"] == "WIN"]
    losses = [r["gross_r"] for r in rows if r["status"] == "LOSS"]
    n = len(rows)
    return dict(sample_size=n,wins=len(wins),losses=len(losses),breakeven=sum(r["status"]=="BREAKEVEN" for r in rows),
                win_rate=100*len(wins)/n if n else None,
                average_win_r=sum(wins)/len(wins) if wins else None,
                average_loss_r=sum(losses)/len(losses) if losses else None,
                expectancy_r=sum(r["gross_r"] for r in rows)/n if n else None,
                profit_factor=sum(wins)/-sum(losses) if losses and sum(losses)<0 else None,
                small_sample=n<minimum,independent_sample_size="NOT_ESTABLISHED",cost_basis="GROSS")


def analyze_rows(rows, minimum=30):
    groups=defaultdict(list);dates=set()
    for row in rows:
        f=context(row["decision_time_utc"],row.get("symbol","EURUSD"))
        dates.add(f["timestamp_utc"][:10])
        for name in ("ASIA","LONDON","NEW_YORK"):
            if name in f["active_sessions"]:groups[("session_membership",name)].append(row)
        if f["is_london_ny_overlap"]:groups[("session_membership","LONDON_NEW_YORK_OVERLAP")].append(row)
        values={"instrument":row.get("symbol","EURUSD"),"direction":row["direction"],"day_of_week":str(f["day_of_week"]),
                "hour_utc":str(f["hour_utc"]),"exclusive_session":f["current_session"]}
        # Historical contextual spread/volatility are unavailable, not reconstructed
        # from future ticks or outcome R. Missing remains UNKNOWN.
        for key in ("spread_state","volatility_state"):values[key]="UNKNOWN"
        for k,v in values.items():groups[(k,v)].append(row)
    for name in ("ASIA","LONDON","NEW_YORK","LONDON_NEW_YORK_OVERLAP"):groups[("session_membership",name)]
    return dict(version=VERSION,total=metrics(rows,minimum),distinct_decision_dates=len(dates),
                warnings=["DESCRIPTIVE_ONLY_NOT_SESSION_PROFITABILITY", "SESSION_MEMBERSHIP_GROUPS_OVERLAP", "MULTIPLE_COMPARISONS_NO_RULE_SELECTION", "TEMPORAL_CLUSTERING",
                          "TEMPORAL_DEPENDENCE_NOT_ADJUSTED", "BROKER_CALENDARS_UNVERIFIED",
                          "HISTORICAL_SPREAD_VOLATILITY_UNAVAILABLE", "NO_TRAINING_OR_STRATEGY_RULES"],
                groups=[dict(dimension=k,label=v,**metrics(rs,minimum)) for (k,v),rs in sorted(groups.items())])


def read_outcomes(path):
    path=Path(path).resolve(strict=True)
    with closing(sqlite3.connect(path.as_uri()+"?mode=ro",uri=True)) as db:
        db.execute("PRAGMA query_only=ON")
        records=db.execute("SELECT outcome_id,setup_id,status,payload,payload_sha256,appended_at_utc FROM outcomes ORDER BY outcome_id").fetchall()
    fingerprint=hashlib.sha256(json.dumps(records,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()
    rows=[json.loads(r[3]) for r in records if r[2] in ("WIN","LOSS","BREAKEVEN","TIMEOUT")]
    for row in rows:
        if not isinstance(row.get("gross_r"),(int,float)):
            raise ValueError("MISSING_GROSS_R")
    return rows,fingerprint
