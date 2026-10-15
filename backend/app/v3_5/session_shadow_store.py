"""Explicit, isolated shadow persistence; never targets existing campaign stores."""
import hashlib
import json
import sqlite3
from pathlib import Path
from contextlib import closing
from datetime import datetime,timezone
from .session_shadow import VERSION

ROOT=Path(__file__).resolve().parents[3]
STORE=ROOT/'runtime/v35_session_shadow_r1/shadow.sqlite3'


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False,
                      default=lambda x:x.isoformat()).encode()


class ShadowStore:
    def __init__(self,path=STORE,*,fixture=False):
        self.path=Path(path).resolve()
        if fixture and self.path.is_relative_to(ROOT):raise ValueError('FIXTURE_CANNOT_TARGET_PROJECT')
        if not fixture:
            if self.path!=STORE.resolve():raise ValueError('SEPARATE_SHADOW_STORE_REQUIRED')
            from .storage_health import require
            require(self.path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(self.path,timeout=1)) as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS shadow_records(
              record_id TEXT PRIMARY KEY,observation_id TEXT NOT NULL,version TEXT NOT NULL,
              record_kind TEXT NOT NULL CHECK(record_kind IN ('REPLAY','PROSPECTIVE','FIXTURE')),
              recorded_at_utc TEXT NOT NULL,payload TEXT NOT NULL,payload_sha256 TEXT NOT NULL,
              UNIQUE(observation_id,version));
              CREATE TRIGGER IF NOT EXISTS shadow_no_update BEFORE UPDATE ON shadow_records
                BEGIN SELECT RAISE(ABORT,'APPEND_ONLY'); END;
              CREATE TRIGGER IF NOT EXISTS shadow_no_delete BEFORE DELETE ON shadow_records
                BEGIN SELECT RAISE(ABORT,'APPEND_ONLY'); END;''')
            db.commit()

    def append(self,record,kind='PROSPECTIVE'):
        if kind not in ('REPLAY','PROSPECTIVE','FIXTURE'):raise ValueError('INVALID_RECORD_KIND')
        if record.get('strategy_shadow_version')!=VERSION or not record.get('observation_id') or record.get('symbol')!='EURUSD':raise ValueError('INVALID_SHADOW_RECORD')
        if record.get('mode')!='SHADOW_ONLY' or record.get('signal_output') is not None:raise ValueError('SIGNAL_OUTPUT_FORBIDDEN')
        payload=canonical(record).decode();digest=hashlib.sha256(payload.encode()).hexdigest()
        identity=hashlib.sha256((record['observation_id']+'|'+VERSION).encode()).hexdigest()
        with closing(sqlite3.connect(self.path,timeout=1)) as db:
            inserted=db.execute('INSERT OR IGNORE INTO shadow_records VALUES(?,?,?,?,?,?,?)',
                (identity,record['observation_id'],VERSION,kind,datetime.now(timezone.utc).isoformat(),payload,digest)).rowcount
            old=db.execute('SELECT payload_sha256,record_kind FROM shadow_records WHERE record_id=?',(identity,)).fetchone()
            if old!=(digest,kind):raise ValueError('SHADOW_IDENTITY_CONFLICT')
            db.commit()
        return {'record_id':identity,'status':'RECORDED' if inserted else 'ALREADY_RECORDED'}


def isolated_observe(builder,store,*,kind='PROSPECTIVE'):
    """Callable after commit once separately authorized; no decision return value."""
    try:return store.append(builder(),kind=kind)
    except Exception as exc:return {'status':'SHADOW_FAILED','error':type(exc).__name__+':'+str(exc)}


def prospective_status(path=STORE):
    """Read-only checkpoints; sample counts confer no statistical certification."""
    path=Path(path).resolve()
    if not path.exists():rows=[]
    else:
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
            db.execute('pragma query_only=on')
            rows=[json.loads(v[0]) for v in db.execute("select payload from shadow_records where record_kind='PROSPECTIVE'")]
    return {'prospective_eligible_observations':len(rows),'unique_days':len({r['timestamp_utc'][:10] for r in rows}),
            'sessions':sorted({r['session'] for r in rows}),
            'volatility_regimes':sorted({r['volatility']['volatility_state'] for r in rows}),
            'checkpoints':{str(n):{'reached':len(rows)>=n,'remaining':max(0,n-len(rows))} for n in (50,100,250,500)},
            'candidate_present':{name:sum(r['candidates'][name]['candidate_present'] is True for r in rows) for name in ('ASIA_LONDON_SWEEP_REVERSAL','NY_OVERLAP_CONTINUATION','LONDON_CLOSE_MEAN_REVERSION')},
            'cost_adjusted_validation':'NOT_AVAILABLE','edge_validated':False}
