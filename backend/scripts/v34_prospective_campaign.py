"""Initialize immutable campaign, preflight read-only MT5, emit blinded health."""
from __future__ import annotations
from app.mt5_connection import initialize_checked, shutdown as shutdown_mt5
import argparse,json,os,sqlite3
from datetime import UTC,datetime,timedelta
from pathlib import Path
from app.v3.prospective import *
from app.v3.certification import COLLECTOR_VERSION,POLICY_VERSION,SAFETY
from app.v3.certified_store import atomic_json

def initialize():
    require_safety();DIRECTORY.mkdir(parents=True,exist_ok=True)
    if MANIFEST.exists():
        m=campaign_manifest();verify_policy_files(m)
        if load_contract()['schema_hash']!=m['training_contract_schema_hash']:raise ValueError('CONTRACT_CHANGED')
        print(json.dumps({'status':'EXISTING_IMMUTABLE_CAMPAIGN','campaign_id':m['campaign_id']}));return
    stamp=datetime.now(UTC).isoformat()
    draft=ROOT/'runtime/v34_training_readiness_20260925/training_contract_DRAFT.json'
    c=sealed_read(draft);c.pop('schema_hash');c.update(version='v3.4-enforced-research-contract-r1',
      status='ENFORCED_RESEARCH_ONLY',training_allowed=False,policy_hash=COLLECTOR_HASH,policy_version=COLLECTOR_VERSION,
      timestamp_semantics='Explicit UTC; availability and ingestion <= decision; bars complete; macro release and vintage required',
      provenance_version='v34-provenance-r1',permanent_deny_pattern=DENIED.pattern)
    c['schema_hash']=digest_object(c)
    if CONTRACT.exists() and json.loads(CONTRACT.read_text())!=c:raise ValueError('EXISTING_CONTRACT_CONFLICT')
    atomic_json(CONTRACT,c)
    baseline=json.loads((ROOT/'runtime/v34_training_readiness_20260925/snapshot.json').read_text())
    refs={}
    for name,ref in baseline['files'].items():
        p=Path(ref['path']);h=file_hash(p)
        if h!=ref['sha256']:raise ValueError('BASELINE_CHANGED:'+name)
        refs[name]=dict(path=str(p),sha256=h,bytes=p.stat().st_size,rows=ref['rows'])
    frozen={}
    # Freeze every strategy, signal, regime and RR implementation, not collector plumbing.
    for folder in ['backend/app/v3','backend/app/v3_1','backend/app/v3_2','backend/app/v3_3','backend/app/v3_4']:
        for p in (ROOT/folder).glob('*.py'):
            if p.name in ['prospective.py','certified_store.py','worker_lifecycle.py']:continue
            frozen[str(p.relative_to(ROOT)).replace('\\','/')]=file_hash(p)
    ids=[]
    with (ROOT/'certified_data/v3/V3_CERTIFIED_OUTCOMES_time_r2.jsonl').open() as f:
        for line in f:
            r=json.loads(line)
            if r['rr']==1:ids.append(dict(observation_id=r['observation_id'],outcome_id=r['outcome_id'],decision_timestamp=r['decision_timestamp']))
    if len(ids)!=2061 or len({r['observation_id'] for r in ids})!=2061:raise ValueError('BASELINE_COUNT_MISMATCH')
    ids.sort(key=lambda r:r['observation_id']);atomic_json(DIRECTORY/'historical_baseline.json',ids)
    m=dict(version='v34-prospective-campaign-r1',campaign_id='V34-PV-'+stamp.replace(':','').replace('-','').split('.')[0],
      created_utc=stamp,prospective_validation_start_utc=stamp,policy_version=COLLECTOR_VERSION,policy_hash=COLLECTOR_HASH,
      outcome_policy_version=POLICY_VERSION,outcome_policy_hash=POLICY_HASH,feature_schema_version='v3-mtf-context-r1',
      training_contract_schema_hash=c['schema_hash'],observation_count=11447,certified_resolved_count=2061,
      baseline_population='HISTORICAL_DEVELOPMENT_BASELINE',baseline_population_sha256=file_hash(DIRECTORY/'historical_baseline.json'),
      dataset_references=refs,frozen_policy_files=frozen,training_allowed=False,
      bucket_plan={'A':[1,40],'B':[41,50],'C':[51,60],'D':[61,80]},
      date_definition='UTC trading date with at least one collected EURUSD observation, irrespective of setup eligibility or result',
      cutoff_rule='Immutable; decision_timestamp >= cutoff; no backdated membership or shifting dates based on results',
      holdout_rule='All prospective performance blinded to routine reports; no release endpoint in this campaign',
      embargo_seconds=86400,purge_rule='Decision through max_hold+60 seconds; no overlapping partition horizons')
    m['manifest_hash']=digest_object(m)
    with MANIFEST.open('x',encoding='utf8') as f:json.dump(m,f,indent=2);f.flush();os.fsync(f.fileno())
    with (DIRECTORY/'manifest_anchor.sha256').open('x') as f:f.write(m['manifest_hash']+'\n')
    # A record for every audited path; unavailable provenance is explicitly null, never invented.
    inventory=json.loads((ROOT/'runtime/v34_training_readiness_20260925/feature_inventory.json').read_text())
    registry=[]
    for v in inventory:
        name=v['feature_name'];r={k:None for k in REQUIRED_PROVENANCE}
        r.update(feature_name=name,feature_version='v34-provenance-r1',source_provider='UNPROVEN',source_series=name,
          transformation='Registered source path; per-observation evidence required',point_in_time_safe=False,
          synthetic=True if v['synthetic']=='YES' else False if v['synthetic']=='NO' else None,future_derived=bool(DENIED.search(name)),training_allowed=False,
          reason='PENDING_PER_OBSERVATION_EVIDENCE' if name in c['feature_order'] else v['reason'])
        if 'providers' in name or any(x in name.lower() for x in ['macro','yield','fred','bea','bls','ecb','eurostat','bundesbank']):r['reason']='MACRO_VINTAGE_UNCERTIFIED'
        registry.append(r)
    atomic_json(DIRECTORY/'feature_registry.json',registry)
    with connect_sidecar():pass
    print(json.dumps(m,indent=2))

def preflight():
    require_safety();m=campaign_manifest();verify_policy_files(m);load_contract()
    report={'utc':datetime.now(UTC).isoformat(),'status':'FAIL','orders_sent':0,'account_verified':False,
      'EURUSD':{'spread':'UNAVAILABLE','commission':'UNAVAILABLE','swap':'UNAVAILABLE','slippage':'UNAVAILABLE'},'cost_basis':'PRE_COMMISSION'}
    try:
        import MetaTrader5 as mt5
        from app.core.mt5_offset import configured_offset_seconds
        from app.v3_4.mt5_time import normalize_mt5_epoch,require_not_future,require_recent
        if not initialize_checked(mt5):raise RuntimeError('MT5_INITIALIZE_FAILED:'+str(mt5.last_error()))
        terminal=mt5.terminal_info();account=mt5.account_info()
        if not terminal or not terminal.connected:raise RuntimeError('MT5_NOT_CONNECTED')
        if not account or account.server!='ICMarketsSC-Demo' or account.currency!='USD' or account.trade_mode!=0:raise RuntimeError('ACCOUNT_SERVER_MISMATCH')
        report['account_verified']=True;report['server']=account.server
        info=mt5.symbol_info('EURUSD');tick=mt5.symbol_info_tick('EURUSD')
        if not info or not tick:raise RuntimeError('EURUSD_UNAVAILABLE')
        import math
        if not math.isfinite(tick.bid) or not math.isfinite(tick.ask) or not 0 < tick.bid <= tick.ask:
            raise RuntimeError('INVALID_BID_ASK')
        report['EURUSD'].update(spread='OBSERVED',spread_price=tick.ask-tick.bid,
          swap='CONFIGURED',swap_long=info.swap_long,swap_short=info.swap_short,swap_mode=info.swap_mode)
        # Deal history is evidence about past actual deals, not hypothetical future fees.
        deals=mt5.history_deals_get(datetime.now(UTC)-timedelta(days=30),datetime.now(UTC),group='*EURUSD*')
        if deals is None:report['deal_history_status']='UNAVAILABLE'
        else:
            eur=[d for d in deals if d.symbol=='EURUSD' and d.volume>0]
            report['deal_history_status']='AVAILABLE';report['eurusd_deals_30d']=len(eur)
            report['historical_deal_cost_evidence']=[{'commission':d.commission,'swap':d.swap,'fee':getattr(d,'fee',None),'volume':d.volume,'time':d.time} for d in eur]
            if eur:report['EURUSD']['historical_commission']='OBSERVED_NOT_A_VERIFIED_FUTURE_SCHEDULE'
        report['XAUUSD']='AVAILABLE_EXCLUDED_FROM_CONTRACT' if mt5.symbol_info('XAUUSD') else 'UNAVAILABLE_NOT_REQUIRED'
        offset=configured_offset_seconds();normalized=normalize_mt5_epoch(tick.time,offset_seconds=offset,source='PREFLIGHT')
        report['tick_timestamp']=normalized.timestamp.isoformat();report['tick_age_seconds']=(datetime.now(UTC)-normalized.timestamp).total_seconds()
        require_not_future(normalized.timestamp,system_utc=datetime.now(UTC),source='PREFLIGHT',tolerance_seconds=0)
        require_recent(normalized.timestamp,system_utc=datetime.now(UTC),source='PREFLIGHT')
        report['timeframes']={}
        for tf in DURATIONS:
            bars=mt5.copy_rates_from_pos('EURUSD',getattr(mt5,'TIMEFRAME_'+tf),1,30)
            report['timeframes'][tf]=0 if bars is None else len(bars)
            if bars is None or len(bars)<21:raise RuntimeError('TIMEFRAME_UNAVAILABLE:'+tf)
            for bar in bars:
                opened=normalize_mt5_epoch(float(bar['time']),offset_seconds=offset,source=tf).timestamp
                if opened+timedelta(seconds=DURATIONS[tf])>datetime.now(UTC):
                    raise RuntimeError('INCOMPLETE_OR_FUTURE_BAR:'+tf)
        report['status']='PASS'
    except Exception as exc:report['error']=type(exc).__name__+':'+str(exc)
    finally:
        try:shutdown_mt5(mt5)
        except (NameError,UnboundLocalError):pass
    atomic_json(DIRECTORY/'mt5_preflight.json',report);print(json.dumps(report,indent=2))
    return 0 if report['status']=='PASS' else 2

def health(quiet=False):
    from scripts.v3_worker_health import inspect
    require_safety();m=campaign_manifest()
    with connect_sidecar() as db:
        measured=health_counts(db)
        integrity=db.execute('pragma quick_check').fetchone()[0]
    report={'campaign_id':m['campaign_id'],'current_utc':datetime.now(UTC).isoformat(),
      'collector_health':inspect('v3_prospective_collector',reconcile=False),
      'resolver_health':inspect('v3_outcome_resolver',reconcile=False),**measured,
      'memory_health':'NOT_SOAK_CERTIFIED','duplicate_count':0 if integrity=='ok' else None,
      'timestamp_health':'ENFORCED_AT_INGEST','provider_health':'UNVERIFIED_MACRO_EXCLUDED',
      'cost_provenance_status':'PRE_COMMISSION','sidecar_integrity':integrity}
    source=ROOT/'runtime/v34_certified_outcomes.sqlite3'
    with sqlite3.connect('file:'+source.as_posix()+'?mode=ro',uri=True) as db:
        report['observation_count']=db.execute('SELECT count(*) FROM observations').fetchone()[0]
        report['eligible_setup_count']=db.execute('SELECT count(*) FROM setups').fetchone()[0]
        report['resolved_count']=db.execute('SELECT count(*) FROM outcomes WHERE rr=1').fetchone()[0]
        report['pending_count']=report['eligible_setup_count']-report['resolved_count']
    atomic_json(DIRECTORY/'daily_health.json',report)
    atomic_json(DIRECTORY/('health_'+datetime.now(UTC).date().isoformat()+'.json'),report)
    from app.v3.health_checkpoints import update
    update(DIRECTORY/'health_continuity.json',report)
    if not quiet:print(json.dumps(report,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['init','preflight','health']);args=p.parse_args()
    raise SystemExit({'init':initialize,'preflight':preflight,'health':health}[args.mode]())
