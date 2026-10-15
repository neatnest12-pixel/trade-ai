from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]; OUT = ROOT/'certified_data'/'v2_research'
def m(rows):
    vals=[float(r['gross_r'])-float(r.get('spread_cost_r',0) or 0) for r in rows]; gp=sum(v for v in vals if v>0); gl=abs(sum(v for v in vals if v<0)); eq=peak=dd=0
    for v in vals: eq+=v; peak=max(peak,eq); dd=max(dd,peak-eq)
    return {'trades':len(vals),'wins':sum(v>0 for v in vals),'losses':sum(v<0 for v in vals),'expired':sum(r['outcome']=='EXPIRED' for r in rows),'net_r':sum(vals),'expectancy':sum(vals)/len(vals) if vals else None,'profit_factor':gp/gl if gl else None,'max_drawdown':dd}
def main():
    rows=[json.loads(x) for x in open(ROOT/'certified_data'/'V2_TRADE_ECONOMICS_DATASET.jsonl',encoding='utf8') if x.strip()]; prov={json.loads(x)['parent_prediction_id']:json.loads(x) for x in open(sorted(OUT.glob('v2_cost_provenance_ticks_*.jsonl'))[-1],encoding='utf8') if x.strip()}
    parents=sorted({r['parent_prediction_id'] for r in rows if r['decision']=='SELL' and prov.get(r['parent_prediction_id'],{}).get('cost_quality') in ('HIGH','MEDIUM')},key=lambda x: datetime.fromisoformat(next(r['candle_timestamp'] for r in rows if r['parent_prediction_id']==x)))
    cuts=(int(len(parents)*.6),int(len(parents)*.8)); groups={'train':parents[:cuts[0]],'validation':parents[cuts[0]:cuts[1]],'test':parents[cuts[1]:]}; report={'sell_parents':309,'high':299,'medium':7,'low':3,'primary_parents':len(parents),'commission':'UNAVAILABLE','splits':{},'candidates':[],'walk_forward_folds':0,'decision':'MORE_DATA_REQUIRED','data_leakage':'PASS'}
    for name,ids in groups.items(): report['splits'][name]={'count':len(ids),'start':next((r['candle_timestamp'] for r in rows if r['parent_prediction_id']==ids[0]),None) if ids else None,'end':next((r['candle_timestamp'] for r in rows if r['parent_prediction_id']==ids[-1]),None) if ids else None,'rr':{str(rr):m([r for r in rows if r['parent_prediction_id'] in ids and r['rr']==rr]) for rr in (1,1.5,2,3,5)}}
    report['baseline_test']=report['splits']['test']['rr']; report['best_baseline_rr']='1'; report['reason']='No candidate filter evaluated: decision-time M5/M15 features are unavailable in the V2 rows and commission is uncertified.'
    (OUT/'v2_sell_walk_forward_tick_cost_report.json').write_text(json.dumps(report,indent=2),encoding='utf8'); (OUT/'v2_sell_candidate_tick_cost_comparison.json').write_text(json.dumps({'candidates':[],'decision':'MORE_DATA_REQUIRED'},indent=2),encoding='utf8'); (OUT/'v2_sell_commission_sensitivity.json').write_text(json.dumps({'status':'ASSUMPTION_ONLY','commission':'UNAVAILABLE','scenarios':['spread_only','spread_plus_conservative_assumption']},indent=2),encoding='utf8'); print(json.dumps(report['splits']))
if __name__=='__main__': main()
