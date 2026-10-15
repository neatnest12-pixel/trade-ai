"""Explicit offline analysis entry point. Writes only a NEW report file."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.v3_5.session_analysis import read_outcomes, analyze_rows

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--database",required=True)
    parser.add_argument("--output",required=True)
    args=parser.parse_args()
    source=Path(args.database).resolve(strict=True)
    target=Path(args.output).resolve()
    root=Path(__file__).resolve().parents[2]
    # Explicit report-only destination, outside runtime/certified datasets.
    allowed=root/"analysis_reports"/"market_context_r1"
    if not target.is_relative_to(allowed) or target==source:
        raise ValueError("NEW_ANALYTICAL_REPORT_PATH_REQUIRED")
    rows,before=read_outcomes(source)
    report=analyze_rows(rows)
    _,after=read_outcomes(source)
    if before!=after:raise RuntimeError("SOURCE_CHANGED_DURING_ANALYSIS")
    report.update(source=str(source),source_rows_sha256=before,source_unchanged=True)
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.open("x",encoding="utf-8") as f:json.dump(report,f,indent=2,allow_nan=False)
    print(json.dumps({"output":str(target),"source_fingerprint":before,"total":report["total"]}))

if __name__=="__main__":main()
