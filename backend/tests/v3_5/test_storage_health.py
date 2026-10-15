import errno
import json
import sqlite3
from collections import namedtuple
import pytest
from app.v3_5 import storage_health as storage, state_io

Usage = namedtuple('Usage', 'total used free')


def set_free(monkeypatch, free, total=100 * 1024**3):
    monkeypatch.setattr(storage.shutil, 'disk_usage', lambda path: Usage(total, total-free, free))


def test_byte_and_percentage_thresholds_and_warning(tmp_path, monkeypatch):
    set_free(monkeypatch, 12*1024**3)
    assert storage.require(tmp_path)['status']=='WARNING'
    set_free(monkeypatch, 9*1024**3)
    with pytest.raises(storage.StorageHealthError):storage.require(tmp_path)
    set_free(monkeypatch, 20*1024**3, total=500*1024**3)
    with pytest.raises(storage.StorageHealthError):storage.require(tmp_path)
    set_free(monkeypatch, 10*1024**3)
    assert storage.require(tmp_path)['storage_safe']


@pytest.mark.parametrize('table', ['observations','outcomes'])
def test_mid_transaction_low_space_rolls_back_no_partial_record(tmp_path, monkeypatch, table):
    db=storage.connect(tmp_path/'store.sqlite3',enforce=True)
    db.execute(f'create table {table}(id primary key,payload)')
    with pytest.raises(storage.StorageHealthError):
        with db:
            db.execute(f'insert into {table} values(1,?)',('first',))
            set_free(monkeypatch, 1)
            db.execute(f'insert into {table} values(2,?)',('partial',))
    assert db.execute(f'select count(*) from {table}').fetchone()[0]==0
    db.close()


def test_low_space_before_commit_rolls_back(tmp_path, monkeypatch):
    db=storage.connect(tmp_path/'store.sqlite3',enforce=True);db.execute('create table observations(id)')
    with pytest.raises(storage.StorageHealthError):
        with db:
            db.execute('insert into observations values(1)')
            set_free(monkeypatch, 1)
    assert not db.in_transaction and db.execute('select count(*) from observations').fetchone()[0]==0
    db.close()


def test_enospc_atomic_state_preserves_previous(tmp_path, monkeypatch):
    path=tmp_path/'state.json';state_io.write_json_atomic(path,{'status':'RUNNING'})
    def fail(*args,**kwargs):raise OSError(errno.ENOSPC,'simulated disk full')
    monkeypatch.setattr(state_io.json,'dump',fail)
    with pytest.raises(OSError):state_io.write_json_atomic(path,{'status':'STORAGE_UNCERTIFIED'})
    assert json.loads(path.read_text())=={'status':'RUNNING'}
    assert not list(tmp_path.glob('*.tmp'))


def test_pause_then_recovery_runs_no_data_callback(tmp_path, monkeypatch):
    values=iter([Usage(100*1024**3,99*1024**3,1024**3),Usage(100*1024**3,60*1024**3,40*1024**3)])
    monkeypatch.setattr(storage.shutil,'disk_usage',lambda path:next(values));states=[];sleeps=[]
    result=storage.wait_until_healthy(tmp_path,states.append,sleep=sleeps.append)
    assert [x['status'] for x in states]==['STORAGE_UNCERTIFIED','PASS'] and sleeps==[5] and result['storage_safe']


def test_enospc_and_sqlite_full_recognized():
    assert storage.is_storage_error(OSError(errno.ENOSPC,'full'))
    error=sqlite3.OperationalError('database or disk is full');error.sqlite_errorcode=sqlite3.SQLITE_FULL
    assert storage.is_storage_error(error)
    assert not storage.is_storage_error(OSError(errno.EACCES,'denied'))


def test_wal_checkpoint_keeps_rows_and_append_only(tmp_path):
    db=storage.connect(tmp_path/'store.sqlite3',enforce=True);db.execute('pragma journal_mode=wal')
    db.executescript("create table outcomes(id primary key);create trigger no_delete before delete on outcomes begin select raise(abort,'APPEND_ONLY');end;")
    with db:db.execute('insert into outcomes values(212)')
    assert db.execute('pragma wal_checkpoint(truncate)').fetchone()[0]==0
    assert db.execute('pragma integrity_check').fetchone()[0]=='ok' and db.execute('select id from outcomes').fetchone()==(212,)
    with pytest.raises(sqlite3.IntegrityError):db.execute('delete from outcomes')
    db.close()


def test_collector_startup_low_space_fails_closed_without_market_calls(tmp_path, monkeypatch):
    from app.v3_5 import live_adapter as collector
    owner={'pid':1,'worker_id':'storage-test'};states=[];market=[]
    monkeypatch.setattr(collector,'_acquire_collector_lock',lambda:owner)
    monkeypatch.setattr(collector,'LOCK_PATH',tmp_path/'missing.lock')
    monkeypatch.setattr(collector,'_write_state',lambda status,**kw:states.append((status,kw)))
    monkeypatch.setattr(collector,'clock_evidence',lambda:market.append('clock'))
    set_free(monkeypatch,1)
    with pytest.raises(storage.StorageHealthError):collector.run_continuous_collection(max_cycles_for_test=1)
    assert not market and states[-1][0]=='STORAGE_UNCERTIFIED'
    assert states[-1][1]['continuous_collection'] is False and not states[-1][1]['storage_health']['storage_safe']
    assert collector.os.environ['FIT_MODEL']=='0' and collector.os.environ['ORDER_SEND']=='0'
    assert collector.os.environ['DEMO_EXECUTION_ENABLED']=='false' and collector.os.environ['REAL_EXECUTION_ENABLED']=='false'


def test_resolver_startup_low_space_fails_closed_before_outcome_store(tmp_path, monkeypatch):
    from app.v3_5 import outcome_resolver as resolver
    states=[];stores=[]
    monkeypatch.setattr(resolver,'_acquire_lock',lambda now:{'pid':1,'worker_id':'storage-test'})
    monkeypatch.setattr(resolver,'LOCK_PATH',tmp_path/'missing.lock')
    monkeypatch.setattr(resolver,'_state',lambda owner,**kw:states.append(kw))
    monkeypatch.setattr(resolver,'outcome_store',lambda:stores.append(1))
    set_free(monkeypatch,1)
    with pytest.raises(storage.StorageHealthError):resolver.run_resolver(max_cycles_for_test=1)
    assert not stores and states[-1]['status']=='STORAGE_UNCERTIFIED'
    assert resolver.os.environ['FIT_MODEL']=='0' and resolver.os.environ['ORDER_SEND']=='0'
