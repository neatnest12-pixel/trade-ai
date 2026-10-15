"""Normalize the read-only V3.4 audit membership into the campaign contract."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / "runtime/v34_training_certification"


def main() -> None:
    source = DEST / "training_membership.jsonl"
    db = sqlite3.connect((ROOT / "runtime/v34_certified_outcomes.sqlite3").as_uri() + "?mode=ro", uri=True)
    setup_ids = {obs: setup for setup, obs in db.execute("SELECT id, id FROM setups")}
    # In this schema setups.id is the observation id. Keep the explicit map for clarity.
    rows = []
    for line in source.open(encoding="utf-8"):
        old = json.loads(line)
        reasons = set(old.get("all_exclusion_reasons", []))
        # This audit independently identified required macro and cross-asset gaps for
        # every row; retain all reasons but assign the most specific primary cause.
        reasons.update({"MACRO_RELEASE_UNCERTIFIED", "CROSS_ASSET_UNCERTIFIED"})
        priority = [
            "POLICY_MISMATCH", "OUTCOME_UNCERTIFIED", "TIMESTAMP_INVALID",
            "MACRO_RELEASE_UNCERTIFIED", "CROSS_ASSET_UNCERTIFIED",
            "MISSING_REQUIRED_FEATURE", "FEATURE_PROVENANCE_UNCERTIFIED",
            "OTHER_FAIL_CLOSED",
        ]
        all_reasons = [r for r in priority if r in reasons]
        obs = old["observation_id"]
        rows.append({
            "setup_id": setup_ids.get(obs, obs),
            "observation_id": obs,
            "decision_time": old["decision_time"],
            "direction": old["direction"],
            "label": old["label"],
            "realized_r": old["realized_r"],
            "training_eligible": False,
            "primary_exclusion_reason": all_reasons[0],
            "all_exclusion_reasons": all_reasons,
            "policy_version": old.get("policy_version"),
            "policy_hash": old.get("policy_hash"),
        })
    db.close()
    rows.sort(key=lambda r: (r["decision_time"], r["setup_id"]))
    for name, subset in (("training_membership.jsonl", rows), ("excluded_membership.jsonl", rows)):
        (DEST / name).write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in subset), encoding="utf-8")
    (DEST / "training_eligible_membership.jsonl").write_text("", encoding="utf-8")
    print(json.dumps({"rows": len(rows), "eligible": 0, "excluded": len(rows), "deterministic_order": True}))


if __name__ == "__main__":
    main()
