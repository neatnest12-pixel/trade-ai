from __future__ import annotations

import hashlib
import json
import os
import tracemalloc
from datetime import UTC, datetime, timedelta

import pytest

from app.v3.certification import (COLLECTOR_HASH, COLLECTOR_VERSION, NORMALIZATION, POLICY_HASH,
                                SAFETY, project_setup, require_safety, training_targets)
from app.v3.certified_resolver import MT5Ticks, evaluate
from app.v3.certified_store import connect, counts, export_jsonl, ingest, persist
from app.v3.worker_lifecycle import Lease, effective_status

BASE = datetime(2026, 9, 10, 12, tzinfo=UTC)


def row(oid="one", side="BUY"):
    bar = BASE-timedelta(minutes=1)
    ctx = {"available": True, "source_candle_timestamp": (BASE-timedelta(minutes=15)).isoformat(),
           "feature_timestamp": bar.isoformat(), "atr14": .002}
    return {"observation_id": oid, "symbol": "EURUSD", "timestamp": bar.isoformat(),
            "decision_timestamp": BASE.isoformat(), "time_normalization_version": NORMALIZATION,
            "mt5_server_utc_offset_seconds": 10800, "raw_m1_timestamp": (bar+timedelta(hours=3)).isoformat(),
            "raw_m1_epoch_seconds": (bar+timedelta(hours=3)).timestamp(),
            "raw_tick_timestamp": (BASE+timedelta(hours=3)-timedelta(seconds=1)).isoformat(),
            "raw_tick_epoch_seconds": (BASE+timedelta(hours=3)-timedelta(seconds=1)).timestamp(),
            "tick_timestamp": (BASE-timedelta(seconds=1)).isoformat(), "bid": 1.1, "ask": 1.1002,
            "collector_policy_version": COLLECTOR_VERSION, "collector_policy_hash": COLLECTOR_HASH,
            "strategy_policy_version": "v3-strategy-family-r1",
            "strategy_policy_hash": hashlib.sha256(b"v3-strategy-family-r1").hexdigest(),
            "strategy_input_policy_version": "v3-strategy-input-r1",
            "strategy_input_policy_hash": hashlib.sha256(b"v3-strategy-input-r1").hexdigest(),
            "strategy_families": {"TREND": {"setup": True, "side": side}},
            "strategy_input_payload": {"decision_timestamp": bar.isoformat(), "contexts": {"M15": ctx}}}


class Ticks:
    def __init__(self, data): self.data = data
    def ticks(self, *_): yield from self.data


def tick(second, bid, ask): return (int((BASE+timedelta(seconds=second)).timestamp()*1000), bid, ask)


def test_strict_timing_and_synthetic_isolation():
    r = row()
    expected = project_setup(r)
    r["v3_3_system_intelligence"] = {"rich_outcome_labels": {"net_r": 999999}, "drift_monitoring": {"win_rate": 1}}
    assert project_setup(r) == expected
    assert expected["timing_status"] == "CERTIFIED"
    del r["decision_timestamp"]
    assert project_setup(r)["timing_status"] == "LEGACY_UNCERTIFIED"
    with pytest.raises(RuntimeError, match="TRAINING_DISABLED"): training_targets(r)


@pytest.mark.parametrize("field,value", [("decision_timestamp", "2026-09-10T12:00:00"),
    ("mt5_server_utc_offset_seconds", 0), ("tick_timestamp", "2026-09-10T12:01:00+00:00")])
def test_bad_time_fails_closed(field, value):
    r=row();r[field]=value
    assert project_setup(r)["timing_status"] == "TIMING_UNCERTIFIABLE"


@pytest.mark.parametrize("side,exit_bid,exit_ask,reason", [
    ("BUY",1.116,1.1162,"TP_HIT"),("BUY",1.096,1.0962,"SL_HIT"),
    ("SELL",1.083,1.0832,"TP_HIT"),("SELL",1.104,1.1042,"SL_HIT")])
def test_future_only_entry_bid_ask_rr(side, exit_bid, exit_ask, reason):
    s=project_setup(row(side=side))
    data=[tick(-1,9,10),tick(0,9,10),tick(1,1.1,1.1002),tick(2,exit_bid,exit_ask)]
    category,_,out=evaluate(s,Ticks(data))
    assert category == "RESOLVABLE" and len(out)==5
    one=out[0]
    assert one["entry_price"] == (1.1002 if side=="BUY" else 1.1)
    assert one["exit_price"] == (exit_bid if side=="BUY" else exit_ask)
    assert one["exit_reason"]==reason and one["rr"]==1
    assert one["realized_r"]==pytest.approx((1 if side=="BUY" else -1)*(one["exit_price"]-one["entry_price"])/.003)
    assert one["commission_cost_r"] is None and one["net_r"] is None


def test_timeout_and_missing_ticks_not_invented():
    s=project_setup(row())
    data=[tick(i,1.1,1.1002) for i in range(1,21602,60)] + [tick(21602,1.1,1.1002)]
    category,_,out=evaluate(s,Ticks(data))
    assert category=="RESOLVABLE" and all(r["exit_reason"]=="TIME_EXIT" for r in out)
    assert evaluate(s,Ticks([]))[0]=="INSUFFICIENT_FUTURE_DATA"
    assert evaluate(s,Ticks([tick(61,1.1,1.1002)]))[1]=="ENTRY_GAP"
    assert evaluate(s,Ticks([tick(1,1.1,1.1002),tick(400,1.2,1.2002)]))[1]=="FUTURE_TICK_GAP"


def test_persistence_crash_replay_conflict_and_pending(tmp_path):
    db=connect(tmp_path/'db');p=tmp_path/'obs';p.write_text(json.dumps(row())+'\n')
    ingest(db,p)
    assert counts(db)["pending"]==1
    out=evaluate(project_setup(row()),Ticks([tick(1,1.1,1.1002),tick(2,1.096,1.0962)]))[2]
    assert persist(db,out)==5
    db.close() # simulate crash after commit before JSONL export
    db=connect(tmp_path/'db')
    assert persist(db,out)==0 and counts(db)["pending"]==0
    export_jsonl(db,tmp_path/'out')
    assert len((tmp_path/'out').read_text().splitlines())==5
    changed=[{**out[0],"realized_r":900}]
    with pytest.raises(RuntimeError,match="CONFLICTING"):persist(db,changed)
    # A mid-transaction failure rolls back ALL newly inserted contracts.
    db.execute('DELETE FROM outcomes');db.commit()
    bad=[out[0],{**out[1],"certification":"SYNTHETIC"}]
    with pytest.raises(ValueError):persist(db,bad)
    assert counts(db)["certified_resolved"]==0


def test_index_resume_and_bounded_memory(tmp_path):
    p=tmp_path/'obs'
    with p.open('w') as f:
        for i in range(2000):
            r=row(str(i));r['timestamp']=f'bar-{i}' # retained as uncertifiable, never guessed
            r['padding']='x'*10000
            f.write(json.dumps(r)+'\n')
    db=connect(tmp_path/'db');tracemalloc.start();ingest(db,p);_,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
    assert peak<8*1024*1024
    assert ingest(db,p)['total']==2000 and counts(db)['uncertifiable']==2000
    assert counts(db)['pending']==2000
    with p.open('a') as f:f.write('{')
    with pytest.raises(RuntimeError,match='PARTIAL'):ingest(db,p)


def test_ingestion_rolls_back_uncheckpointed_rows(tmp_path):
    p=tmp_path/'obs';db=connect(tmp_path/'db')
    p.write_text(json.dumps(row())+'\n{')
    with pytest.raises(RuntimeError):ingest(db,p)
    assert db.execute('select count(*) from observations').fetchone()[0]==0
    p.write_text(json.dumps(row())+'\n')
    assert ingest(db,p)['total']==1


def test_lock_never_stolen_and_dead_state_not_running(tmp_path):
    lock,state=tmp_path/'lock',tmp_path/'state'
    with Lease(lock,state):
        with pytest.raises(FileExistsError):
            with Lease(lock,state):pass
    assert not lock.exists()
    assert effective_status({'status':'RUNNING','pid':99999999},{'pid':99999999})=='STOPPED_OR_STALE_OWNER'
    lock.write_text('{}')
    with pytest.raises(FileExistsError):
        with Lease(lock,state):pass


def test_safety_requires_every_explicit_flag(monkeypatch):
    for k,v in SAFETY.items():monkeypatch.setenv(k,v)
    require_safety()
    monkeypatch.delenv('FIT_MODEL')
    with pytest.raises(RuntimeError,match='FIT_MODEL'):require_safety()


def test_tick_normalization_and_window_boundary_cache(tmp_path):
    class MT5:
        COPY_TICKS_ALL=1
        def __init__(self):self.calls=[]
        def copy_ticks_range(self,symbol,start,end,flag):
            self.calls.append((start,end))
            return [{'time_msc':int(start.timestamp()*1000)+1000,'bid':1.1,'ask':1.1002},
                    {'time_msc':int(end.timestamp()*1000),'bid':1.1,'ask':1.1002}]
    mt5=MT5();source=MT5Ticks(mt5,connect(tmp_path/'db'))
    got=list(source.ticks(BASE,BASE+timedelta(minutes=16),10800))
    assert len(got)==2 and got[0][0]==tick(1,0,0)[0]
    assert all((b-a).total_seconds()==900 for a,b in mt5.calls)
    list(source.ticks(BASE,BASE+timedelta(minutes=16),10800))
    assert len(mt5.calls)==2


def test_legacy_resolver_cannot_label_v34():
    from app.v3.outcome_resolver import clean_setups, resolve_setup
    with pytest.raises(RuntimeError,match='CERTIFIED_DECISION_TIME'):
        resolve_setup(clean_setups([row()])[0],1,[])


def test_service_survives_transient_initialization_then_idle(tmp_path,monkeypatch):
    import importlib.util
    import sys
    from pathlib import Path
    from types import SimpleNamespace
    path=Path('backend/scripts/v34_recertify.py')
    spec=importlib.util.spec_from_file_location('campaign_service_test',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    for key in ['DB','OBS','OUT','STATE','LOCK']:monkeypatch.setattr(module,key,tmp_path/key)
    monkeypatch.setattr(module,'CAMPAIGN',tmp_path/'campaign')
    module.OBS.write_text('')
    db=connect(module.DB)
    db.execute("INSERT INTO metadata VALUES('pilot_certified',?)",(POLICY_HASH,));db.commit();db.close()
    class MT5:
        calls=0
        def initialize(self,**kwargs):self.calls+=1;return self.calls>1
        def terminal_info(self):return SimpleNamespace(connected=True)
        def last_error(self):return (0,'transient test failure')
        def account_info(self):return SimpleNamespace(server='ICMarketsSC-Demo',trade_mode=0,currency='USD')
        def shutdown(self):pass
    mt5=MT5();monkeypatch.setitem(sys.modules,'MetaTrader5',mt5)
    from app.mt5_connection import initialize_checked, DEFAULT_TERMINAL
    monkeypatch.setattr(module,'initialize_checked',lambda client:initialize_checked(client,
        processes=lambda:[{'ExecutablePath':DEFAULT_TERMINAL}],exists=lambda p:True))
    sleeps=[]
    def sleep(_):
        sleeps.append(json.loads(module.STATE.read_text())['status'])
        if len(sleeps)==2:raise KeyboardInterrupt()
    monkeypatch.setattr(module.time,'sleep',sleep)
    for k,v in SAFETY.items():monkeypatch.setenv(k,v)
    with pytest.raises(KeyboardInterrupt):module.main(['serve'])
    assert sleeps==['RETRY_WAIT','RUNNING'] and mt5.calls==2
    assert not module.LOCK.exists()
