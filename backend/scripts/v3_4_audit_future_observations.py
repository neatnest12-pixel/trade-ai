from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OBSERVATIONS = ROOT / "certified_data" / "v3" / "V3_PROSPECTIVE_DATASET.jsonl"
EXPECTED_VERSION = "v3.4-complete-fx-intelligence-r1"
EXPECTED_HASH = "c056fac9dca4cf331dc94c52ea3e46dc7e6257e9fd0009506fbf808ce795ee58"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-utc", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audit_utc = datetime.fromisoformat(args.audit_utc.replace("Z", "+00:00"))
    if audit_utc.tzinfo is None:
        audit_utc = audit_utc.replace(tzinfo=UTC)
    before_hash = _sha256(OBSERVATIONS)
    records: list[dict[str, object]] = []
    with OBSERVATIONS.open(encoding="utf8") as handle:
        for line in handle:
            row = json.loads(line)
            if row.get("collector_policy_version") != EXPECTED_VERSION:
                continue
            if row.get("collector_policy_hash") != EXPECTED_HASH:
                continue
            timestamp = datetime.fromisoformat(row["timestamp"])
            if timestamp > audit_utc:
                records.append(
                    {
                        "observation_id": row["observation_id"],
                        "timestamp": row["timestamp"],
                        "difference_seconds": (timestamp - audit_utc).total_seconds(),
                        "classification": "TIMESTAMP_NORMALIZATION_ERROR",
                    }
                )
    artifact = {
        "campaign": "NAVORA QUANT AI — V3.4 UTC ALIGNMENT CERTIFICATION + SAFE COLLECTOR RECOVERY",
        "audit_utc": audit_utc.isoformat(),
        "classification": "TIMESTAMP_NORMALIZATION_ERROR",
        "root_cause": (
            "ICMarketsSC-Demo tick/bar epochs were empirically +10800 seconds "
            "relative to trusted UTC and the collector interpreted them directly as UTC."
        ),
        "policy_version": EXPECTED_VERSION,
        "policy_hash": EXPECTED_HASH,
        "future_dated_count": len(records),
        "old_observations_modified": False,
        "observations_sha256_before": before_hash,
        "records": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf8")
    after_hash = _sha256(OBSERVATIONS)
    if after_hash != before_hash:
        raise RuntimeError("OBSERVATION_STORE_CHANGED_DURING_READ_ONLY_AUDIT")
    print(json.dumps({"output": str(args.output), "count": len(records), "observations_sha256": after_hash}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
