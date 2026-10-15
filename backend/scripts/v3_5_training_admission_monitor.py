"""Print the read-only V3.5 training-admission report; never trains."""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))
from app.v3_5.outcome_resolver import OUTCOME_DB, STORE, training_admission_report

def main() -> None:
    if not OUTCOME_DB.exists():
        raise SystemExit("V3.5 outcome resolver has not initialized its sidecar store")
    source=sqlite3.connect(f"file:{STORE}?mode=ro",uri=True);source.execute("PRAGMA query_only=ON")
    labels=sqlite3.connect(f"file:{OUTCOME_DB}?mode=ro",uri=True);labels.execute("PRAGMA query_only=ON")
    try:
        source.execute("BEGIN"); labels.execute("BEGIN")
        report=training_admission_report(source,labels)
        print(json.dumps(report,indent=2,sort_keys=True))
    finally:
        source.rollback(); labels.rollback(); source.close(); labels.close()

if __name__ == "__main__":
    main()
