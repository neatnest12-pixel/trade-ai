"""Initialize separate V3.5 manifests and report read-only cohort status."""
from __future__ import annotations
import json
from app.v3_5.provenance import ROOT, COHORT_VERSION, SCHEMA_VERSION, COLLECTOR_VERSION, POLICY, POLICY_VERSION, POLICY_HASH, STORE, OBSERVATIONS
from scripts.v35_freeze_and_monitor import freeze
from app.v3_5.provenance import connect, monitor

def main():
    dest=ROOT/'runtime/v35_provenance';dest.mkdir(parents=True,exist_ok=True)
    frozen=freeze()
    manifest={'cohort_version':COHORT_VERSION,'schema_version':SCHEMA_VERSION,'collector_version':COLLECTOR_VERSION,
      'policy_version':POLICY_VERSION,'policy':POLICY,'policy_hash':POLICY_HASH,'observations_path':str(OBSERVATIONS),
      'append_only_store_path':str(STORE),'fit_model':0,'order_send':0,'demo_execution':False,'real_execution':False,
      'v34_classification':'RESEARCH_DIAGNOSTIC_ONLY','v34_ml_training_eligible':False,
      'collection_authorized':False,'safe_to_train':False}
    (dest/'cohort_manifest.json').write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n',encoding='utf8')
    db=connect();print(json.dumps({'v34_freeze_files':len(frozen['immutable_file_manifest']),'cohort_manifest':manifest,'monitor':monitor(db)},indent=2));db.close()
if __name__=='__main__':main()
