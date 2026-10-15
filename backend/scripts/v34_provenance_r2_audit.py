"""Per-value provenance audit; reads observations only, never prospective returns."""
import ast,json,sqlite3
from datetime import datetime,UTC,timedelta
from pathlib import Path
from app.v3.prospective import *
from app.v3.certified_store import atomic_json

def main():
    m=campaign_manifest();verify_policy_files(m);c=load_contract()
    dest=DIRECTORY/'r2';dest.mkdir(exist_ok=True)
    source=ROOT/'certified_data/v3/V3_PROSPECTIVE_DATASET.jsonl'
    evidence=[];providers=[];cross=[]
    with source.open('rb') as f:
        f.seek(m['dataset_references'][source.name]['bytes'])
        for line in f:
            row=json.loads(line);d=utc(row['decision_timestamp']);ing=row.get('collection_ingestion_timestamp')
            for name in c['feature_order']:
                if '.session.' in name:continue
                value=row
                for key in name.split('.'):value=value[key]
                tf=name.split('.')[2] if '.contexts.' in name else None
                ctx=row['strategy_input_payload']['contexts'][tf] if tf else None
                stamp=ctx['source_candle_timestamp'] if ctx else row['tick_timestamp']
                close=(utc(stamp)+timedelta(seconds=DURATIONS[tf])).isoformat() if tf else None
                evidence.append(dict(observation_id=row['observation_id'],feature_name=name,source='MT5 EURUSD',
                  source_timestamp=stamp,acquisition_timestamp=ing,decision_cutoff=d.isoformat(),bar_open_time=stamp if tf else None,
                  bar_close_time=close,bar_completed=utc(close)<=d if tf else None,latest_legally_available_value=None,
                  recorded_value=value,raw_value_preserved=True,raw_broker_timestamp=row['raw_tick_timestamp'] if not tf else None,
                  normalized_utc_timestamp=stamp,normalization_applied=row['time_normalization_version'],
                  offset_seconds=row['mt5_server_utc_offset_seconds'],offset_provenance='configured explicit offset; per-bar original broker record not persisted' if tf else 'persisted raw/normalized timestamp and epoch',
                  stale_limit_seconds=2*DURATIONS[tf] if tf else 5,
                  future_data_guard='bar close <= cutoff' if tf else 'tick <= cutoff and age <=5s; reject, never clip',
                  point_in_time_safe=False,reason='ACQUIRED_AFTER_DECISION; no earlier exact-value capture evidence; latest legally available value cannot be reconstructed'))
            def records(v,path=''):
                if isinstance(v,dict):
                    if isinstance(v.get('record'),dict):yield path,v['record']
                    for k,x in v.items():yield from records(x,path+'.'+k if path else k)
            for name,r in records(row.get('v3_4_forex_intelligence',{}).get('providers',{})):
                providers.append(dict(observation_id=row['observation_id'],provider=name,observation_period=r.get('observation_date',r.get('timestamp')),
                  release_date=r.get('release_date'),release_timestamp=r.get('release_timestamp'),vintage=r.get('vintage'),
                  api_retrieval_time=r.get('retrieved_at'),decision_time=d.isoformat(),decision_time_safe=False,reason='MACRO_VINTAGE_UNCERTIFIED'))
            cross.append({'observation_id':row['observation_id'],'decision_timestamp':d.isoformat(),
              'certified':False,'reason':'No exact acquired cross-asset value with raw/normalized timestamp, calendar, completion and as-of evidence in certified contract'})
    atomic_json(dest/'market_features_individual.json',evidence);atomic_json(dest/'macro_records.json',providers);atomic_json(dest/'cross_asset_records.json',cross)
    # Exhaustive local-function call graph with exact source references, not a claim
    # that an AST graph independently certifies dynamic data dependencies.
    graph=[]
    for folder in ['v3','v3_1','v3_2','v3_3','v3_4']:
        for path in (ROOT/'backend/app'/folder).glob('*.py'):
            if path.name in ['prospective.py','worker_audit.py','worker_lifecycle.py','certified_store.py']:continue
            tree=ast.parse(path.read_text(encoding='utf8'))
            for node in ast.walk(tree):
                if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
                    calls=sorted({ast.unparse(n.func) for n in ast.walk(node) if isinstance(n,ast.Call)})
                    graph.append({'source':str(path.relative_to(ROOT)),'function':node.name,'line':node.lineno,'calls':calls,
                      'certified':False,'reason':'Upstream market acquisition/history not certified; AST dependencies alone are insufficient'})
    atomic_json(dest/'derived_dependency_graph.json',graph)
    summary={'campaign_id':m['campaign_id'],'cutoff':m['prospective_validation_start_utc'],
      'observations_audited':len(cross),'market_feature_names':len({r['feature_name'] for r in evidence}),
      'per_value_records':len(evidence),'market_certified':0,'calendar_certified':3,'derived_certified':0,
      'macro_certified':0,'cross_asset_certified':0,'complete_training_vector_certified':False,
      'no_source_rewrites':True,'created_utc':datetime.now(UTC).isoformat()}
    atomic_json(dest/'feature_summary.json',summary);print(json.dumps(summary,indent=2))

if __name__=='__main__':main()
