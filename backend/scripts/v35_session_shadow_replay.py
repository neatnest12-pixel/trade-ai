"""Bounded read-only campaign replay; writes only a NEW analytical report.

Outcome labels are loaded AFTER feature construction and used only for descriptive
conditional analysis of the existing strategy. No hypothetical trade labels.
"""
import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from datetime import timedelta
from contextlib import closing
from bisect import bisect_right
from app.v3_5.session_shadow import Candle,build,utc,VERSION,DURATIONS
from app.v3_5.session_shadow_store import canonical
from app.v3_5.session_analysis import metrics
from app.v3_5.market_context import Sample

ROOT=Path(__file__).resolve().parents[2]


def fingerprints():
    result={};base=ROOT/'runtime/v35_provenance'
    for name,q in [('observations','select observation_id,decision_id,snapshot_sha256 from observations order by observation_id'),('setups','select setup_id,observation_id,snapshot_sha256 from setups order by setup_id'),('outcomes','select outcome_id,setup_id,status,payload,payload_sha256,appended_at_utc from outcomes order by outcome_id')]:
        path=base/('cohort.sqlite3' if name=='observations' else 'outcomes_evidence_r2.sqlite3')
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as d:
            rows=d.execute(q).fetchall()
        result[name]={'count':len(rows),'sha256':hashlib.sha256(json.dumps(rows,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()}
    result['decision_manifest']=hashlib.sha256((base/'frozen_decision_feature_manifest.json').read_bytes()).hexdigest()
    return result


def conditional_metrics(rows):
    values=metrics(rows);ordered=sorted(rows,key=lambda r:(r['exit_timestamp_utc'],r['outcome_id']))
    equity=peak=drawdown=0
    for r in ordered:
        equity+=r['gross_r'];peak=max(peak,equity);drawdown=max(drawdown,peak-equity)
    values.update(unique_days=len({r['decision_time_utc'][:10] for r in rows}),max_drawdown_R=drawdown if rows else None,
                  drawdown_basis='SEQUENTIAL EXISTING OUTCOME EXIT TIMES; overlapping positions and portfolio sizing not represented',
                  interpretation='EXISTING_STRATEGY_OUTCOMES_CONDITIONAL_ON_SHADOW_CONTEXT; NOT CANDIDATE TRADE RETURNS')
    return values


def replay(*,progress=lambda x:None):
    before=fingerprints();path=ROOT/'runtime/v35_provenance/cohort.sqlite3'
    files=['backend/app/v3_5/session_shadow.py','backend/app/v3_5/session_shadow_store.py','backend/app/v3_5/shadow_risk.py','backend/scripts/v35_session_shadow_replay.py']
    implementation={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in files}
    histories={};changes=0;observations=[]
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as d:
        d.execute('pragma query_only=on')
        observations=d.execute('select observation_id,eligible,payload from observations order by rowid').fetchall()
        # Streaming insertion order: no SQLite temporary sort or bulk evidence export.
        for (text,) in d.execute('select payload from provenance order by rowid'):
            v=json.loads(text)
            if v.get('kind')!='bar' or v.get('provenance_status')!='CERTIFIED' or v.get('instrument_series')!='EURUSD':continue
            tf=v['timeframe']
            if tf not in DURATIONS:continue
            op=utc(v['normalized_timestamp']);cl=utc(v['bar_close_utc']);av=utc(v['information_available_at_utc']);raw=v['raw_value']
            b=Candle(tf,op,cl,av,raw['open'],raw['high'],raw['low'],raw['close'],raw.get('tick_volume',0),v['evidence_id'])
            key=(tf,op);versions=histories.setdefault(key,[])
            geometry=(b.open,b.high,b.low,b.close,b.tick_volume)
            if versions and av<versions[-1].available_at:raise ValueError('NONCHRONOLOGICAL_PROVENANCE_ACQUISITION')
            if not versions or geometry!=tuple(getattr(versions[-1],k) for k in ('open','high','low','close','tick_volume')):
                versions.append(b);changes+=1
    progress({'evidence_bar_identities':len(histories),'distinct_known_versions':changes})
    indexed={key:([b.available_at for b in values],values) for key,values in histories.items()}
    outcomes={};groups={n:{'present':[],'absent':[],'unknown':[],'directions':{'UP':0,'DOWN':0},'days':set()} for n in ('ASIA_LONDON_SWEEP_REVERSAL','NY_OVERLAP_CONTINUATION','LONDON_CLOSE_MEAN_REVERSION')}
    detections=[];sample=None;eligible_count=0;spreads=[];volatilities=[];last_time=None
    spread_states={};volatility_states={}
    for identity,eligible,payload in observations:
        if not eligible:continue
        v=json.loads(payload);f=v['snapshot'];t=utc(f['decision_time_utc']);known={tf:[] for tf in DURATIONS}
        if last_time is not None and t<last_time:raise ValueError('NONCHRONOLOGICAL_OBSERVATION_REPLAY')
        last_time=t
        for (tf,op),(times,versions) in indexed.items():
            pos=bisect_right(times,t)-1
            if pos>=0 and versions[pos].close_time<=t:known[tf].append(versions[pos])
        bars=[]
        for tf,values in known.items():
            values.sort(key=lambda b:b.open_time)
            bars.extend([b for b in values if b.open_time>=t-timedelta(hours=24)] if tf=='M1' else values[-80:])
        quote=f['feature_values'].get('current_tick_quote',{});tick_time=None;available=None
        if quote.get('raw_epoch_seconds') is not None:
            from app.v3_4.mt5_time import normalize_mt5_epoch
            tick_time=normalize_mt5_epoch(quote['raw_epoch_seconds'],offset_seconds=quote['configured_utc_offset_seconds'],source='EURUSD').timestamp
            available=t # certified frozen decision bounds acquisition, exact time remains provenance metadata
        r=build(t,identity,bars,bid=quote.get('bid'),ask=quote.get('ask'),tick_time=tick_time,tick_available_at=available,
                spread_history=spreads,volatility_history=volatilities)
        spread_state=r['execution_context']['spread_state'];volatility_state=r['volatility']['volatility_state']
        spread_states[spread_state]=spread_states.get(spread_state,0)+1;volatility_states[volatility_state]=volatility_states.get(volatility_state,0)+1
        # Append only AFTER T was evaluated: current samples never enter their own reference distribution.
        sp=r['execution_context']['spread_pips'];rv=r['volatility']['short_term_realized_volatility']
        if sp is not None:spreads.append(Sample('EURUSD',t,t,sp*.0001,r['session']))
        if rv is not None:volatilities.append(Sample('EURUSD',t,t,rv,r['session']))
        if sample is None:sample=r
        eligible_count+=1
        for name,c in r['candidates'].items():
            status='unknown' if c['candidate_present'] is None else 'present' if c['candidate_present'] else 'absent'
            groups[name][status].append(identity)
            if status=='present':
                direction=c.get('potential_reversal_direction') or c.get('direction') or c.get('mean_reversion_direction')
                if direction in groups[name]['directions']:groups[name]['directions'][direction]+=1
                groups[name]['days'].add(t.date().isoformat())
        if eligible_count%50==0:progress({'eligible_replayed':eligible_count})
    # Labels deliberately arrive only after all feature/detection computations.
    p=ROOT/'runtime/v35_provenance/outcomes_evidence_r2.sqlite3'
    with closing(sqlite3.connect(p.as_uri()+'?mode=ro',uri=True)) as d:
        for (payload,) in d.execute("select payload from outcomes where status in ('WIN','LOSS','BREAKEVEN','TIMEOUT')"):
            row=json.loads(payload);outcomes[row['observation_id']]=row
    summaries={}
    for name,g in groups.items():
        summaries[name]={'N':len(g['present']),'unique_days':len(g['days']),'BUY_candidates':g['directions']['UP'],'SELL_candidates':g['directions']['DOWN'],
                         'absent_observations':len(g['absent']),'unknown_observations':len(g['unknown']),
                         'present_existing_outcomes':conditional_metrics([outcomes[x] for x in g['present'] if x in outcomes]),
                         'absent_existing_outcomes':conditional_metrics([outcomes[x] for x in g['absent'] if x in outcomes]),
                         'unknown_existing_outcomes':conditional_metrics([outcomes[x] for x in g['unknown'] if x in outcomes])}
    after=fingerprints()
    if after!=before:raise ValueError('CAMPAIGN_EVIDENCE_CHANGED_DURING_REPLAY')
    if implementation!={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in files}:raise ValueError('IMPLEMENTATION_CHANGED_DURING_REPLAY')
    report={'version':VERSION,'classification':'DESCRIPTIVE_ONLY_NOT_VALIDATED','eligible_replayed':eligible_count,'summaries':summaries,
            'fingerprints_before':before,'fingerprints_after':after,'immutability':'SAME',
            'prospective_records':0,'prospective_checkpoints':[50,100,250,500],'implementation_hashes':implementation,
            'historical_reference_states':{'spread':spread_states,'volatility':volatility_states},
            'warnings':['Small samples','Temporal clustering','Overlapping candidate definitions','Multiple comparisons','Existing-strategy selection bias',
                        'Candidate-specific entry/exit/cost contracts are undefined; no candidate trade profitability or edge is measured',
                        'Missing full session history and prior-day reference distributions remain UNKNOWN; not negative examples',
                        'No current MT5 history was used to reconstruct past knowledge','No confidence interval can establish an edge from absent/clustered candidate samples']}
    return report,sample


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);args=parser.parse_args()
    path=Path(args.output).resolve();allowed=(ROOT/'analysis_reports/session_shadow_r1').resolve()
    if not path.is_relative_to(allowed):raise ValueError('ANALYTICAL_REPORT_PATH_REQUIRED')
    if path.exists():raise ValueError('REPORT_ALREADY_EXISTS')
    report,sample=replay(progress=lambda x:print(json.dumps(x),flush=True))
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('xb') as out:out.write(canonical(report))
    # One bounded replay record proves real-store plumbing; all other replay results remain aggregate.
    from app.v3_5.session_shadow_store import ShadowStore
    try:recorded=ShadowStore().append(sample,'REPLAY') if sample else {'status':'NO_ELIGIBLE_SAMPLE'}
    except Exception as exc:recorded={'status':'NOT_RECORDED','error':str(exc)}
    print(json.dumps({'report':str(path),'shadow_sample':recorded,'summaries':report['summaries']}),flush=True)
