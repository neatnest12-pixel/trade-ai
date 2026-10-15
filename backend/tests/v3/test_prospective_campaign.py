import copy,json
from datetime import datetime,UTC,timedelta
import pytest
from app.v3.prospective import (provenance_status,asof,validate_vector,connect_sidecar,
 register_observation,bucket_for_date,guarded_report_population,digest_object,sealed_read,
 health_counts,DENIED,REQUIRED_PROVENANCE)
from app.v3.certification import COLLECTOR_HASH,SAFETY,require_safety

D=datetime(2026,9,25,12,tzinfo=UTC)
def proof(**changes):
    r={k:None for k in REQUIRED_PROVENANCE}
    r.update(feature_name='bid',feature_version='r1',source_provider='MT5',source_series='EURUSD',
      raw_source_timestamp=(D-timedelta(seconds=1)).isoformat(),ingestion_timestamp=D.isoformat(),
      availability_timestamp=D.isoformat(),decision_timestamp=D.isoformat(),transformation='identity',
      lookback_window=1,staleness=1,max_staleness_seconds=5,point_in_time_safe=True,
      synthetic=False,future_derived=False,training_allowed=True,reason='proof',evidence_sha256='a'*64,kind='quote')
    r.update(changes);return r

def test_valid_quote():assert provenance_status(proof())[0]
@pytest.mark.parametrize('field',['raw_source_timestamp','ingestion_timestamp','availability_timestamp'])
def test_future_times_fail(field):assert not provenance_status(proof(**{field:(D+timedelta(seconds=1)).isoformat()}))[0]
@pytest.mark.parametrize('name',['future_return','exit_price','MFE','MAE','synthetic_drift_return','TP_result','SL_result','placeholder_performance','future_high','future_low','future_volatility','resolved_outcome'])
def test_firewall(name):assert not provenance_status(proof(feature_name=name))[0]
def test_completed_bar():
    r=proof(kind='bar',timeframe='M1',raw_source_timestamp=(D-timedelta(seconds=30)).isoformat())
    assert provenance_status(r)[1]=='INCOMPLETE_BAR'
    r['raw_source_timestamp']=(D-timedelta(minutes=1)).isoformat();assert provenance_status(r)[0]
def test_macro_vintage():
    r=proof(kind='macro',feature_name='cpi',release_timestamp=(D-timedelta(days=1)).isoformat())
    assert provenance_status(r)[1]=='MACRO_VINTAGE_UNCERTIFIED'
    r.update(vintage_timestamp=(D-timedelta(hours=1)).isoformat(),vintage_reference='asof-series-vintage')
    assert provenance_status(r)[0]
    r['release_timestamp']=(D+timedelta(days=1)).isoformat();assert not provenance_status(r)[0]
def test_uncertified_dependency():
    assert not provenance_status(proof(kind='derived'),[{'point_in_time_safe':False}])[0]
    assert not provenance_status(proof(kind='derived'))[0]
def test_crossasset_asof():
    rows=[dict(timestamp=(D+timedelta(seconds=i)).isoformat(),availability_timestamp=(D+timedelta(seconds=i)).isoformat(),point_in_time_safe=True) for i in [-90,-5,2]]
    assert asof(rows,D.isoformat(),10)==rows[1]
    assert asof(rows,D.isoformat(),1) is None
def contract():
    c=dict(training_allowed=False,feature_order=['bid'],features=[dict(name='bid',dtype='float64')]);c['schema_hash']=digest_object(c);return c
def test_schema_and_training_disabled():
    c=contract();assert validate_vector({'bid':1.2},{'bid':proof()},c,c['schema_hash'])==(1.2,)
    for values,h,training in [({'bid':1.2,'future_return':1},'abc',False),({'bid':None},'abc',False),({'bid':1.2},'wrong',False),({'bid':1.2},'abc',True)]:
        with pytest.raises(ValueError):validate_vector(values,{'bid':proof()},c,h,training=training)
def test_provenance_not_trusted_by_boolean():
    c=contract()
    with pytest.raises(ValueError):validate_vector({'bid':1.2},{'bid':{'point_in_time_safe':True}},c,c['schema_hash'])

def test_mutated_contract():
    c=contract();c['features'][0]['dtype']='string'
    with pytest.raises(ValueError,match='CONTENT_MISMATCH'):validate_vector({'bid':1.2},{'bid':proof()},c,c['schema_hash'])

def test_unknown_synthetic_status_rejected():assert not provenance_status(proof(synthetic=None))[0]

def test_same_day_timestamp_regression(tmp_path):
    db=connect_sidecar(tmp_path/'db');m=manifest()
    register_observation(db,row('later',D+timedelta(minutes=1)),m,{})
    with pytest.raises(ValueError,match='TIMESTAMP_REGRESSION'):register_observation(db,row(),m,{})
def test_seal(tmp_path):
    p=tmp_path/'s.json';v={'a':1};v['schema_hash']=digest_object(v);p.write_text(json.dumps(v));sealed_read(p)
    v['a']=2;p.write_text(json.dumps(v))
    with pytest.raises(ValueError):sealed_read(p)
def manifest():return dict(prospective_validation_start_utc=D.isoformat(),policy_hash=COLLECTOR_HASH,campaign_id='campaign',training_contract_schema_hash='schema')
def row(oid='one',decision=D):return dict(observation_id=oid,decision_timestamp=decision.isoformat(),collector_policy_hash=COLLECTOR_HASH)
def test_cutoff_restart_and_immutable_identity(tmp_path):
    p=tmp_path/'db';db=connect_sidecar(p);m=manifest()
    assert register_observation(db,row('old',D-timedelta(seconds=1)),m,{})=='HISTORICAL_DEVELOPMENT_BASELINE'
    assert register_observation(db,row(),m,{})=='A';db.close();db=connect_sidecar(p)
    assert register_observation(db,row(),m,{})=='A';assert health_counts(db)['new_prospective_observations']==1
    with pytest.raises(ValueError):register_observation(db,{**row(),'extra':'changed'},m,{})
    with pytest.raises(Exception):db.execute("UPDATE members SET bucket='D'")
def test_buckets_and_late_date(tmp_path):
    db=connect_sidecar(tmp_path/'db');b=[]
    for i in range(81):b.append(bucket_for_date(db,(D+timedelta(days=i)).date().isoformat()))
    assert b.count('A')==40 and b.count('B')==10 and b.count('C')==10 and b.count('D')==20
    assert b[-1]=='POST_PLAN_BLINDED'
    with pytest.raises(ValueError):bucket_for_date(db,'2026-01-01')
def test_holdout_routine_reports_blind_all_prospective():
    old=row('old',D-timedelta(seconds=1));new=row();holdout=row('holdout',D+timedelta(days=80))
    assert guarded_report_population([old,new,holdout],manifest())==[old]
def test_safety(monkeypatch):
    for k,v in SAFETY.items():monkeypatch.setenv(k,v)
    require_safety()
    for key in ['FIT_MODEL','ORDER_SEND','DEMO_EXECUTION_ENABLED','REAL_EXECUTION_ENABLED']:
        monkeypatch.setenv(key,'1')
        with pytest.raises(RuntimeError):require_safety()
        monkeypatch.setenv(key,SAFETY[key])
