"""Secret-free lifecycle evidence. Never serialize exception messages or locals."""
import json,os,traceback
from datetime import UTC,datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
LOGS=ROOT/'runtime/v34_prospective_validation_r1/lifecycle'
ALLOWED={'status','worker_id','cycle','exit_code','exit_reason','observations_count',
 'certified_resolved','pending','last_completed_m1','last_successful_cycle','lock_owned','exception_type','frames'}

def event(worker,kind,**fields):
    if os.getenv('PYTEST_CURRENT_TEST') and LOGS==ROOT/'runtime/v34_prospective_validation_r1/lifecycle':return
    LOGS.mkdir(parents=True,exist_ok=True)
    row={'utc':datetime.now(UTC).isoformat(),'pid':os.getpid(),'parent_pid':os.getppid(),
         'worker':worker,'event':kind,'campaign_id':'V34-PV-20260925T154159',
         'policy_hash':'c056fac9dca4cf331dc94c52ea3e46dc7e6257e9fd0009506fbf808ce795ee58'}
    row.update({k:v for k,v in fields.items() if k in ALLOWED})
    path=LOGS/(worker+'-'+datetime.now(UTC).date().isoformat()+'.jsonl')
    with path.open('a',encoding='utf8') as f:
        f.write(json.dumps(row,allow_nan=False)+'\n');f.flush();os.fsync(f.fileno())

def exception(worker,exc):
    # Frames without source-line text or locals avoid credentials in error URLs/messages.
    frames=[{'file':Path(f.filename).name,'line':f.lineno,'function':f.name} for f in traceback.extract_tb(exc.__traceback__)]
    event(worker,'exception',exception_type=type(exc).__name__,frames=frames)
