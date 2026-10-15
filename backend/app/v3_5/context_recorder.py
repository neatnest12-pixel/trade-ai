"""Separate append-only shadow store. No imports of decisions or campaign DBs."""
import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime,timezone
from pathlib import Path
from .broker_context import snapshot,VERSION,TerminalEvidence,TickEvidence
from .market_context import utc

ROOT=Path(__file__).resolve().parents[3]
STORE=ROOT/'runtime/v35_context_r2/context.sqlite3'
AUTHORITIES={'BROKER_VERIFIED','BROKER_DERIVED','GENERIC_TEMPLATE','UNAVAILABLE'}


class ContextRecorder:
    def __init__(self,path=STORE,*,fixture=False):
        self.path=Path(path).resolve()
        if self.path!=STORE.resolve() and not fixture:raise ValueError('CONTEXT_ONLY_STORE_REQUIRED')
        if fixture and self.path.is_relative_to(ROOT):raise ValueError('CAMPAIGN_STORE_FORBIDDEN')
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(self.path,timeout=1)) as d:
            d.executescript("""
            CREATE TABLE IF NOT EXISTS context_records(
              context_record_id TEXT PRIMARY KEY, observation_id TEXT,
              context_version TEXT NOT NULL, recorded_at_utc TEXT NOT NULL,
              symbol TEXT NOT NULL CHECK(symbol='EURUSD'), decision_timestamp_utc TEXT NOT NULL,
              schedule_authority TEXT NOT NULL, record_kind TEXT NOT NULL,
              payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
              UNIQUE(observation_id,context_version));
            CREATE TRIGGER IF NOT EXISTS context_no_update BEFORE UPDATE ON context_records
              BEGIN SELECT RAISE(ABORT,'APPEND_ONLY'); END;
            CREATE TRIGGER IF NOT EXISTS context_no_delete BEFORE DELETE ON context_records
              BEGIN SELECT RAISE(ABORT,'APPEND_ONLY'); END;
            """)
            d.commit()

    def append(self,result,*,authority,evidence,kind='PROSPECTIVE'):
        if authority not in AUTHORITIES:raise ValueError('UNKNOWN_SCHEDULE_AUTHORITY')
        if kind not in {'PROSPECTIVE','LIVE_DIAGNOSTIC','REPLAY','FIXTURE'}:raise ValueError('INVALID_RECORD_KIND')
        value=result.to_dict();f,q,m=result.context,result.quote_health,result.market
        if f['instrument']!='EURUSD' or result.context_version!=VERSION:raise ValueError('INVALID_CONTEXT')
        stamp=utc(f['timestamp_utc']).isoformat()
        if kind=='PROSPECTIVE' and not result.observation_id:raise ValueError('OBSERVATION_ID_REQUIRED')
        identity=result.observation_id or kind+'|'+stamp
        record_id=hashlib.sha256((identity+'|'+VERSION).encode()).hexdigest()
        record=dict(context_record_id=record_id,context_version=VERSION,recorded_at_utc=datetime.now(timezone.utc).isoformat(),
                    observation_id=result.observation_id,symbol='EURUSD',decision_timestamp_utc=stamp,
                    current_session=f['current_session'],active_sessions=f['active_sessions'],market_state=m['effective_market_state'],
                    schedule_authority=authority,feed_state=q['feed_state'],tick_timestamp_utc=q['normalized_tick_timestamp'],
                    tick_age_seconds=q['tick_age_seconds'],tick_fresh=q['tick_is_fresh'],tick_advancing=q['tick_is_advancing'],
                    spread_pips=q['spread_pips'],spread_state=f['spread_state'],volatility_state=f['volatility_state'],
                    source_evidence=evidence,record_kind=kind,snapshot=value)
        payload=json.dumps(record,sort_keys=True,separators=(',',':'),allow_nan=False)
        digest=hashlib.sha256(payload.encode()).hexdigest()
        with closing(sqlite3.connect(self.path,timeout=1)) as d:
            existing=d.execute('SELECT context_record_id FROM context_records WHERE context_record_id=?',(record_id,)).fetchone()
            if existing:return {'status':'ALREADY_RECORDED','id':existing[0]}
            inserted=d.execute('INSERT OR IGNORE INTO context_records VALUES(?,?,?,?,?,?,?,?,?,?)',
                      (record_id,result.observation_id,VERSION,record['recorded_at_utc'],'EURUSD',stamp,authority,kind,payload,digest))
            d.commit()
        return {'status':'RECORDED' if inserted.rowcount else 'ALREADY_RECORDED','id':record_id}


def isolated_record(builder,recorder,**metadata):
    """Never propagate recorder/builder errors into the committed decision path."""
    try:return recorder.append(builder(),**metadata)
    except Exception as exc:return {'status':'SHADOW_RECORD_FAILED','error':type(exc).__name__+':'+str(exc)}


def observe_committed(cycle):
    """Post-commit context only. No current/future MT5 query or decision output."""
    try:
        if not cycle.get('data_training_eligible'):return
        frozen=cycle['snapshot'];t=utc(frozen['decision_time_utc'])
        quote=frozen['feature_values'].get('current_tick_quote',{})
        raw=quote.get('raw_epoch_seconds')
        tick=None
        # No invented normalization: use the original cycle's canonical tick evidence.
        # Existing quote stores raw broker epoch plus the certified configured offset.
        if raw is not None:
            from app.v3_4.mt5_time import normalize_mt5_epoch
            normalized=normalize_mt5_epoch(raw,offset_seconds=quote['configured_utc_offset_seconds'],source='EURUSD').timestamp
            tick=TickEvidence(normalized,t,quote['bid'],quote['ask'],raw)
        term=TerminalEvidence(t,t,None,None,None,quote.get('broker_server'),True,None,
                              frozen.get('clock_safe'),None)
        r=snapshot(t,broker='ICMarkets',expected_server='ICMarketsSC-Demo',terminal=term,tick=tick,
                   observation_id=cycle['observation_id'])
        ContextRecorder().append(r,authority='GENERIC_TEMPLATE',evidence={
            'source':'COMMITTED_DECISION_SNAPSHOT','snapshot_sha256':frozen['snapshot_sha256'],
            'tick_evidence_id':cycle.get('tick_evidence_id'),
            'availability_basis':'Certified decision timestamp is an upper bound; exact acquisition time is not copied',
            'terminal_connection_evidence':'UNAVAILABLE in committed snapshot'})
    except Exception as exc:
        # stdout only, never mutate saved campaign state or interrupt collection.
        print(json.dumps({'event':'SHADOW_CONTEXT_FAILURE','error':type(exc).__name__+':'+str(exc)}),flush=True)
