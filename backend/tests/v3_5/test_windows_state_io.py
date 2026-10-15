import json
import os
import ctypes
import threading
import time
from pathlib import Path

import pytest
from app.v3_5 import state_io as io


def denied(code=5):
    e = PermissionError('simulated Windows access error')
    e.winerror = code
    return e


@pytest.mark.parametrize('code', [5, 32, 33])
def test_transient_retry_and_unique_temp(tmp_path, monkeypatch, code):
    p = tmp_path / 'state.json'; seen=[]; original=io.os.replace
    def replace(a,b):
        seen.append(str(a))
        if len(seen) < 3: raise denied(code)
        original(a,b)
    monkeypatch.setattr(io.os,'replace',replace)
    io.write_json_atomic(p, {'n':1});io.write_json_atomic(p, {'n':2})
    assert json.loads(p.read_text()) == {'n':2}
    assert seen[0] == seen[1] and seen[0] != seen[-1]
    assert not list(tmp_path.glob('*.tmp'))


def test_persistent_permission_fail_closed(tmp_path, monkeypatch):
    p=tmp_path/'state.json';p.write_text('{"old":1}');calls=[]
    def replace(*args):calls.append(1);raise denied()
    monkeypatch.setattr(io.os,'replace',replace);monkeypatch.setattr(io.time,'sleep',lambda _:None)
    with pytest.raises(PermissionError):io.write_json_atomic(p, {'new':1})
    assert len(calls)==6 and json.loads(p.read_text())=={'old':1}
    assert not list(tmp_path.glob('*.tmp'))


def test_concurrent_writer_prevented(tmp_path):
    p=tmp_path/'state.json'
    with io.exclusive(p.with_name(p.name+'.writer.guard')):
        with pytest.raises(RuntimeError,match='CONCURRENT'):io.write_json_atomic(p,{'n':1})
    assert not p.exists()


def test_real_windows_sharing_violation_recovers(tmp_path):
    p=tmp_path/'state.json';p.write_text('{"old":1}')
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateFileW.argtypes=[ctypes.c_wchar_p,ctypes.c_ulong,ctypes.c_ulong,ctypes.c_void_p,ctypes.c_ulong,ctypes.c_ulong,ctypes.c_void_p]
    kernel.CreateFileW.restype=ctypes.c_void_p
    kernel.CloseHandle.argtypes=[ctypes.c_void_p]
    handle=kernel.CreateFileW(str(p),0x80000000,1,None,3,0,None)
    assert handle not in (None,ctypes.c_void_p(-1).value)
    t=threading.Thread(target=lambda:(time.sleep(.18),kernel.CloseHandle(handle)));t.start()
    io.write_json_atomic(p,{'new':1});t.join()
    assert json.loads(p.read_text())=={'new':1}


def test_reader_writer_complete_json(tmp_path):
    p=tmp_path/'state.json';io.write_json_atomic(p,{'n':0});errors=[];done=threading.Event()
    def reader():
        while not done.is_set():
            try:assert isinstance(json.loads(io.read_text_shared(p))['n'],int)
            except Exception as e:errors.append(e)
            # Polling readers close the handle and yield between snapshots.
            time.sleep(.01)
    t=threading.Thread(target=reader);t.start()
    try:
        for n in range(30):io.write_json_atomic(p,{'n':n})
    finally:done.set();t.join()
    assert not errors


def test_crash_before_replace_preserves_old(tmp_path,monkeypatch):
    p=tmp_path/'state.json';p.write_text('{"old":1}')
    def crash(*_):raise SystemExit('crash')
    monkeypatch.setattr(io.os,'replace',crash)
    with pytest.raises(SystemExit):io.write_json_atomic(p,{'new':1})
    assert json.loads(p.read_text())=={'old':1}


def test_stale_temp_cleanup_proves_owner_dead(tmp_path):
    p=tmp_path/'state.json'
    dead=tmp_path/'.state.json.123.dead.tmp';dead.write_text('partial')
    alive=tmp_path/f'.state.json.{os.getpid()}.alive.tmp';alive.write_text('partial')
    io.cleanup_stale_temps(p,lambda pid:pid==os.getpid())
    assert not dead.exists() and alive.exists()


def test_owner_pid_and_identity_required(tmp_path):
    p=tmp_path/'worker.lock';p.write_text(json.dumps({'pid':1,'worker_id':'a'}))
    io.assert_owner(p,{'pid':1,'worker_id':'a'})
    for owner in [{'pid':2,'worker_id':'a'},{'pid':1,'worker_id':'b'}]:
        with pytest.raises(RuntimeError,match='OWNERSHIP'):io.assert_owner(p,owner)


def test_collector_missing_lock_stops_resolver(tmp_path,monkeypatch):
    import sqlite3
    from app.v3_5 import outcome_resolver as resolver
    monkeypatch.setattr(resolver,'ROOT',tmp_path)
    with pytest.raises(RuntimeError,match='COLLECTOR_STATE_OR_LOCK_MISSING'):
        resolver.verify_collector_health(sqlite3.connect(':memory:'))


def test_crash_process_preserves_json_and_releases_writer_guard(tmp_path):
    import subprocess,sys
    p=tmp_path/'state.json';io.write_json_atomic(p,{'old':1})
    script=("from pathlib import Path; import os; from app.v3_5 import state_io as io; "
            "io.os.replace=lambda *args:os._exit(9); io.write_json_atomic(Path("+repr(str(p))+"),{'new':1})")
    result=subprocess.run([sys.executable,'-c',script],timeout=10)
    assert result.returncode==9 and json.loads(p.read_text())=={'old':1}
    io.cleanup_stale_temps(p,lambda pid:False)
    io.write_json_atomic(p,{'restarted':1})
    assert json.loads(p.read_text())=={'restarted':1}


def test_actual_collector_lock_owner_accepts_startup_metadata(tmp_path,monkeypatch):
    from app.v3_5 import live_adapter as live
    monkeypatch.setattr(live,'STATE_PATH',tmp_path/'state.json')
    monkeypatch.setattr(live,'LOCK_PATH',tmp_path/'collector.lock')
    owner=live._acquire_collector_lock()
    try:
        live._write_state('STARTING',**owner,startup_at_utc=live._utc_now().isoformat())
        state=json.loads(io.read_text_shared(live.STATE_PATH))
        assert state['worker_id']==owner['worker_id'] and state['status']=='STARTING'
    finally:live.LOCK_PATH.unlink()


def test_collection_refreshes_heartbeat_after_clock_before_provider(tmp_path,monkeypatch):
    import sqlite3
    from datetime import datetime,UTC
    from app.v3_5 import live_adapter as live
    db=sqlite3.connect(':memory:')
    db.execute('CREATE TABLE observations(observation_id TEXT,snapshot_sha256 TEXT,payload TEXT,decision_id TEXT)')
    events=[]
    monkeypatch.setattr(live,'clock_evidence',lambda: events.append('clock') or {'clock_safe':True})
    def provider(_):
        events.append('provider')
        raise RuntimeError('provider stopped')
    monkeypatch.setattr(live,'_rate_arrays',provider)
    with pytest.raises(RuntimeError,match='provider stopped'):
        live._collect_cycle(None,db,datetime.now(UTC),{},1,continuous=True,
                            heartbeat_callback=lambda:events.append('heartbeat'))
    assert events==['clock','heartbeat','provider']


def test_long_sqlite_check_refreshes_single_writer_state(tmp_path,monkeypatch):
    import sqlite3
    from app.v3_5 import live_adapter as live
    monkeypatch.setattr(live,'STATE_PATH',tmp_path/'state.json')
    monkeypatch.setattr(live,'LOCK_PATH',tmp_path/'collector.lock')
    owner=live._acquire_collector_lock();live._write_state('RUNNING',**owner,observations_count=124)
    ticks=iter(range(0,100000,6))
    monkeypatch.setattr(live.time,'monotonic',lambda:next(ticks))
    db=sqlite3.connect(':memory:');faults=live._install_sqlite_heartbeat(db,owner)
    result=db.execute('WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<20000) SELECT sum(x) FROM n').fetchone()[0]
    assert result==200010000 and not faults
    state=json.loads(io.read_text_shared(live.STATE_PATH))
    assert state['heartbeat_at_utc']!=state['acquired_at_utc'] and state['observations_count']==124
    assert state['worker_id']==owner['worker_id']


def test_sqlite_heartbeat_failure_aborts_query_with_original_error(tmp_path,monkeypatch):
    import sqlite3
    from app.v3_5 import live_adapter as live
    monkeypatch.setattr(live,'STATE_PATH',tmp_path/'state.json')
    monkeypatch.setattr(live,'LOCK_PATH',tmp_path/'collector.lock')
    owner=live._acquire_collector_lock();live._write_state('RUNNING',**owner)
    ticks=iter(range(0,100000,6));monkeypatch.setattr(live.time,'monotonic',lambda:next(ticks))
    def fail(*_, **__):raise PermissionError('persistent state failure')
    monkeypatch.setattr(live,'write_json_atomic',fail)
    db=sqlite3.connect(':memory:');faults=live._install_sqlite_heartbeat(db,owner)
    with pytest.raises(sqlite3.OperationalError,match='interrupted'):
        db.execute('WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<20000) SELECT sum(x) FROM n').fetchone()
    assert len(faults)==1 and isinstance(faults[0],PermissionError)
