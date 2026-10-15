"""Frozen prospective research boundary. No model fitting or execution APIs.

The sidecar is append-only by identity. Historical observations remain untouched.
Feature availability means demonstrably available to this collector, not merely
an economic observation date or a later downloaded historical candle.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .certification import COLLECTOR_HASH, POLICY_HASH, require_safety, utc

ROOT = Path(__file__).resolve().parents[3]
DIRECTORY = ROOT / 'runtime/v34_prospective_validation_r1'
MANIFEST = DIRECTORY / 'manifest.json'
CONTRACT = DIRECTORY / 'research_contract.json'
DENIED = re.compile(r'future|outcome|realized|net_r|gross_r|mfe|mae|exit_price|exit_timestamp|tp_hit|sl_hit|tp_result|sl_result|drift|example|placeholder|performance|rich_outcome|calibration|meta_label', re.I)
DURATIONS = dict(M1=60, M5=300, M15=900, H1=3600, H6=21600, H12=43200)
REQUIRED_PROVENANCE = ('feature_name','feature_version','source_provider','source_series',
 'raw_source_timestamp','release_timestamp','ingestion_timestamp','decision_timestamp',
 'availability_timestamp','transformation','lookback_window','timeframe','staleness',
 'point_in_time_safe','synthetic','future_derived','training_allowed','reason')


def digest_object(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def file_hash(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
    return h.hexdigest()


def sealed_read(path, key='schema_hash'):
    obj=json.loads(Path(path).read_text(encoding='utf8'))
    expected=obj.pop(key)
    if digest_object(obj)!=expected: raise ValueError('SEALED_DOCUMENT_MISMATCH')
    obj[key]=expected
    return obj


def campaign_manifest():
    m=sealed_read(MANIFEST,'manifest_hash')
    if m['policy_hash']!=COLLECTOR_HASH or m['outcome_policy_hash']!=POLICY_HASH:
        raise ValueError('CAMPAIGN_POLICY_MISMATCH')
    anchor=DIRECTORY/'manifest_anchor.sha256'
    if not anchor.exists() or anchor.read_text().strip()!=m['manifest_hash']:
        raise ValueError('CAMPAIGN_IMMUTABLE_ANCHOR_MISMATCH')
    return m


def verify_policy_files(manifest):
    for name,expected in manifest['frozen_policy_files'].items():
        if file_hash(ROOT/name)!=expected: raise ValueError('FROZEN_POLICY_CHANGED:'+name)


def load_contract(path=CONTRACT):
    c=sealed_read(path)
    if c['training_allowed'] is not False: raise ValueError('TRAINING_MUST_REMAIN_DISABLED')
    if c['target']['policy_hash']!=POLICY_HASH: raise ValueError('OUTCOME_POLICY_MISMATCH')
    if c['policy_hash']!=COLLECTOR_HASH: raise ValueError('FEATURE_POLICY_MISMATCH')
    if c['feature_order']!=[x['name'] for x in c['features']]: raise ValueError('ORDER_MISMATCH')
    if len(set(c['feature_order']))!=len(c['feature_order']): raise ValueError('DUPLICATE_FEATURE')
    if any(DENIED.search(n) for n in c['feature_order']): raise ValueError('FORBIDDEN_FEATURE')
    return c


def provenance_status(record, dependencies=()):
    """Explicit release, vintage, ingestion and complete-bar gates; never infer missing time."""
    try:
        if any(k not in record for k in REQUIRED_PROVENANCE): return False,'MISSING_PROVENANCE'
        if DENIED.search(record['feature_name']) or record['synthetic'] is not False or record['future_derived'] is not False:
            return False,'SYNTHETIC_OR_FUTURE_FIREWALL'
        d=utc(record['decision_timestamp']);s=utc(record['raw_source_timestamp'])
        ing=utc(record['ingestion_timestamp']);avail=utc(record['availability_timestamp'])
        if s>d or ing>d or avail>d or avail<ing: return False,'NOT_AVAILABLE_AT_DECISION'
        if record.get('kind')=='macro':
            if not record.get('vintage_timestamp') or not record.get('vintage_reference'):
                return False,'MACRO_VINTAGE_UNCERTIFIED'
            release=utc(record['release_timestamp']);vintage=utc(record['vintage_timestamp'])
            if release>d or vintage>d or avail<max(release,vintage):return False,'MACRO_RELEASE_AFTER_DECISION'
        if record.get('kind')=='bar':
            seconds=DURATIONS[record['timeframe']]
            if s+timedelta(seconds=seconds)>d or avail<s+timedelta(seconds=seconds):
                return False,'INCOMPLETE_BAR'
        if record.get('kind')=='cross_asset' and (not record.get('normalization_verified') or not record.get('market_calendar')):
            return False,'CROSS_ASSET_CALENDAR_OR_NORMALIZATION_UNPROVEN'
        if record['staleness'] is None or record['staleness']<0 or record['staleness']>record.get('max_staleness_seconds',0):
            return False,'STALE_OR_UNSPECIFIED_LIMIT'
        if any(not provenance_status(p)[0] for p in dependencies): return False,'UNCERTIFIED_DEPENDENCY'
        if record.get('kind')=='derived' and not dependencies:return False,'MISSING_DEPENDENCIES'
        if not re.fullmatch('[0-9a-f]{64}',record.get('evidence_sha256','')):return False,'MISSING_SOURCE_EVIDENCE'
        if record.get('source_provider') in (None,'UNPROVEN') or not record.get('transformation'):return False,'UNPROVEN_SOURCE'
        return True,'POINT_IN_TIME_EVIDENCE_VERIFIED'
    except (KeyError,ValueError,TypeError,OverflowError):return False,'INVALID_PROVENANCE'


def asof(rows, decision, max_age_seconds):
    """No nearest-future join; input must already have certified source normalization."""
    d=utc(decision);valid=[]
    for r in rows:
        if r.get('point_in_time_safe') and utc(r['availability_timestamp'])<=d and utc(r['timestamp'])<=d:
            if 0<=(d-utc(r['timestamp'])).total_seconds()<=max_age_seconds:valid.append(r)
    return max(valid,key=lambda r:utc(r['timestamp'])) if valid else None


def validate_vector(values, provenance, contract, schema_hash, *, training=False):
    if training or contract['training_allowed'] is not False:raise ValueError('TRAINING_DISABLED')
    if digest_object({k:v for k,v in contract.items() if k!='schema_hash'})!=contract['schema_hash']:raise ValueError('CONTRACT_CONTENT_MISMATCH')
    if schema_hash!=contract['schema_hash']:raise ValueError('SCHEMA_MISMATCH')
    if list(values)!=contract['feature_order']:raise ValueError('UNREGISTERED_MISSING_OR_MISORDERED_FEATURE')
    for spec in contract['features']:
        n=spec['name'];v=values[n]
        if DENIED.search(n):raise ValueError('FORBIDDEN_FEATURE')
        p=provenance.get(n,{})
        if p.get('feature_name')!=n or not provenance_status(p)[0]:raise ValueError('UNCERTIFIED_FEATURE:'+n)
        if spec['dtype']=='float64':valid=type(v) in (float,int) and math.isfinite(v)
        elif spec['dtype']=='int64':valid=type(v) is int
        else:valid=isinstance(v,str) and v in ['ASIA','LONDON','OVERLAP','NEW_YORK','OFF_HOURS']
        if not valid:raise ValueError('FEATURE_TYPE_OR_MISSING:'+n)
    return tuple(values[n] for n in contract['feature_order'])


def connect_sidecar(path=None):
    db=sqlite3.connect(path or DIRECTORY/'campaign.sqlite3',timeout=30)
    db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA synchronous=FULL')
    db.execute('PRAGMA cache_size=-4096');db.execute('PRAGMA temp_store=FILE')
    db.executescript('''
      CREATE TABLE IF NOT EXISTS dates(day TEXT PRIMARY KEY,ordinal INTEGER UNIQUE,bucket TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS members(id TEXT PRIMARY KEY,decision TEXT NOT NULL,day TEXT NOT NULL,
        bucket TEXT NOT NULL,campaign TEXT NOT NULL,policy TEXT NOT NULL,schema_hash TEXT NOT NULL,
        digest TEXT NOT NULL,eligible INTEGER NOT NULL,provenance TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS labels(outcome_id TEXT PRIMARY KEY,observation_id TEXT NOT NULL,
        campaign TEXT NOT NULL,policy TEXT NOT NULL,schema_hash TEXT NOT NULL,digest TEXT NOT NULL,
        rr REAL NOT NULL, UNIQUE(observation_id,rr,policy));
      CREATE TABLE IF NOT EXISTS checkpoints(name TEXT PRIMARY KEY,value TEXT NOT NULL);
      CREATE TRIGGER IF NOT EXISTS members_no_update BEFORE UPDATE ON members BEGIN SELECT RAISE(ABORT,'IMMUTABLE_MEMBERSHIP'); END;
      CREATE TRIGGER IF NOT EXISTS members_no_delete BEFORE DELETE ON members BEGIN SELECT RAISE(ABORT,'IMMUTABLE_MEMBERSHIP'); END;
      CREATE TRIGGER IF NOT EXISTS dates_no_update BEFORE UPDATE ON dates BEGIN SELECT RAISE(ABORT,'IMMUTABLE_DATE'); END;
      CREATE TRIGGER IF NOT EXISTS dates_no_delete BEFORE DELETE ON dates BEGIN SELECT RAISE(ABORT,'IMMUTABLE_DATE'); END;
    ''')
    return db


def bucket_for_date(db,day):
    old=db.execute('SELECT bucket FROM dates WHERE day=?',(day,)).fetchone()
    if old:return old[0]
    latest=db.execute('SELECT max(day),count(*) FROM dates').fetchone()
    if latest[0] and day<latest[0]:raise ValueError('LATE_DATE_REJECTED_NO_REBUCKET')
    ordinal=latest[1]+1
    bucket='A' if ordinal<=40 else 'B' if ordinal<=50 else 'C' if ordinal<=60 else 'D' if ordinal<=80 else 'POST_PLAN_BLINDED'
    db.execute('INSERT INTO dates VALUES(?,?,?)',(day,ordinal,bucket));return bucket


def register_observation(db,row,manifest,registry):
    from .certification import eligible
    if utc(row['decision_timestamp'])<utc(manifest['prospective_validation_start_utc']):return 'HISTORICAL_DEVELOPMENT_BASELINE'
    if row['collector_policy_hash']!=manifest['policy_hash']:raise ValueError('POLICY_MISMATCH')
    digest=digest_object(row)
    existing=db.execute('SELECT digest,bucket FROM members WHERE id=?',(row['observation_id'],)).fetchone()
    if existing:
        if existing[0]!=digest:raise ValueError('OBSERVATION_IDENTITY_MUTATED')
        return existing[1]
    day=utc(row['decision_timestamp']).date().isoformat()
    latest=db.execute('SELECT max(decision) FROM members').fetchone()[0]
    if latest and utc(row['decision_timestamp'])<utc(latest):raise ValueError('TIMESTAMP_REGRESSION')
    with db:
        bucket=bucket_for_date(db,day)
        db.execute('INSERT INTO members VALUES(?,?,?,?,?,?,?,?,?,?)',
          (row['observation_id'],row['decision_timestamp'],day,bucket,manifest['campaign_id'],manifest['policy_hash'],
           manifest['training_contract_schema_hash'],digest,int(eligible(row) is not None),json.dumps(registry,sort_keys=True)))
    return bucket


def guarded_report_population(rows,manifest=None):
    """Routine performance reports are historical-only, even before bucket-D arrives."""
    if manifest is None:
        if not MANIFEST.exists():return rows
        manifest=campaign_manifest()
    cutoff=utc(manifest['prospective_validation_start_utc'])
    return [r for r in rows if utc(r['decision_timestamp'])<cutoff]


def runtime_registry(row,ingestion):
    """Persist honest acquisition timing; later downloads cannot be backdated."""
    c=load_contract();result={};decision=row['decision_timestamp']
    for name in c['feature_order']:
        tf=name.split('.')[2] if name.startswith('strategy_input_payload.contexts.') else None
        calendar=name.startswith('strategy_input_payload.session.')
        if tf:source=row['strategy_input_payload']['contexts'][tf].get('source_candle_timestamp');kind='bar'
        elif calendar:source=row['strategy_input_payload']['decision_timestamp'];kind='calendar'
        else:source=row.get('tick_timestamp');kind='quote'
        # Calendar is a deterministic UTC transform; market evidence arrives at ingestion.
        available=source if calendar else ingestion
        record=dict(feature_name=name,feature_version='v34-provenance-r1',source_provider='UTC_CALENDAR' if calendar else 'MT5',
          source_series='EURUSD' if not calendar else 'UTC',raw_source_timestamp=source,release_timestamp=None,
          ingestion_timestamp=source if calendar else ingestion,decision_timestamp=decision,availability_timestamp=available,
          transformation='UTC calendar' if calendar else 'recorded completed OHLC' if tf else 'recorded quote/quote difference/age',
          lookback_window=1,timeframe=tf,staleness=(utc(decision)-utc(source)).total_seconds() if source else None,
          max_staleness_seconds=2*DURATIONS[tf] if tf else 120 if calendar else 5,kind=kind,
          point_in_time_safe=False,synthetic=False,future_derived=False,training_allowed=False,reason='',evidence_sha256=digest_object(row))
        safe,reason=provenance_status(record);record.update(point_in_time_safe=safe,reason=reason)
        # Feature eligibility is distinct from global campaign prohibition on training.
        record['training_allowed']=safe
        result[name]=record
    values={}
    try:
        for name in c['feature_order']:
            value=row
            for part in name.split('.'):value=value[part]
            values[name]=value
        validate_vector(values,result,c,c['schema_hash'])
        result['_contract_validation']={'accepted':True,'training_allowed':False}
    except (ValueError,KeyError,TypeError) as exc:
        result['_contract_validation']={'accepted':False,'training_allowed':False,'reason':str(exc)}
    return result


def record_collected(row,ingestion):
    if not MANIFEST.exists():return
    m=campaign_manifest();verify_policy_files(m)
    if utc(row['decision_timestamp'])<utc(m['prospective_validation_start_utc']):return
    with connect_sidecar() as db:register_observation(db,row,m,runtime_registry(row,ingestion))


def sync_members():
    """Recover sidecar after append/sidecar crash. Stream only committed complete rows."""
    if not MANIFEST.exists():return
    m=campaign_manifest();verify_policy_files(m)
    source=ROOT/'certified_data/v3/V3_PROSPECTIVE_DATASET.jsonl'
    with connect_sidecar() as db,source.open('rb') as f:
        checkpoint=db.execute("SELECT value FROM checkpoints WHERE name='observation_offset'").fetchone()
        offset=int(checkpoint[0]) if checkpoint else m['dataset_references'][source.name]['bytes']
        if source.stat().st_size<offset:raise ValueError('OBSERVATION_SOURCE_SHRANK')
        f.seek(offset)
        while True:
            raw=f.readline(4*1024*1024+1)
            if not raw:break
            if len(raw)>4*1024*1024:raise ValueError('OVERSIZED_OBSERVATION')
            if not raw.endswith(b'\n'):break
            row=json.loads(raw)
            if utc(row['decision_timestamp'])>=utc(m['prospective_validation_start_utc']):
                registry=runtime_registry(row,row.get('collection_ingestion_timestamp',datetime.now(UTC).isoformat()))
                register_observation(db,row,m,registry)
            with db:db.execute("INSERT OR REPLACE INTO checkpoints VALUES('observation_offset',?)",(str(f.tell()),))


def attach_labels(source_db):
    """Identity-only sidecar; does not expose or aggregate returns, including holdout."""
    if not MANIFEST.exists():return
    m=campaign_manifest()
    with connect_sidecar() as db:
        for oid,obs,rr,policy,payload in source_db.execute('SELECT outcome_id,observation_id,rr,policy_hash,payload FROM outcomes'):
            if not db.execute('SELECT 1 FROM members WHERE id=?',(obs,)).fetchone():continue
            h=hashlib.sha256(payload.encode()).hexdigest()
            old=db.execute('SELECT digest FROM labels WHERE outcome_id=?',(oid,)).fetchone()
            if old and old[0]!=h:raise ValueError('OUTCOME_IDENTITY_MUTATED')
            if policy!=m['outcome_policy_hash']:raise ValueError('OUTCOME_POLICY_MISMATCH')
            db.execute('INSERT OR IGNORE INTO labels VALUES(?,?,?,?,?,?,?)',(oid,obs,m['campaign_id'],policy,m['training_contract_schema_hash'],h,rr))


def health_counts(db):
    obs,eligible=db.execute('SELECT count(*),coalesce(sum(eligible),0) FROM members').fetchone()
    resolved=db.execute('SELECT count(*) FROM labels WHERE rr=1').fetchone()[0]
    return dict(new_prospective_observations=obs,new_eligible_setups=eligible,new_resolved_setups=resolved,new_pending_setups=eligible-resolved)
