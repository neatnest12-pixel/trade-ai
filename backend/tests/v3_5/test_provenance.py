from __future__ import annotations
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from app.v3_5.provenance import (COHORT_VERSION, POLICY_HASH, acquisition_evidence, append_evidence, append_only_jsonl, evaluate_smoke,
    append_observation, append_outcome, connect, digest, freeze_decision, make_bar_evidence,
    make_derived_evidence, make_macro_evidence, make_tick_evidence, monitor, validate_clock)

T=datetime(2026,9,29,12,0,tzinfo=UTC)
SAFE={'clock_safe':True,'skew_seconds':0,'evidence_hash':'a'*64}

def raw(**kw):
    return acquisition_evidence(source='test',instrument='EURUSD',raw_value=kw.pop('value',1.0),
      raw_source_timestamp=kw.pop('source',T),normalized_timestamp=kw.pop('normalized',T),
      acquired_at_utc=kw.pop('acquired',T-timedelta(seconds=1)),information_available_at_utc=kw.pop('available',T-timedelta(seconds=1)),
      decision_time_utc=kw.pop('decision',T),source_timezone='UTC',normalization_rule='identity',provider_request_id='req-1',
      clock_evidence=kw.pop('clock',SAFE),kind=kw.pop('kind','raw'),max_staleness_seconds=kw.pop('max_age',3600),**kw)

def test_tick_preserves_raw_timestamp_and_plus_10800_normalization():
    raw_dt=T-timedelta(seconds=2); epoch=(raw_dt+timedelta(seconds=10800)).timestamp()
    p=make_tick_evidence(raw_epoch_seconds=epoch,configured_offset_seconds=10800,decision_time_utc=T,acquired_at_utc=T-timedelta(seconds=1),bid=1.1,ask=1.1001,symbol='EURUSD',broker_server='demo-server',request_id='tick-1',clock_evidence=SAFE)
    assert p['raw_value']['raw_broker_timestamp']==(raw_dt+timedelta(seconds=10800)).isoformat()
    assert p['normalized_timestamp']==raw_dt.isoformat() and p['provenance_status']=='CERTIFIED'

def test_future_and_stale_ticks_fail_closed():
    assert 'FUTURE_TICK' in make_tick_evidence(raw_epoch_seconds=(T+timedelta(seconds=1)+timedelta(seconds=10800)).timestamp(),configured_offset_seconds=10800,decision_time_utc=T,acquired_at_utc=T,bid=1,ask=1.1,symbol='EURUSD',broker_server='s',request_id='r',clock_evidence=SAFE)['exclusion_reasons']
    assert 'STALE_TICK' in make_tick_evidence(raw_epoch_seconds=(T-timedelta(seconds=8)+timedelta(seconds=10800)).timestamp(),configured_offset_seconds=10800,decision_time_utc=T,acquired_at_utc=T,bid=1,ask=1.1,symbol='EURUSD',broker_server='s',request_id='r',clock_evidence=SAFE)['exclusion_reasons']

def test_future_acquisition_and_clock_skew_rejected():
    assert 'ACQUIRED_AFTER_DECISION' in raw(acquired=T+timedelta(seconds=1))['exclusion_reasons']
    assert not validate_clock(sampled_utc=T,system_utc=T+timedelta(seconds=6))['clock_safe']

def test_bars_require_completed_time_and_provenance():
    good=make_bar_evidence(source='MT5',symbol='EURUSD',timeframe='M15',raw_open_utc=T-timedelta(minutes=15),acquired_at_utc=T+timedelta(seconds=30),decision_time_utc=T+timedelta(minutes=1),ohlcv={'open':1.,'high':2.,'low':0.5,'close':1.5},request_id='b',clock_evidence=SAFE)
    bad=make_bar_evidence(source='MT5',symbol='EURUSD',timeframe='M15',raw_open_utc=T,acquired_at_utc=T+timedelta(seconds=30),decision_time_utc=T+timedelta(minutes=1),ohlcv={'open':1.,'high':2.,'low':0.5,'close':1.5},request_id='b',clock_evidence=SAFE)
    assert good['provenance_status']=='CERTIFIED' and bad['provenance_status']=='FAIL_CLOSED'
    with pytest.raises(ValueError,match='AGGREGATED_BAR_SOURCE_LINEAGE_REQUIRED'):
        make_bar_evidence(source='LOCAL',symbol='EURUSD',timeframe='H6',raw_open_utc=T-timedelta(hours=3),acquired_at_utc=T-timedelta(seconds=1),decision_time_utc=T,ohlcv={'open':1.,'high':2.,'low':0.5,'close':1.5},request_id='h6',clock_evidence=SAFE,locally_aggregated=True)

def test_macro_date_only_and_missing_vintage_fail_closed():
    p=make_macro_evidence(provider='FRED',series_id='X',observation_period='2026-09',value=2,release_timestamp_utc=None,publication_timestamp_utc=None,vintage_id=None,retrieved_at_utc=T-timedelta(seconds=1),decision_time_utc=T,request_id='m',response_payload={'value':2},clock_evidence=SAFE)
    assert {'MACRO_DATE_ONLY','MACRO_VINTAGE_UNKNOWN'} <= set(p['exclusion_reasons'])

def test_macro_release_future_or_retrieval_after_decision_rejected():
    p=make_macro_evidence(provider='BLS',series_id='X',observation_period='x',value=1,release_timestamp_utc=T+timedelta(seconds=2),publication_timestamp_utc=None,vintage_id='v1',vintage_timestamp_utc=T-timedelta(hours=1),retrieved_at_utc=T-timedelta(seconds=1),decision_time_utc=T,request_id='m',response_payload={},clock_evidence=SAFE)
    assert 'FUTURE_INFORMATION' in p['exclusion_reasons']

def test_exact_macro_release_and_vintage_are_required_evidence():
    p=make_macro_evidence(provider='FRED',series_id='SERIES',observation_period='2026-09',value=2,
      release_timestamp_utc=T-timedelta(hours=1),publication_timestamp_utc=None,vintage_id='vintage-1',vintage_timestamp_utc=T-timedelta(hours=1),
      retrieved_at_utc=T-timedelta(minutes=5),decision_time_utc=T,request_id='fred-1',response_payload={'value':2},clock_evidence=SAFE)
    assert p['provenance_status']=='CERTIFIED'

def test_derived_lineage_fails_if_any_ancestor_missing_or_unsafe():
    good=raw(); bad=raw(acquired=T+timedelta(seconds=1))
    x=make_derived_evidence(feature_name='ema',feature_version='1',calculation_hash='c'*64,value=3,dependencies=[good,bad],window_start_utc=T-timedelta(hours=1),window_end_utc=T-timedelta(seconds=2),computed_at_utc=T-timedelta(seconds=1),decision_time_utc=T,request_id='d',clock_evidence=SAFE,source_observation_ids=['a','b'])
    assert 'UNSAFE_ANCESTOR' in x['exclusion_reasons']

def test_cross_asset_future_rejected():
    p=raw(kind='cross_asset',source=T+timedelta(seconds=1),normalized=T+timedelta(seconds=1))
    assert 'CROSS_ASSET_TIMESTAMP_INVALID' in p['exclusion_reasons']

def test_append_crash_restart_duplicate_and_snapshot_isolation(tmp_path):
    path=tmp_path/'c.sqlite3'; db=connect(path); p=raw(); evidence_id=append_evidence(db,p,appended_at_utc=T)
    assert append_evidence(db,p,appended_at_utc=T)==evidence_id
    snap=freeze_decision(decision_id='d1',decision_time_utc=T,feature_set_version='fs1',feature_values={'x':1.0},feature_evidence={'x':p},required_features=['x'],clock_evidence=SAFE)
    oid=append_observation(db,snap,direction='BUY',appended_at_utc=T)
    before=db.execute('select payload from observations where observation_id=?',(oid,)).fetchone()[0]
    append_outcome(db,oid,outcome_id='o1',payload={'realized_r':1},appended_at_utc=T+timedelta(hours=1))
    assert db.execute('select payload from observations where observation_id=?',(oid,)).fetchone()[0]==before
    db.close(); db=connect(path); assert monitor(db)['total_observations']==1
    with pytest.raises(sqlite3.IntegrityError): db.execute('update observations set eligible=1 where observation_id=?',(oid,))
    db.close()

def test_append_only_jsonl_duplicate_and_partial_tail_fail_closed(tmp_path):
    path=tmp_path/'journal.jsonl'; record={'evidence_id':'a','value':1}
    first=append_only_jsonl(path,record); assert append_only_jsonl(path,record)==first
    with path.open('ab') as f:f.write(b'{partial')
    with pytest.raises(ValueError,match='PARTIAL_PROVENANCE_JOURNAL_TAIL'):
        append_only_jsonl(path,{'evidence_id':'b','value':2})

def test_deterministic_membership_and_bootstrap_safety(tmp_path):
    db=connect(tmp_path/'s.sqlite3'); p=raw(); append_evidence(db,p,appended_at_utc=T)
    s=freeze_decision(decision_id='stable',decision_time_utc=T,feature_set_version='f1',feature_values={'x':1.0},feature_evidence={'x':p},required_features=['x'],clock_evidence=SAFE)
    a=append_observation(db,s,direction='SELL',appended_at_utc=T);b=append_observation(db,s,direction='SELL',appended_at_utc=T)
    assert a==b and s['training_eligible'] is True and monitor(db)['safe_to_train'] is False
    assert s['data_training_eligible'] is True
    assert s['eligibility_reasons'] == []
    assert s['eligibility_semantics_version'] == 'v3.5-eligibility-semantics-r2'
    assert s['training_execution_authorized'] is False
    row=db.execute('select payload from observations where observation_id=?',(a,)).fetchone()[0]
    assert 'BOOTSTRAP_TRAINING_DISABLED' not in row
    assert '"outcome_status":"UNKNOWN_AT_DECISION"' in row

def test_v34_never_reclassified_or_mutated():
    assert COHORT_VERSION=='v3.5-provenance-certified-r1' and POLICY_HASH

def test_snapshot_requires_frozen_feature_manifest():
    s=freeze_decision(decision_id='d',decision_time_utc=T,feature_set_version='f',feature_values={'x':1},feature_evidence={},clock_evidence=SAFE)
    assert not s['training_eligible'] and 'FEATURE_SET_MANIFEST_MISSING' in s['eligibility_reasons']

def test_smoke_gate_requires_two_advanced_eligible_cycles():
    cycle={'completed_m1_utc':T.isoformat(),'raw_tick':'PASS','bar':'PASS','derived_lineage':'PASS','macro':'EXPLICIT_FAIL_CLOSED',
      'cross_asset':'PASS','clock':'PASS','decision_snapshot':'PASS','outcome_isolation':'PASS','no_lookahead':'PASS',
      'training_eligible':1,'duplicates':False,'future_features':0,'stale_decision_ticks':0,'v34_modified':False}
    second={**cycle,'completed_m1_utc':(T+timedelta(minutes=1)).isoformat()}
    assert evaluate_smoke([cycle,second])['continuous_collection_authorized'] is True
    assert evaluate_smoke([cycle])['continuous_collection_authorized'] is False
    assert evaluate_smoke([cycle,{**second,'training_eligible':0}])['continuous_collection_authorized'] is False
