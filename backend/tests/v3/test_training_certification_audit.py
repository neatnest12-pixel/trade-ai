import copy
import json

import pytest

from scripts.v34_training_certification import (
    LEAK, check_feature, certify_graph, membership, split_safe, strict_record, macro_record_status,
)
from app.v3.certification import COLLECTOR_HASH, COLLECTOR_VERSION, POLICY_HASH
from app.v3.prospective import digest_object


def row():
    return dict(observation_id='one',decision_timestamp='2026-09-23T12:00:00+00:00',
        tick_timestamp='2026-09-23T11:59:59+00:00',raw_tick_timestamp='2026-09-23T14:59:59+00:00',
        bid=1.1,ask=1.1001,spread=.0001,mt5_server_utc_offset_seconds=10800,
        collector_policy_hash=COLLECTOR_HASH,collector_policy_version=COLLECTOR_VERSION,
        strategy_input_payload={'contexts':{'M1':{'close':1.1,'source_candle_timestamp':'2026-09-23T11:59:00+00:00'}}})


def record(name='bid',kind='quote'):
    return dict(feature_name=name,feature_version='test',source_provider='MT5',source_series='EURUSD',
        raw_source_timestamp='2026-09-23T11:59:59+00:00',release_timestamp=None,
        ingestion_timestamp='2026-09-23T11:59:59+00:00',decision_timestamp='2026-09-23T12:00:00+00:00',
        availability_timestamp='2026-09-23T11:59:59+00:00',transformation='identity',lookback_window=1,
        timeframe=None,staleness=1,max_staleness_seconds=5,point_in_time_safe=True,synthetic=False,
        future_derived=False,training_allowed=False,reason='',kind=kind,evidence_sha256='a'*64)


@pytest.mark.parametrize('field,value,reason',[
    ('tick_timestamp','2026-09-23T12:00:01+00:00','FUTURE_TICK'),
    ('tick_timestamp','2026-09-23T11:59:00+00:00','STALE_TICK'),
    ('mt5_server_utc_offset_seconds',0,'OFFSET_MISMATCH'),
    ('raw_tick_timestamp','2026-09-23T13:59:59+00:00','NORMALIZATION_MISMATCH'),
    ('decision_timestamp','2026-09-23T12:00:00','TIMESTAMP_OR_PROVENANCE_INVALID'),
])
def test_tick_rejections(field,value,reason):
    r=row();r[field]=value
    assert check_feature(r,'bid',{})==(False,reason)


def test_offset_and_immutable_evidence():
    r=row();before=copy.deepcopy(r);p={'bid':record()}
    p['bid']['evidence_sha256']=digest_object(r)
    assert check_feature(r,'bid',p)[0]
    assert r==before
    p['bid']['evidence_sha256']='b'*64
    assert check_feature(r,'bid',p)==(False,'EVIDENCE_VALUE_BINDING_MISMATCH')


def test_completed_bar_and_missing_acquisition():
    r=row();name='strategy_input_payload.contexts.M1.close'
    assert check_feature(r,name,{})==(False,'MISSING_ACQUISITION_EVIDENCE')
    r['strategy_input_payload']['contexts']['M1']['source_candle_timestamp']='2026-09-23T12:00:00+00:00'
    assert check_feature(r,name,{})==(False,'INCOMPLETE_BAR')


def test_late_acquisition_cannot_be_repaired():
    r=row();r['collection_ingestion_timestamp']='2026-09-23T12:00:20+00:00'
    assert check_feature(r,'bid',{})==(False,'ACQUIRED_AFTER_DECISION')


@pytest.mark.parametrize('status',['DATE_ONLY','UNKNOWN'])
def test_macro_fail_closed(status):
    p=record('macro_value','macro');p['release_time_status']=status
    assert not strict_record(p)[0]


def test_macro_future_release():
    p=record('macro_value','macro');p.update(release_timestamp='2026-09-23T13:00:00+00:00',vintage_timestamp='2026-09-23T11:00:00+00:00',vintage_reference='v1')
    assert not strict_record(p)[0]


def test_cross_asset_future():
    p=record('gold','cross_asset');p.update(raw_source_timestamp='2026-09-23T12:00:01+00:00',normalization_verified=True,market_calendar='gold')
    assert not strict_record(p)[0]


def test_recursive_dependencies():
    p={'raw':record('raw'),'first':record('first','derived'),'second':record('second','derived')}
    g={'first':['raw'],'second':['first']}
    assert certify_graph('second',g,p)[0]
    p['raw']['ingestion_timestamp']='2026-09-23T12:01:00+00:00'
    assert not certify_graph('second',g,p)[0]
    assert not certify_graph('second',{'second':['second']},p)[0]


@pytest.mark.parametrize('name',['observation_id','setup_id','outcome_id','future_price','realized_r','mfe','mae','exit_timestamp','byte_offset','resolved_at','row_sequence','snapshot_hash'])
def test_blacklist(name):
    assert LEAK.search(name)


def test_membership_deterministic_no_outcome_mutation_or_join():
    r=row();o=dict(observation_id='one',direction='BUY',realized_r=1,rr=1,certification='CERTIFIED',outcome_policy_hash=POLICY_HASH)
    frozen=copy.deepcopy((r,o));a,_=membership(r,o,['bid'],{});b,_=membership(r,o,['bid'],{})
    assert json.dumps(a,sort_keys=True)==json.dumps(b,sort_keys=True)
    assert not a['training_eligible']
    assert (r,o)==frozen
    # An outcome field cannot supply a missing observation field.
    r.pop('bid');o['bid']=1.1
    a,_=membership(r,o,['bid'],{})
    assert a['primary_exclusion_reason']=='MISSING_REQUIRED_FEATURE'
    r['collector_policy_hash']='wrong';a,_=membership(r,o,['bid'],{})
    assert a['primary_exclusion_reason']=='POLICY_MISMATCH'


def test_split_order_purge_embargo():
    assert split_safe('2026-09-20T00:00:00+00:00','2026-09-20T06:00:00+00:00','2026-09-22T00:00:00+00:00')
    assert not split_safe('2026-09-20T00:00:00+00:00','2026-09-20T06:00:00+00:00','2026-09-21T06:00:30+00:00')
    assert not split_safe('2026-09-22T00:00:00+00:00','2026-09-22T06:00:00+00:00','2026-09-21T00:00:00+00:00')


@pytest.mark.parametrize('p,expected',[
    ({'release_time_status':'DATE_ONLY','retrieved_at':'2026-09-23T11:00:00+00:00'},('DATE_ONLY','MACRO_FAIL_CLOSED')),
    ({'retrieved_at':'2026-09-23T12:00:01+00:00'},('RETRIEVAL_ONLY','MACRO_UNSAFE')),
    ({'state':'UNAVAILABLE'},('UNKNOWN','MACRO_MISSING')),
])
def test_macro_record_classification(p,expected):
    assert macro_record_status(p,'2026-09-23T12:00:00+00:00')==expected


@pytest.mark.parametrize('tf,seconds',[('M1',60),('M5',300),('M15',900),('H1',3600),('H6',21600),('H12',43200)])
def test_every_timeframe_completion_boundary(tf,seconds):
    from datetime import datetime,timedelta
    r=row();name=f'strategy_input_payload.contexts.{tf}.close'
    d=datetime.fromisoformat(r['decision_timestamp'])
    r['strategy_input_payload']['contexts'][tf]={'close':1.1,'source_candle_timestamp':(d-timedelta(seconds=seconds-1)).isoformat()}
    assert check_feature(r,name,{})==(False,'INCOMPLETE_BAR')
    r['strategy_input_payload']['contexts'][tf]['source_candle_timestamp']=(d-timedelta(seconds=seconds)).isoformat()
    assert check_feature(r,name,{})==(False,'MISSING_ACQUISITION_EVIDENCE')


def test_economic_period_is_not_exact_release():
    assert macro_record_status({'observation_period':'2026Q2'},'2026-09-23T12:00:00+00:00')==('DATE_ONLY','MACRO_FAIL_CLOSED')


def test_date_only_with_late_retrieval_is_unsafe():
    assert macro_record_status({'release_time_status':'DATE_ONLY','retrieved_at':'2026-09-23T12:01:00+00:00'},'2026-09-23T12:00:00+00:00')==('DATE_ONLY','MACRO_UNSAFE')
