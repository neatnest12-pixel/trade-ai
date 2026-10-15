"""Read source datasets/journal; write separate audit artifacts only."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

from app.v3.certification import POLICY_HASH, require_certified_outcome
from app.v3.certified_store import atomic_json, counts
from v34_recertify import CAMPAIGN, DB, OBS, OUT, ROOT


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def metric(ids, outcomes, rr=1, field='realized_r'):
    rows=[outcomes[(oid,rr)] for oid in ids if (oid,rr) in outcomes]
    from app.v3.prospective import guarded_report_population
    rows=guarded_report_population(rows)
    vals=[r[field] for r in rows];positive=sum(max(v,0) for v in vals);negative=-sum(min(v,0) for v in vals)
    return {'eligible':len(ids),'resolved':len(rows),'pending':len(ids)-len(rows),
            'wins':sum(v>0 for v in vals),'losses':sum(v<0 for v in vals),'breakeven':sum(v==0 for v in vals),
            'timeouts':sum(r['exit_reason']=='TIME_EXIT' for r in rows),
            'tp_hits':sum(r['exit_reason']=='TP_HIT' for r in rows),'sl_hits':sum(r['exit_reason']=='SL_HIT' for r in rows),
            'win_rate_pct':100*sum(v>0 for v in vals)/len(vals) if vals else None,
            'expectancy_r':sum(vals)/len(vals) if vals else None,
            'profit_factor':positive/negative if negative else ('INF' if positive else None),
            'net_pre_commission_r':sum(vals)}


def main():
    db=sqlite3.connect(f'file:{DB.as_posix()}?mode=ro',uri=True);db.row_factory=sqlite3.Row
    assert db.execute('PRAGMA quick_check').fetchone()[0]=='ok'
    setup_rows={r['id']:dict(r) for r in db.execute('SELECT * FROM setups')}
    setups={oid:json.loads(r['payload']) for oid,r in setup_rows.items()}
    outcomes={};duplicates=0
    with OUT.open() as f:
        for line in f:
            r=json.loads(line);require_certified_outcome(r)
            key=(r['observation_id'],r['rr']);duplicates+=key in outcomes;outcomes[key]=r
            s=setups[r['observation_id']]
            assert s['timing_status']=='CERTIFIED' and s['decision_timestamp']==r['decision_timestamp']
            sign=1 if r['direction']=='BUY' else -1
            assert abs(sign*(r['exit_price']-r['entry_price'])/r['stop_distance']-r['realized_r'])<1e-9
            stored=db.execute('SELECT payload FROM outcomes WHERE outcome_id=?',(r['outcome_id'],)).fetchone()
            assert stored and json.loads(stored[0])==r
    assert not duplicates
    assert len(outcomes)==db.execute('SELECT count(*) FROM outcomes').fetchone()[0]
    frozen=json.loads((CAMPAIGN/'forensic_snapshot.json').read_text())
    preserved={}
    for name in ['V3_PROSPECTIVE_DATASET.jsonl','V3_AUTOMATIC_OUTCOMES_v3_outcome_r1.jsonl']:
        source=Path(frozen[name]['path']);actual=digest(source)
        preserved[name]={'bytes':source.stat().st_size,'sha256':actual,'preserved':actual==frozen[name]['sha256']}
        assert preserved[name]['preserved']
    groups={k:defaultdict(set) for k in ['strategy','regime','session','volatility','date','week','direction']}
    for oid,s in setups.items():
        stamp=datetime.fromisoformat(s['decision_timestamp'] or s['bar_timestamp']);year,week,_=stamp.isocalendar()
        dimensions={'strategy':s['families'],'regime':[s['regime']],'session':[s['session']],
                    'volatility':[s['volatility']],'date':[stamp.date().isoformat()],
                    'week':[f'{year}-W{week:02d}'],'direction':[s['direction']]}
        for dim,values in dimensions.items():
            for v in values:groups[dim][v].add(oid)
    legacy=[]
    with (ROOT/'certified_data/v3/V3_AUTOMATIC_OUTCOMES_v3_outcome_r1.jsonl').open() as f:
        for line in f:
            r=json.loads(line)
            if r['observation_id'] in setups:
                legacy.append({'outcome_id':r['outcome_id'],'observation_id':r['observation_id'],'rr':r['rr'],
                               'certification':'LEGACY_UNCERTIFIED','reason':'EXPLICIT_NORMALIZATION_MISSING','preserved':True})
    result={'audit_utc':datetime.now(UTC).isoformat(),'policy_hash':POLICY_HASH,'source_preservation':preserved,
            'total_observations':frozen['V3_PROSPECTIVE_DATASET.jsonl']['total_rows'],
            'v34_observations':frozen['V3_PROSPECTIVE_DATASET.jsonl']['v34_rows'],**counts(db),
            'timing':dict(Counter(s['timing_status'] for s in setups.values())),
            'feasibility_reasons':dict(Counter(str(r['reason']) for r in setup_rows.values())),
            'resolution_rate_pct':100*counts(db)['certified_resolved']/len(setups),
            'overall':metric(setups,outcomes),'fixed_contract_reference':metric(setups,outcomes,field='policy_r'),
            'by_rr':{str(rr):metric(setups,outcomes,rr) for rr in [1,1.5,2,3,5]},
            'groups':{dim:{k:metric(v,outcomes) for k,v in sorted(buckets.items())} for dim,buckets in groups.items()},
            'legacy_preserved_setup_count':len({r['observation_id'] for r in legacy}),
            'legacy_preserved_contract_count':len(legacy),'duplicate_contracts':duplicates,
            'outcome_export_sha256':digest(OUT),'cost_status':'PRE_COMMISSION_QUOTE_BASED',
            'commission':'UNAVAILABLE','actual_slippage':'UNAVAILABLE','swap':'UNAVAILABLE',
            'training_ready':False,'fit_model':0,'order_send':0}
    atomic_json(CAMPAIGN/'legacy_outcome_certification.json',legacy)
    atomic_json(CAMPAIGN/'final_audit.json',result)
    # Per-setup classifications remain inspectable without opening the SQLite DB.
    with (CAMPAIGN/'setup_classification.jsonl').open('w',encoding='utf8') as f:
        for oid,r in setup_rows.items():
            f.write(json.dumps({'observation_id':oid,'decision_timestamp':r['decision'],'direction':r['side'],
                                'timing':r['timing'],'feasibility':r['feasibility'],'reason':r['reason'],
                                'certified_1r_resolved':(oid,1) in outcomes})+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ['groups','source_preservation','by_rr']},indent=2))
    db.close()


if __name__=='__main__':main()
