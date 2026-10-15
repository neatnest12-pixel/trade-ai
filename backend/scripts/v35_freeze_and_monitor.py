"""Create V3.4 freeze manifests and display read-only V3.5 cohort status."""
from __future__ import annotations
import argparse, hashlib, json, sqlite3
from pathlib import Path
from app.v3_5.provenance import ROOT, STORE, COHORT_VERSION, SCHEMA_VERSION, COLLECTOR_VERSION, POLICY_VERSION, POLICY_HASH, monitor

DEST=ROOT/'runtime/v35_provenance'

def sha(path:Path)->str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def freeze()->dict:
    prior=ROOT/'runtime/v34_training_certification/baseline.json'
    baseline=json.loads(prior.read_text(encoding='utf8'))
    files={}
    rels=['certified_data/v3/V3_PROSPECTIVE_DATASET.jsonl','certified_data/v3/V3_CERTIFIED_OUTCOMES_time_r2.jsonl',
          'runtime/v34_certified_outcomes.sqlite3',*baseline.get('frozen_files',{}).keys()]
    cert_dir=ROOT/'runtime/v34_training_certification'
    rels += [str(p.relative_to(ROOT)) for p in cert_dir.rglob('*') if p.is_file()]
    rels=sorted(set(rels))
    for rel in rels:
        p=ROOT/rel
        if p.exists(): files[rel]={'path':str(p),'bytes':p.stat().st_size,'sha256':sha(p)}
    result={'classification':'RESEARCH_DIAGNOSTIC_ONLY','ml_training_eligible':False,
      'policy_version':baseline['policy_version'],'policy_hash':baseline['policy_hash'],
      'observation_count':baseline['observations'],'v34_observation_count':baseline['v34'],
      'resolved_setups':baseline['resolved'],'pending_setups':baseline['pending'],
      'immutable_file_manifest':files,'v3_4_observations_modified':False,'v3_4_outcomes_modified':False}
    (DEST/'v34_freeze_manifest.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n',encoding='utf8')
    return result

def main()->None:
    ap=argparse.ArgumentParser();ap.add_argument('--freeze-v34',action='store_true');args=ap.parse_args()
    DEST.mkdir(parents=True,exist_ok=True)
    if args.freeze_v34: freeze()
    db=sqlite3.connect(f'file:{STORE.as_posix()}?mode=ro',uri=True)
    print(json.dumps(monitor(db),indent=2,sort_keys=True));db.close()

if __name__=='__main__':main()
