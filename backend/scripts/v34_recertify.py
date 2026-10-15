"""Repair campaign CLI. No observation writes, strategy evaluation, fitting or execution."""
from __future__ import annotations
from app.mt5_connection import initialize_checked, shutdown as shutdown_mt5

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from app.v3.certification import EXCLUSIONS, POLICY_CONTRACT, POLICY_HASH, require_safety
from app.v3.certified_store import atomic_json, connect, counts, export_jsonl, ingest, persist
from app.v3.certified_resolver import MT5Ticks, evaluate
from app.v3.worker_lifecycle import Lease

ROOT = Path(__file__).resolve().parents[2]
CAMPAIGN = ROOT / "runtime/v34_recertification_20260925"
DB = ROOT / "runtime/v34_certified_outcomes.sqlite3"
OBS = ROOT / "certified_data/v3/V3_PROSPECTIVE_DATASET.jsonl"
OUT = ROOT / "certified_data/v3/V3_CERTIFIED_OUTCOMES_time_r2.jsonl"
STATE = ROOT / "runtime/v3_outcome_resolver_state.json"
LOCK = ROOT / "runtime/v3_outcome_resolver.lock"


def feasibility(db, source, beat, limit=None, retry=False):
    cursor = ("", "")
    processed = 0
    if retry:
        db.execute("UPDATE setups SET feasibility='NOT_CHECKED' WHERE timing='CERTIFIED' AND feasibility IN ('OTHER_FAILURE','INSUFFICIENT_FUTURE_DATA')")
        db.commit()
    while limit is None or processed < limit:
        # Keyset paging, no whole-population Python object list.
        batch = db.execute("SELECT * FROM setups WHERE timing='CERTIFIED' AND feasibility='NOT_CHECKED' AND (decision,id)>(?,?) ORDER BY decision,id LIMIT 25", cursor).fetchall()
        if not batch:
            break
        for row in batch:
            if limit is not None and processed >= limit:
                break
            cursor = (row["decision"], row["id"])
            try:
                category, reason, candidates = evaluate(json.loads(row["payload"]), source)
            except Exception as exc:
                category, reason, candidates = "OTHER_FAILURE", f"{type(exc).__name__}:{exc}", []
            with db:
                db.execute("UPDATE setups SET feasibility=?,reason=?,candidate=?,checked_at=? WHERE id=?",
                           (category, reason, json.dumps(candidates, sort_keys=True), datetime.now(UTC).isoformat(), row["id"]))
            processed += 1
            beat()
        print(json.dumps({"processed": processed, **counts(db), "mt5_calls": source.calls,
                          "max_tick_window": source.max_window_ticks}), flush=True)
    return processed


def commit_candidates(db, pilot: bool):
    if not pilot:
        gate = db.execute("SELECT value FROM metadata WHERE key='pilot_certified'").fetchone()
        if not gate or gate[0] != POLICY_HASH:
            raise RuntimeError("PILOT_CERTIFICATION_REQUIRED")
    # Deterministic round-robin over date/direction groups for the small pilot.
    rank = "row_number() OVER(PARTITION BY substr(decision,1,10),side ORDER BY decision,id)"
    query = f"SELECT *,{rank} AS rank FROM setups WHERE candidate IS NOT NULL AND candidate!='[]' ORDER BY rank,decision,id"
    inserted = setups = 0
    for row in db.execute(query):
        records = json.loads(row["candidate"])
        inserted += persist(db, records)
        setups += 1
        if pilot and setups >= 25:
            break
    export_jsonl(db, OUT)
    return {"setups_processed": setups, "inserted_contracts": inserted, **counts(db)}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["index", "feasibility", "pilot", "commit", "serve"])
    parser.add_argument("--limit", type=int)
    parser.add_argument("--retry", action="store_true")
    args = parser.parse_args(argv)
    require_safety()
    from app.v3.prospective import MANIFEST, campaign_manifest, verify_policy_files, load_contract, sync_members, attach_labels
    if MANIFEST.exists():
        verify_policy_files(campaign_manifest())
        load_contract()
    CAMPAIGN.mkdir(exist_ok=True)
    with Lease(LOCK, STATE) as lease:
        db = connect(DB)
        def beat():
            lease.beat(**counts(db))
        try:
            atomic_json(CAMPAIGN / "exclusion_contract.json", {"excluded": EXCLUSIONS, "policy": POLICY_CONTRACT,
                        "policy_hash": POLICY_HASH, "training_allowed": False,
                        "rule": "Only project_setup allowlist and certified journal outcomes are accepted. Entire research snapshot trees are excluded."})
            if args.mode == "index":
                print(json.dumps(ingest(db, OBS, beat)), flush=True)
            elif args.mode in {"pilot", "commit"}:
                print(json.dumps(commit_candidates(db, args.mode == "pilot")), flush=True)
            else:
                import MetaTrader5 as mt5
                try:
                    while True:
                        from app.v3.worker_audit import event,exception
                        event('resolver','cycle_start')
                        try:
                            if not initialize_checked(mt5):
                                raise RuntimeError(f"MT5_INITIALIZE_FAILED:{mt5.last_error()}")
                            account = mt5.account_info()
                            if not account or account.server != "ICMarketsSC-Demo" or account.trade_mode != 0 or account.currency != "USD":
                                raise RuntimeError("DEMO_READ_ONLY_ACCOUNT_REQUIRED")
                            source = MT5Ticks(mt5, db, beat)
                            if args.mode == "serve":
                                ingest(db, OBS, beat)
                            feasibility(db, source, beat, args.limit, args.retry or args.mode == "serve")
                            if args.mode == "serve":
                                commit_candidates(db, False)
                                attach_labels(db)
                                from scripts.v34_prospective_campaign import health
                                health(quiet=True)
                            lease.beat(last_error=None, last_successful_cycle=datetime.now(UTC).isoformat(), **counts(db))
                            event('resolver','cycle_finish',status='SUCCESS')
                        except Exception as exc:
                            exception('resolver',exc)
                            event('resolver','cycle_finish',status='RETRY_WAIT')
                            lease.beat("RETRY_WAIT", last_error=f"{type(exc).__name__}:{exc}", **counts(db))
                            if args.mode != "serve":
                                raise
                        if args.mode != "serve":
                            break
                        time.sleep(30)
                finally:
                    shutdown_mt5(mt5)
            atomic_json(CAMPAIGN / "progress.json", counts(db))
            beat()  # retain reconciled counters in the final STOPPED state, too
        finally:
            db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
