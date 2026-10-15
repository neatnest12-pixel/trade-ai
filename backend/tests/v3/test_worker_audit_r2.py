import json
from app.v3 import worker_audit
from app.v3.worker_lifecycle import Lease,effective_status
import pytest

def test_safe_traceback_and_secret_exclusion(tmp_path,monkeypatch):
    monkeypatch.setattr(worker_audit,'LOGS',tmp_path)
    try:raise RuntimeError('PASSWORD=CANARY_CREDENTIAL_9182 api_key=CANARY_API_6173')
    except RuntimeError as exc:worker_audit.exception('test',exc)
    worker_audit.event('test','heartbeat',password='CANARY_CREDENTIAL_9182',status='RUNNING')
    raw=next(tmp_path.glob('*.jsonl')).read_text()
    assert 'CANARY_CREDENTIAL_9182' not in raw and 'CANARY_API_6173' not in raw
    rows=[json.loads(x) for x in raw.splitlines()]
    assert rows[0]['frames'] and rows[0]['exception_type']=='RuntimeError'

def test_unexpected_exception_state_and_lock(tmp_path,monkeypatch):
    monkeypatch.setattr(worker_audit,'LOGS',tmp_path/'logs')
    lock=tmp_path/'lock';state=tmp_path/'state'
    with pytest.raises(RuntimeError):
        with Lease(lock,state):raise RuntimeError('synthetic crash')
    result=json.loads(state.read_text());assert result['status']=='FAILED'
    assert result['exit_code']==1 and result['exit_reason']=='RuntimeError'
    assert not lock.exists()

def test_dead_process_never_running(monkeypatch):
    monkeypatch.setattr('app.v3.worker_lifecycle.pid_alive',lambda pid:False)
    assert effective_status({'pid':123,'status':'RUNNING'},{'pid':123})=='STOPPED_OR_STALE_OWNER'
