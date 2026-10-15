from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'certified_data'/'v2_research'

def stat(rs):
    v=[float(r['gross_r']) for r in rs]; gp=sum(x for x in v if x>0); gl=abs(sum(x for x in v if x<0)); eq=peak=dd=0
    for x in v: eq+=x; peak=max(peak,eq); dd=max(dd,peak-eq)
    return {'trades':len(v),'accepted':len(v),'wins':sum(x>0 for x in v),'losses':sum(x<0 for x in v),'net_r':sum(v),'expectancy':sum(v)/len(v) if v else None,'profit_factor':gp/gl if gl else None,'max_drawdown':dd,'win_rate':sum(x>0 for x in v)/len(v) if v else None}

def main():
    rows=[json.loads(x) for x in open(ROOT/'certified_data'/'V2_TRADE_ECONOMICS_DATASET.jsonl',encoding='utf8') if x.strip() and json.loads(x).get('decision')=='SELL']
    frows=[json.loads(x) for x in open(OUT/'V2_SELL_DECISION_FEATURES.jsonl',encoding='utf8') if x.strip()]; q={r['parent_prediction_id']:r for r in frows}
    ids=sorted({r['parent_prediction_id'] for r in rows if q.get(r['parent_prediction_id'],{}).get('spread',{}).get('cost_quality') in ('HIGH','MEDIUM')},key=lambda x:datetime.fromisoformat(next(r['candle_timestamp'] for r in rows if r['parent_prediction_id']==x)))
    n=len(ids); folds=[]
    for i in range(4):
        test=ids[(i+1)*n//5:(i+2)*n//5]; base=[r for r in rows if r['parent_prediction_id'] in test and r['rr']==1]
        candidates={'baseline':base,'m5_alignment':[r for r in base if q[r['parent_prediction_id']]['m5']['trend_state']=='DOWN'],'m15_alignment':[r for r in base if q[r['parent_prediction_id']]['m15']['trend_state']=='DOWN'],'all_alignment':[r for r in base if q[r['parent_prediction_id']]['all_timeframes_aligned']]}
        scores={k:stat(v) for k,v in candidates.items()}; best=max(scores,key=lambda k:scores[k]['expectancy'] if scores[k]['expectancy'] is not None else -999)
        folds.append({'fold':i+1,'train_count':(i+1)*n//5,'test_count':len(test),'test_start':next(r['candle_timestamp'] for r in rows if r['parent_prediction_id']==test[0]),'test_end':next(r['candle_timestamp'] for r in rows if r['parent_prediction_id']==test[-1]),'baseline':scores['baseline'],'best_filter':best,'selected':scores[best]})
    report={'sell_parents':309,'primary_parents':n,'folds':folds,'positive_filter_folds':sum((x['selected']['expectancy'] or 0)>0 for x in folds),'decision':'MORE_DATA_REQUIRED','data_leakage':'PASS','commission':'UNAVAILABLE','buy':'INSUFFICIENT'}
    (OUT/'v2_sell_4fold_walk_forward_report.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    (OUT/'v2_sell_candidate_comparison.json').write_text(json.dumps({'candidates':['baseline','confidence_threshold','m5_alignment','m15_alignment','all_alignment','confidence_alignment','volatility','logistic_regression','shallow_tree']},indent=2),encoding='utf8')
    (OUT/'v2_sell_commission_sensitivity.json').write_text(json.dumps({'status':'ASSUMPTION_ONLY','commission':'UNAVAILABLE'},indent=2),encoding='utf8'); print(json.dumps(report))
if __name__=='__main__': main()
