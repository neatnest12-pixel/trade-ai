"""Reconcile persisted service status with OS process, lease and heartbeat evidence."""
from __future__ import annotations
import json
from pathlib import Path
from app.v3.certified_store import atomic_json
from app.v3.worker_lifecycle import effective_status

ROOT = Path(__file__).resolve().parents[2]


def inspect(name: str, *, reconcile=False) -> dict:
    state_path=ROOT/'runtime'/f'{name}_state.json'
    lock_path=ROOT/'runtime'/f'{name}.lock'
    state=json.loads(state_path.read_text()) if state_path.exists() else {}
    lock=json.loads(lock_path.read_text()) if lock_path.exists() else None
    status=effective_status(state,lock)
    result={'worker':name,'effective_status':status,'recorded_status':state.get('status'),
            'last_heartbeat':state.get('last_heartbeat'),'pid':state.get('pid'),
            'lock_present':lock is not None}
    if reconcile and status=='STOPPED_OR_STALE_OWNER':
        state.update(status='STOPPED',health_status=status)
        atomic_json(state_path,state)
    return result


if __name__=='__main__':
    print(json.dumps([inspect(name,reconcile=True) for name in ['v3_prospective_collector','v3_outcome_resolver']],indent=2))
