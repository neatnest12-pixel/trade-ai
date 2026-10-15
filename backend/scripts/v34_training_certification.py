"""Read-only source audit. Writes only certification artifacts; never fits anything."""
from __future__ import annotations
import ast
import collections
import csv
import hashlib
import heapq
import json
import math
import re
import sqlite3
import statistics
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.v3.prospective import (DURATIONS, DENIED, campaign_manifest, verify_policy_files,
    load_contract, file_hash, provenance_status, digest_object)
from app.v3.certification import COLLECTOR_HASH, COLLECTOR_VERSION, POLICY_HASH

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / 'runtime/v34_training_certification'
LEAK = re.compile(r'future|outcome|realized|net_r|gross_r|mfe|mae|exit_|resolved|tp_hit|sl_hit|timeout|drift|example|placeholder|performance|calibration|meta_label|(?:^|\.)(?:observation_id|setup_id|snapshot_hash|record_hash|row_sequence|byte_offset|policy_hash|collector_policy_hash|model_fit_calls|broker_execution_calls)(?:$|\.)', re.I)
PRIORITY = ['POLICY_MISMATCH','OUTCOME_UNCERTIFIED','TIMESTAMP_INVALID','MISSING_REQUIRED_FEATURE',
            'FEATURE_PROVENANCE_UNCERTIFIED','MACRO_RELEASE_UNCERTIFIED','CROSS_ASSET_UNCERTIFIED','OTHER_FAIL_CLOSED']

def utc(x):
    d = datetime.fromisoformat(x)
    if d.tzinfo is None: raise ValueError('NAIVE_TIMESTAMP')
    return d.astimezone(UTC)

def get(row, name):
    for p in name.split('.'):
        if not isinstance(row, dict) or p not in row: return None
        row = row[p]
    return row

def flatten(obj, prefix=''):
    if isinstance(obj,dict):
        for k,v in obj.items(): yield from flatten(v, f'{prefix}.{k}' if prefix else k)
    elif isinstance(obj,list):
        if not obj: yield prefix+'[]',None
        for v in obj: yield from flatten(v,prefix+'[]')
    else: yield prefix,obj

def missing(v):
    return v is None or (isinstance(v,float) and not math.isfinite(v)) or (isinstance(v,str) and v.upper() in {'UNAVAILABLE','FUTURE','UNKNOWN','NOT_AVAILABLE','NEWS_DATA_UNAVAILABLE'})

def strict_record(record, dependencies=()):
    if record.get('kind')=='macro' and record.get('release_time_status')=='DATE_ONLY':
        return False,'DATE_ONLY_RELEASE'
    if record.get('kind')=='derived':
        if not dependencies or any(not safe for safe,_ in dependencies):return False,'UNCERTIFIED_DEPENDENCY'
    if record.get('kind')=='derived':
        # The recursive evidence is checked above; use a raw evidence record for own timestamps.
        record={**record,'kind':'quote'}
    return provenance_status(record)

def certify_graph(name, graph, records, visiting=()):
    if name in visiting:return False,'DEPENDENCY_CYCLE'
    if name not in records:return False,'MISSING_PROVENANCE'
    dependencies=[certify_graph(n,graph,records,visiting+(name,)) for n in graph.get(name,[])]
    return strict_record(records[name],dependencies)

def check_feature(row,name,persisted):
    value=get(row,name)
    if missing(value):return False,'MISSING_REQUIRED_FEATURE'
    if LEAK.search(name):return False,'LEAKAGE_FIELD'
    try:
        d=utc(row['decision_timestamp'])
        if name.startswith('strategy_input_payload.session.'):
            t=utc(row['strategy_input_payload']['decision_timestamp'])
            session='ASIA' if t.hour<7 else 'LONDON' if t.hour<12 else 'OVERLAP' if t.hour<16 else 'NEW_YORK' if t.hour<21 else 'OFF_HOURS'
            expected={'utc_hour':t.hour,'weekday':t.weekday(),'session':session}[name.rsplit('.',1)[1]]
            return (True,'DETERMINISTIC_UTC_CALENDAR') if t<=d and value==expected else (False,'CALENDAR_MISMATCH')
        tf=name.split('.')[2] if '.contexts.' in name else None
        if tf:
            stamp=utc(row['strategy_input_payload']['contexts'][tf]['source_candle_timestamp'])
            if stamp+timedelta(seconds=DURATIONS[tf])>d:return False,'INCOMPLETE_BAR'
        else:
            stamp=utc(row['tick_timestamp'])
            if stamp>d:return False,'FUTURE_TICK'
            if (d-stamp).total_seconds()>5:return False,'STALE_TICK'
            if not (0<row['bid']<=row['ask']):return False,'INVALID_QUOTE'
            if abs(row['spread']-(row['ask']-row['bid']))>1e-10:return False,'INVALID_SPREAD'
            if row.get('mt5_server_utc_offset_seconds')!=10800:return False,'OFFSET_MISMATCH'
            if abs((utc(row['raw_tick_timestamp'])-stamp).total_seconds()-10800)>0.001:return False,'NORMALIZATION_MISMATCH'
        # Never construct an old acquisition record from today's knowledge.
        evidence=persisted.get(name)
        if evidence:
            if evidence.get('decision_timestamp')!=row['decision_timestamp']:return False,'EVIDENCE_DECISION_MISMATCH'
            if evidence.get('evidence_sha256')!=digest_object(row):return False,'EVIDENCE_VALUE_BINDING_MISMATCH'
            if utc(evidence['raw_source_timestamp'])!=stamp:return False,'EVIDENCE_SOURCE_MISMATCH'
            return strict_record(evidence)
        ing=row.get('collection_ingestion_timestamp')
        if not ing:return False,'MISSING_ACQUISITION_EVIDENCE'
        if utc(ing)>d:return False,'ACQUIRED_AFTER_DECISION'
        return False,'MISSING_VALUE_LEVEL_ACQUISITION_EVIDENCE'
    except (ValueError,KeyError,TypeError):return False,'TIMESTAMP_OR_PROVENANCE_INVALID'

def membership(row,outcome,required,persisted):
    reasons=[];checks={n:check_feature(row,n,persisted) for n in required}
    if row.get('collector_policy_hash')!=COLLECTOR_HASH or row.get('collector_policy_version')!=COLLECTOR_VERSION:reasons.append('POLICY_MISMATCH')
    if outcome.get('certification')!='CERTIFIED' or outcome.get('outcome_policy_hash')!=POLICY_HASH or outcome.get('rr')!=1:reasons.append('OUTCOME_UNCERTIFIED')
    bad={n:reason for n,(safe,reason) in checks.items() if not safe}
    for reason in bad.values():
        if reason=='MISSING_REQUIRED_FEATURE':reasons.append(reason)
        elif reason in {'FUTURE_TICK','STALE_TICK','INCOMPLETE_BAR','OFFSET_MISMATCH','NORMALIZATION_MISMATCH','TIMESTAMP_OR_PROVENANCE_INVALID','CALENDAR_MISMATCH'}:reasons.append('TIMESTAMP_INVALID')
        else:reasons.append('FEATURE_PROVENANCE_UNCERTIFIED')
    reasons=[x for x in PRIORITY if x in reasons]
    return dict(setup_id=outcome['observation_id'],observation_id=row['observation_id'],decision_time=row['decision_timestamp'],
        direction=outcome['direction'],label='POSITIVE' if outcome['realized_r']>0 else 'NEGATIVE' if outcome['realized_r']<0 else 'BREAKEVEN',
        realized_r=outcome['realized_r'],training_eligible=not reasons,primary_exclusion_reason=reasons[0] if reasons else None,
        all_exclusion_reasons=reasons,feature_exclusion_reasons=bad,policy_version=row.get('collector_policy_version'),policy_hash=row.get('collector_policy_hash')),checks

def split_safe(train_decision,train_end,next_start,embargo=86400):
    return utc(train_decision)<utc(next_start) and utc(train_end)+timedelta(seconds=60+embargo)<utc(next_start)

def metrics(xs,cost=0):
    v=[x['realized_r']-cost for x in xs];w=[x for x in v if x>0];l=[x for x in v if x<0]
    return dict(n=len(v),buy=sum(x['direction']=='BUY' for x in xs),sell=sum(x['direction']=='SELL' for x in xs),
      wins=len(w),losses=len(l),breakeven=len(v)-len(w)-len(l),win_rate=100*len(w)/len(v) if v else None,
      expectancy=sum(v)/len(v) if v else None,pf=sum(w)/-sum(l) if l else None,total_r=sum(v),positive_r=sum(w))

def group_metrics(rows,key):
    groups=collections.defaultdict(list)
    for x in rows:
        values=x[key] if isinstance(x[key],list) else [x[key]]
        for v in values:groups[v].append(x)
    return {k:metrics(v) for k,v in sorted(groups.items())}

def family(name):
    n=name.lower()
    if LEAK.search(n):return 'DENIED_LABEL_OR_IDENTIFIER'
    if 'providers' in n or any(k in n for k in ['macro','yield','central_bank','rate_differential']):return 'MACRO_RATES'
    if 'cross_asset' in n or 'usd_proxy' in n or 'equity_proxy' in n:return 'CROSS_ASSET'
    if '.session' in n:return 'SESSION'
    if any(k in n for k in ['liquidity','structure','microstructure','spread_regime']):return 'STRUCTURE_LIQUIDITY_MICROSTRUCTURE'
    if any(k in n for k in ['risk','stop','carry','cost','exposure']):return 'RISK_COST'
    if any(k in n for k in ['regime','volatility','atr']):return 'VOLATILITY_REGIME'
    if any(k in n for k in ['trend','ema','momentum','return','rsi','macd']):return 'TREND_MOMENTUM'
    if any(k in n for k in ['strategy','confluence','breakout','pullback','reversion','setup_states']):return 'STRATEGY_CONFLUENCE'
    return 'PRICE_MTF_OR_METADATA'

def macro_record_status(p, decision):
    release=p.get('release_timestamp') or p.get('publication_timestamp')
    retrieval=p.get('retrieved_at')
    if p.get('release_time_status')=='DATE_ONLY':time_type='DATE_ONLY'
    elif release and 'T' in str(release):time_type='EXACT_RELEASE_TIMESTAMP'
    elif p.get('release_date') or p.get('observation_date') or p.get('timestamp') or p.get('effective_or_release_timestamp'):time_type='DATE_ONLY'
    elif p.get('observation_period'):time_type='DATE_ONLY'
    else:time_type='RETRIEVAL_ONLY' if retrieval else 'UNKNOWN'
    status='MACRO_FAIL_CLOSED'
    if p.get('state')=='UNAVAILABLE':status='MACRO_MISSING'
    else:
        for t in [release,retrieval]:
            try:
                if t and utc(t)>utc(decision):status='MACRO_UNSAFE'
            except (ValueError,TypeError):pass
    return time_type,status

def write(name,data):
    (DEST/name).write_text(json.dumps(data,indent=2,sort_keys=True,allow_nan=False),encoding='utf8')

def main():
    baseline=json.loads((DEST/'baseline.json').read_text());manifest=campaign_manifest();verify_policy_files(manifest);contract=load_contract()
    db=sqlite3.connect((ROOT/'runtime/v34_certified_outcomes.sqlite3').as_uri()+'?mode=ro',uri=True);db.execute('BEGIN')
    rows=[]
    for payload,setup,off,length in db.execute('SELECT o.payload,s.payload,i.byte_offset,i.byte_length FROM outcomes o JOIN setups s ON s.id=o.observation_id JOIN observations i ON i.id=o.observation_id WHERE o.rr=1'):
        o=json.loads(payload);s=json.loads(setup);o.update({k:s[k] for k in ['regime','session','volatility']});o['_offset']=off;o['_length']=length
        o['date']=o['decision_timestamp'][:10];o['week']=utc(o['decision_timestamp']).strftime('%G-W%V');rows.append(o)
    rows.sort(key=lambda x:(x['decision_timestamp'],x['observation_id']));db.close()
    side=sqlite3.connect((ROOT/'runtime/v34_prospective_validation_r1/campaign.sqlite3').as_uri()+'?mode=ro',uri=True)
    provenance={i:json.loads(p) for i,p in side.execute('select id,provenance from members')}
    if any(bucket=='D' for bucket, in side.execute('select bucket from members')):raise RuntimeError('HOLDOUT_PRESENT_REQUIRES_BLINDED_POPULATION')
    side.close();inventory={};members=[];evidence_counts=collections.Counter();macro=[];cross=[]
    source_files=[ROOT/'backend/scripts/v3_prospective_collector.py']
    for folder in ['v3','v3_1','v3_2','v3_3','v3_4']:source_files+=list((ROOT/'backend/app'/folder).rglob('*.py'))
    graph=[];literal_sources=collections.defaultdict(set)
    for p in sorted(source_files):
        tree=ast.parse(p.read_text(encoding='utf8'));rel=str(p.relative_to(ROOT))
        for node in ast.walk(tree):
            if isinstance(node,ast.Constant) and isinstance(node.value,str):literal_sources[node.value].add(f'{rel}:{node.lineno}')
            if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
                graph.append(dict(source=rel,function=node.name,line=node.lineno,calls=sorted({ast.unparse(n.func) for n in ast.walk(node) if isinstance(n,ast.Call)})))
    with (ROOT/'certified_data/v3/V3_PROSPECTIVE_DATASET.jsonl').open('rb') as f,(DEST/'feature_evidence.jsonl').open('w',encoding='utf8') as ev:
        for index,o in enumerate(rows):
            f.seek(o['_offset']);row=json.loads(f.read(o['_length']));member,checks=membership(row,o,contract['feature_order'],provenance.get(o['observation_id'],{}));members.append(member)
            per_path=collections.defaultdict(list)
            for name,value in flatten(row):per_path[name].append(value)
            for name,values in per_path.items():
                present=any(not missing(v) for v in values)
                item=inventory.setdefault(name,dict(feature_name=name,present_count=0,safe_count=0,unsafe_count=0,fail_closed_count=0,unique_values=set(),types=set(),reasons=collections.Counter()))
                item['present_count']+=present;item['types'].update(type(v).__name__ for v in values)
                for v in values:
                    if len(item['unique_values'])<2100:item['unique_values'].add(json.dumps(v,sort_keys=True))
                safe,reason=checks.get(name,(False,'DENIED_TARGET_OR_IDENTIFIER' if LEAK.search(name) else 'OUTSIDE_FROZEN_ALLOWLIST_UNCERTIFIED_LINEAGE'))
                item['safe_count']+=safe
                if not safe:
                    item['unsafe_count']+=bool(LEAK.search(name));item['fail_closed_count']+=not bool(LEAK.search(name))
                item['reasons'][reason]+=1
            for name,(safe,reason) in checks.items():
                tf=name.split('.')[2] if '.contexts.' in name else None;ctx=row.get('strategy_input_payload',{}).get('contexts',{}).get(tf,{}) if tf else {}
                stamp=ctx.get('source_candle_timestamp') if tf else row.get('tick_timestamp');calendar='.session.' in name
                if calendar:stamp=row['strategy_input_payload']['decision_timestamp']
                evidence_counts[reason]+=1
                ev.write(json.dumps(dict(observation_id=o['observation_id'],feature_name=name,value=get(row,name),source_timestamp=stamp,
                    bar_close=(utc(stamp)+timedelta(seconds=DURATIONS[tf])).isoformat() if tf and stamp else None,
                    raw_broker_timestamp=row.get('raw_tick_timestamp') if not tf and not calendar else None,
                    acquisition_timestamp=row.get('collection_ingestion_timestamp'),decision_timestamp=o['decision_timestamp'],safe=safe,reason=reason))+'\n')
            def provider_records(v,prefix=''):
                if isinstance(v,dict):
                    if isinstance(v.get('record'),dict):yield prefix,v['record']
                    for k,z in v.items():yield from provider_records(z,prefix+'.'+k if prefix else k)
            for name,p in provider_records(row.get('v3_4_forex_intelligence',{}).get('providers',{})):
                release=p.get('release_timestamp') or p.get('publication_timestamp');retrieval=p.get('retrieved_at');vintage=p.get('vintage_timestamp') or p.get('vintage')
                time_type,macro_status=macro_record_status(p,o['decision_timestamp'])
                macro.append(dict(observation_id=o['observation_id'],provider=name,release_time_status=time_type,release=release,retrieval=retrieval,vintage=vintage,decision=o['decision_timestamp'],status=macro_status,reason='OUTSIDE_CONTRACT; exact value/release/vintage/acquisition binding not certified'))
            cx=row.get('v3_4_forex_intelligence',{}).get('cross_asset');cross.append(dict(observation_id=o['observation_id'],value=cx,status='MISSING' if not cx or 'UNAVAILABLE' in str(cx.get('state','')) or cx.get('quality')=='UNAVAILABLE' else 'UNCERTIFIED'))
            # Full candidate scope remains separate from the frozen 31-field vector.
            # These are additional blockers, never an implicit change to that vector.
            member['all_exclusion_reasons']=list(dict.fromkeys(member['all_exclusion_reasons']+['MACRO_RELEASE_UNCERTIFIED','CROSS_ASSET_UNCERTIFIED']))
            member['training_eligible']=False
            member['primary_exclusion_reason']=next(r for r in PRIORITY if r in member['all_exclusion_reasons'])
            member['candidate_scope_blockers']={'macro':'No certified release/vintage/acquisition binding','cross_asset':cross[-1]['status']}
            if index%250==0:print(json.dumps({'resolved_rows_audited':index}),flush=True)
    n=len(rows);feature_rows=[];lineage=[]
    for name,item in sorted(inventory.items()):
        fam=family(name);tf=next((t for t in DURATIONS if f'.{t}.' in name),None);allowed=name in contract['feature_order'];calendar=name.startswith('strategy_input_payload.session.')
        sources=sorted(literal_sources.get(name.rsplit('.',1)[-1].replace('[]',''),set()))
        deps=['strategy_input_payload.decision_timestamp'] if calendar else [f'RAW_MT5_{tf}_COMPLETED_HISTORY','ACQUISITION_EVIDENCE'] if tf else ['RAW_MT5_QUOTE','ACQUISITION_EVIDENCE'] if name in contract['feature_order'] else ['UNPROVEN_UPSTREAM_LINEAGE']
        item.update(feature_family=fam,source=sources or ['NO_UNIQUE_PRODUCER_RESOLVED_FAIL_CLOSED'],raw_source_field=name,
          derivation='UTC calendar transform' if calendar else 'Persisted OHLC projection' if tf and name.rsplit('.',1)[-1] in ['open','high','low','close'] else 'See producer/call graph; exact raw dependency completeness unproven',
          timeframe=tf,event_timestamp_field=f'strategy_input_payload.contexts.{tf}.source_candle_timestamp' if tf else 'tick_timestamp',retrieval_timestamp_field='collection_ingestion_timestamp / per-provider retrieved_at',decision_timestamp_field='decision_timestamp',
          availability_rule='Exact value acquisition <= decision; completed bars; release/vintage required for macro; recursive upstream certification',
          provenance_field='campaign.members.provenance / feature_provenance / provider record',missingness_rule='Reject; no imputation',
          allowed_by_frozen_contract=allowed,training_eligibility='SAFE_CALENDAR_ONLY' if calendar and item['safe_count']==n else 'DENIED' if LEAK.search(name) else 'FAIL_CLOSED',
          missing_count=n-item['present_count'],missing_pct=100*(n-item['present_count'])/n,unique_value_count_capped=len(item['unique_values']),high_cardinality=len(item['unique_values'])>n*.5)
        item['types']=sorted(item['types']);del item['unique_values'];feature_rows.append(item)
        lineage.append(dict(feature=name,dependencies=deps,source_references=sources,certified=calendar and item['safe_count']==n,reason='Deterministic calendar' if calendar else 'Exact upstream acquisition evidence not established'))
    write('feature_inventory.json',feature_rows);write('feature_lineage.json',{'scope':'Conservative dependencies plus code call graph; unresolved dynamic lineage fails closed','features':lineage,'functions':graph})
    write('macro_provenance.json',macro);write('cross_asset_provenance.json',cross)
    for filename,pop in [('training_membership.jsonl',members),('excluded_membership.jsonl',[m for m in members if not m['training_eligible']]),('training_eligible_membership.jsonl',[m for m in members if m['training_eligible']])]:
        with (DEST/filename).open('w',encoding='utf8') as f:
            for m in sorted(pop,key=lambda x:(x['decision_time'],x['setup_id'])):f.write(json.dumps(m,sort_keys=True)+'\n')
    daily=group_metrics(rows,'date');weekly=group_metrics(rows,'week')
    with (DEST/'date_performance.csv').open('w',newline='',encoding='utf8') as f:
        writer=csv.DictWriter(f,fieldnames=['date',*metrics([]).keys(),'strategy_mix','regime_mix','session_mix','volatility_mix']);writer.writeheader()
        for date,m in daily.items():
            subset=[o for o in rows if o['date']==date];mix={k+'_mix':json.dumps(dict(collections.Counter(v for o in subset for v in (o['families'] if k=='strategy' else [o[k]]))),sort_keys=True) for k in ['strategy','regime','session','volatility']};writer.writerow({'date':date,**m,**mix})
    ranked=sorted(daily,key=lambda d:daily[d]['expectancy'],reverse=True)
    robustness={'day':daily,'week':weekly,'ranking_rule':'daily mean R; not total R','positive_days':sum(v['expectancy']>0 for v in daily.values()),'negative_days':sum(v['expectancy']<0 for v in daily.values()),'positive_weeks':sum(v['expectancy']>0 for v in weekly.values()),'negative_weeks':sum(v['expectancy']<0 for v in weekly.values()),'best_day':ranked[0],'worst_day':ranked[-1],'median_daily_expectancy':statistics.median(v['expectancy'] for v in daily.values()),'exclude_best':metrics([o for o in rows if o['date']!=ranked[0]]),'exclude_best_two':metrics([o for o in rows if o['date'] not in ranked[:2]]),'exclude_worst':metrics([o for o in rows if o['date']!=ranked[-1]])}
    for side_name in ['BUY','SELL']:
        subset=[o for o in rows if o['direction']==side_name];robustness[side_name]={'all':metrics(subset),'ex_sept23':metrics([o for o in subset if o['date']!='2026-09-23']),**{k:group_metrics(subset,k) for k in ['date','week','families','regime','session','volatility']}}
    write('robustness.json',robustness)
    def dependence(pop):
        heap=[];overlap=peak=clusters=0;cluster_end=None
        for o in pop:
            start=utc(o['decision_timestamp']).timestamp();end=utc(o['exit_timestamp']).timestamp()
            while heap and heap[0]<=start:heapq.heappop(heap)
            overlap+=bool(heap);heapq.heappush(heap,end);peak=max(peak,len(heap))
            if cluster_end is None or start>=cluster_end:clusters+=1
            cluster_end=max(end,cluster_end or end)
        v=[o['realized_r'] for o in pop];lag=None
        if len(v)>2 and statistics.pstdev(v[:-1]) and statistics.pstdev(v[1:]):lag=statistics.correlation(v[:-1],v[1:])
        return dict(n=len(pop),overlap_with_prior_setup=overlap,peak_concurrent_horizons=peak,connected_interval_clusters=clusters,lag1_return_correlation=lag,
          adjacent_same_direction=sum(a['direction']==b['direction'] for a,b in zip(pop,pop[1:])),adjacent_same_strategy_set=sum(sorted(a['families'])==sorted(b['families']) for a,b in zip(pop,pop[1:])),adjacent_same_session=sum(a['session']==b['session'] for a,b in zip(pop,pop[1:])),adjacent_same_regime=sum(a['regime']==b['regime'] for a,b in zip(pop,pop[1:])),
          per_minute=dict(collections.Counter(o['decision_timestamp'][:16] for o in pop)),per_hour=dict(collections.Counter(o['decision_timestamp'][:13] for o in pop)),per_day=dict(collections.Counter(o['date'] for o in pop)),ess=None,ess_reason='Overlapping outcomes, unequal date clusters and nonstationarity; no defensible single ESS estimated')
    sep=[o for o in rows if o['date']=='2026-09-23'];allm=metrics(rows);sepm=metrics(sep)
    concentration={'all':allm,'ex_sept23':metrics([o for o in rows if o['date']!='2026-09-23']),'sept23':sepm,'sept23_gross_positive_R_share':sepm['positive_r']/allm['positive_r'],'sept23_net_R_share':sepm['total_r']/allm['total_r'],
        'sept23_mixes':{k:group_metrics(sep,k) for k in ['direction','families','regime','session','volatility']},'sept23_hour':group_metrics([{**o,'hour':o['decision_timestamp'][:13]} for o in sep],'hour'),
        'price_move_evidence':'Entry/exit quote paths of certified labels only; not an independent market trend reconstruction',
        'sept23_price_ranges':{k:[min(o[k] for o in sep),max(o[k] for o in sep)] for k in ['entry_price','exit_price']},'sept23_dependence':dependence(sep),'all_dependence':dependence(rows)}
    write('dependence_report.json',concentration)
    write('cost_sensitivity.json',{'basis':'Hypothetical additional round-trip R deductions, not observed broker costs','results':{side:{str(cost):metrics([o for o in rows if side=='OVERALL' or o['direction']==side],cost) for cost in [0,.01,.02,.03,.05,.1]} for side in ['OVERALL','BUY','SELL']},'break_even_additional_cost':{side:metrics([o for o in rows if side=='OVERALL' or o['direction']==side])['expectancy'] for side in ['OVERALL','BUY','SELL']},'BUY_note':'Negative headroom means already below zero before additional costs'})
    split={'status':'DESIGN_ONLY_NOT_EXECUTED','current_feasible':False,'training_allowed':False,'bucket_plan':manifest['bucket_plan'],'train':'A first 40 prospective observed dates; expanding walk-forward train minimum 20 dates','validation':'Within A: next 5 observed dates','test':'Within A: following 5 dates; step 5 dates; reserve B next 10 dates for validation and C next 10 for locked evaluation','step':'5 observed dates','purge':'Remove any earlier partition setup whose decision through max_hold+60 seconds overlaps the next partition','embargo_seconds':86400,'boundary_rule':'Prior max_hold + 60 seconds + 86400 seconds < next partition start','final_holdout':'D next 20 dates (ordinals 61-80); untouched and blinded','historical_data':'Already inspected development data, never relabel as untouched holdout','no_shuffle':True,'fold_local_preprocessing':'Future campaign only; fit on training partition exclusively; none fitted here'}
    write('split_design.json',split)
    current=sqlite3.connect((ROOT/'runtime/v34_certified_outcomes.sqlite3').as_uri()+'?mode=ro',uri=True)
    hashes={i:hashlib.sha256(p.encode()).hexdigest() for i,p in current.execute('select outcome_id,payload from outcomes')};current.close()
    invariants={rel:file_hash(ROOT/rel)==v['sha256'] for rel,v in baseline['files'].items() if rel.endswith('.jsonl')}
    report=dict(observations=baseline['observations'],v34_observations=baseline['v34'],resolved_setups=n,features_inventoried=len(feature_rows),features_certified_safe=sum(x['training_eligibility']=='SAFE_CALENDAR_ONLY' for x in feature_rows),features_unsafe=sum(x['training_eligibility']=='DENIED' for x in feature_rows),features_fail_closed=sum(x['training_eligibility']=='FAIL_CLOSED' for x in feature_rows),training_eligible=sum(x['training_eligible'] for x in members),excluded=sum(not x['training_eligible'] for x in members),exclusions=dict(collections.Counter(x['primary_exclusion_reason'] for x in members)),feature_failure_instances=dict(evidence_counts),immutable_jsonl=invariants,outcome_payloads_unchanged=hashes==baseline['outcome_payload_hashes'],frozen_files_unchanged=all(file_hash(ROOT/rel)==h for rel,h in baseline['frozen_files'].items()),contract_unchanged=load_contract()['schema_hash']==baseline['contract_hash'],safe_to_train=False,certified_subset_available=False,calendar_only_subset='3 values may be safe; no approved complete training vector or scientifically justified alternate subset',date_coverage=len(daily),week_coverage=len(weekly),macro_record_status=dict(collections.Counter(x['status'] for x in macro)),macro_time_types=dict(collections.Counter(x['release_time_status'] for x in macro)),cross_asset_status=dict(collections.Counter(x['status'] for x in cross)),membership_deterministic=True,training_matrix_created=False,label_separation='Only membership target columns contain realized_r and label; feature inventory/evidence sourced from observations, not outcomes',missingness_warning='Provider availability/retrieval flags can encode post-decision acquisition conditions; excluded, no imputation',current_resolver=json.loads((ROOT/'runtime/v3_outcome_resolver_state.json').read_text()),collector=json.loads((ROOT/'runtime/v3_prospective_collector_state.json').read_text()))
    write('certification_report.json',report);print(json.dumps(report,indent=2))

if __name__=='__main__':main()
