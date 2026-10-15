"""Independent read-only verification and diagnostics for the training audit."""
import collections
import hashlib
import json
import statistics
import sqlite3
from datetime import timedelta
from pathlib import Path

from scripts.v34_training_certification import ROOT, DEST, utc, metrics, group_metrics
from app.v3.certification import project_setup
from app.v3.prospective import DURATIONS, file_hash


def main():
    db=sqlite3.connect((ROOT/'runtime/v34_certified_outcomes.sqlite3').as_uri()+'?mode=ro',uri=True)
    db.execute('BEGIN')
    rows=[]; quality=collections.defaultdict(collections.Counter); tf_quality=collections.defaultdict(collections.Counter)
    with (ROOT/'certified_data/v3/V3_PROSPECTIVE_DATASET.jsonl').open('rb') as f:
        for payload,setup,offset,length in db.execute('SELECT o.payload,s.payload,i.byte_offset,i.byte_length FROM outcomes o JOIN setups s ON s.id=o.observation_id JOIN observations i ON i.id=o.observation_id WHERE o.rr=1'):
            o=json.loads(payload);s=json.loads(setup);f.seek(offset);r=json.loads(f.read(length))
            o.update({k:s[k] for k in ['regime','session','volatility']})
            d=utc(o['decision_timestamp']);o['date']=d.date().isoformat();o['week']=d.strftime('%G-W%V');rows.append(o)
            q=quality[o['date']];q['setups']+=1
            q['timing_contract_pass']+=project_setup(r)['timing_status']=='CERTIFIED'
            q['missing_acquisition']+=not bool(r.get('collection_ingestion_timestamp'))
            q['late_acquisition']+=bool(r.get('collection_ingestion_timestamp')) and utc(r['collection_ingestion_timestamp'])>d
            q['tick_raw_epoch_match']+=abs(float(r['raw_tick_epoch_seconds'])-utc(r['raw_tick_timestamp']).timestamp())<.001
            q['offset_10800']+=r['mt5_server_utc_offset_seconds']==10800
            for tf,sec in DURATIONS.items():
                c=r['strategy_input_payload']['contexts'].get(tf,{})
                if not c.get('available'):tf_quality[tf]['missing']+=1;continue
                start=utc(c['source_candle_timestamp']);tf_quality[tf]['present']+=1
                tf_quality[tf]['completed_by_decision']+=start+timedelta(seconds=sec)<=d
                # Broker-native H6/H12 anchors are tested in the fixed broker clock.
                tf_quality[tf]['broker_grid_aligned']+=int(start.timestamp()+10800)%sec==0
                tf_quality[tf]['raw_bar_timestamp_persisted']+=any(k in c for k in ['raw_timestamp','raw_source_timestamp','raw_epoch_seconds'])
    hashes={i:hashlib.sha256(p.encode()).hexdigest() for i,p in db.execute('select outcome_id,payload from outcomes')};db.close()
    rows.sort(key=lambda x:(x['decision_timestamp'],x['observation_id']))
    clusters=[]
    for o in rows:
        if not clusters or utc(o['decision_timestamp'])>=utc(clusters[-1]['end']):
            clusters.append({'start':o['decision_timestamp'],'end':o['exit_timestamp'],'rows':[]})
        c=clusters[-1];c['end']=max(c['end'],o['exit_timestamp'],key=utc);c['rows'].append(o)
    episodes=[]
    for c in clusters:
        xs=c['rows'];episodes.append({'start':c['start'],'end':c['end'],**metrics(xs),
            'first_entry':xs[0]['entry_price'],'last_entry':xs[-1]['entry_price'],
            'entry_min':min(x['entry_price'] for x in xs),'entry_max':max(x['entry_price'] for x in xs),
            'dates':sorted(set(x['date'] for x in xs))})
    daily=group_metrics(rows,'date');by_total=sorted(daily,key=lambda d:daily[d]['total_r'],reverse=True)
    direction_diagnostics={}
    for side in ['BUY','SELL']:
        xs=[x for x in rows if x['direction']==side];ex=[x for x in xs if x['date']!='2026-09-23']
        direction_diagnostics[side]={'all':metrics(xs),'excluding_sept23':metrics(ex),
            'all_groups':{k:group_metrics(xs,k) for k in ['date','week','families','regime','session','volatility']},
            'excluding_sept23_groups':{k:group_metrics(ex,k) for k in ['date','week','families','regime','session','volatility']}}
    labels=[int(x['realized_r']>0) for x in rows]
    start=json.loads((DEST/'verification_start.json').read_text())
    final_hashes={name:file_hash(ROOT/name) for name in start['files']}
    result={'observation_and_export_bytes_unchanged':{name:h==start['files'][name]['sha256'] for name,h in final_hashes.items()},
        'final_file_sha256':final_hashes,'certified_outcome_payloads_unchanged':hashes==start['outcome_payload_hashes'],
        'resolved_first':{'id':rows[0]['observation_id'],'decision':rows[0]['decision_timestamp']},
        'resolved_last':{'id':rows[-1]['observation_id'],'decision':rows[-1]['decision_timestamp']},
        'date_quality':quality,'timeframes':tf_quality,'holding_period_episodes':episodes,
        'lag1_positive_label_correlation':statistics.correlation(labels[:-1],labels[1:]),
        'total_R_ranked_days':by_total,
        'exclude_best_total_R_day':metrics([x for x in rows if x['date']!=by_total[0]]),
        'exclude_best_two_total_R_days':metrics([x for x in rows if x['date'] not in by_total[:2]]),
        'exclude_worst_total_R_day':metrics([x for x in rows if x['date']!=by_total[-1]]),
        'direction_diagnostics':direction_diagnostics,
        'limitations':['Price movement uses saved entry quotes, not an independent tick tape reconstruction.',
            'Connected intervals are dependence diagnostics, not an effective sample size.',
            'Strategy groups overlap; a multi-strategy setup counts once in membership.',
            'Raw histories and complete value-level acquisition lineage cannot be recovered from these rows.'],
        'fit_model':0,'order_send':0}
    (DEST/'verification_details.json').write_text(json.dumps(result,indent=2,sort_keys=True))
    print(json.dumps({k:v for k,v in result.items() if k not in ['direction_diagnostics','holding_period_episodes','date_quality']},indent=2))


if __name__=='__main__':main()
