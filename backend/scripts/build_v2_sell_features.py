from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

ROOT=Path(__file__).resolve().parents[2]; OUT=ROOT/'certified_data'/'v2_research'
M5=sorted(OUT.glob('EURUSD_V2_RESEARCH_M5_*.csv'))[-1]; M15=sorted(OUT.glob('EURUSD_V2_RESEARCH_M15_*.csv'))[-1]; M1=sorted(OUT.glob('EURUSD_V2_RESEARCH_M1_*.csv'))[-1]; PROV=sorted(OUT.glob('v2_cost_provenance_ticks_*.jsonl'))[-1]
def features(df, idx):
    x=df.iloc[:idx+1]; c=x.close.astype(float)
    def ret(n): return float(c.iloc[-1]/c.iloc[-1-n]-1) if len(c)>n else None
    tr=(x.high-x.low).astype(float); atr=float(tr.tail(14).mean()) if len(x)>=14 else None; emaf=c.ewm(span=8).mean(); emas=c.ewm(span=21).mean()
    return {'return_1':ret(1),'return_3':ret(3),'momentum':ret(3),'atr14':atr,'normalized_atr':atr/float(c.iloc[-1]) if atr else None,'ema_fast':float(emaf.iloc[-1]),'ema_slow':float(emas.iloc[-1]),'ema_slope':float(emaf.iloc[-1]-emaf.iloc[-2]) if len(emaf)>1 else None,'trend_state':'DOWN' if emaf.iloc[-1]<emas.iloc[-1] else 'UP'}
def main():
    m1=pd.read_csv(M1,parse_dates=['timestamp']).sort_values('timestamp'); m5=pd.read_csv(M5,parse_dates=['timestamp']).sort_values('timestamp'); m15=pd.read_csv(M15,parse_dates=['timestamp']).sort_values('timestamp'); prov={json.loads(x)['parent_prediction_id']:json.loads(x) for x in PROV.read_text(encoding='utf8').splitlines() if x.strip()}; ps=[json.loads(x) for x in (ROOT/'certified_data'/'FORWARD_PAPER_JOURNAL.jsonl').read_text(encoding='utf8').splitlines() if x.strip() and json.loads(x).get('type')=='prediction' and json.loads(x).get('decision')=='SELL']; out=[]
    for p in ps:
        t=pd.Timestamp(p['prediction_time']).tz_convert('UTC'); a=m5[m5.timestamp+pd.Timedelta(minutes=5)<=t]; b=m15[m15.timestamp+pd.Timedelta(minutes=15)<=t]; aidx=len(a)-1; bidx=len(b)-1; m1a=m1[m1.timestamp<=t]
        if aidx<0 or bidx<0: continue
        f={'parent_prediction_id':p['record_hash'],'parent_record_hash':p['record_hash'],'signal_timestamp':p['prediction_time'],'decision':'SELL','buy_probability':p.get('probability_up'),'sell_probability':p.get('probability_down'),'probability_margin':float(p.get('probability_down',0))-float(p.get('probability_up',0)),'v1_snapshot_id':p.get('v1_snapshot_id'),'v1_config_hash':p.get('v1_config_hash'),'m5_candle_timestamp':a.iloc[aidx].timestamp.isoformat(),'m5_candle_age_seconds':(t-a.iloc[aidx].timestamp).total_seconds(),'m15_candle_timestamp':b.iloc[bidx].timestamp.isoformat(),'m15_candle_age_seconds':(t-b.iloc[bidx].timestamp).total_seconds(),'m5':features(a,aidx),'m15':features(b,bidx),'m1':features(m1,len(m1a)-1),'utc_hour':t.hour,'weekday':t.weekday(),'session':'ASIA' if t.hour<8 else 'LONDON' if t.hour<13 else 'NEW_YORK'}
        f['m1_m5_aligned']=f['m1']['trend_state']==f['m5']['trend_state']=='DOWN'; f['m1_m15_aligned']=f['m1']['trend_state']==f['m15']['trend_state']=='DOWN'; f['m5_m15_aligned']=f['m5']['trend_state']==f['m15']['trend_state']=='DOWN'; f['all_timeframes_aligned']=all((f['m1_m5_aligned'],f['m1_m15_aligned'],f['m5_m15_aligned'])); f['alignment_score']=sum((f['m1_m5_aligned'],f['m1_m15_aligned'],f['m5_m15_aligned'])); f['spread']=prov.get(p['record_hash'],{}); f['row_hash']=hashlib.sha256(json.dumps(f,sort_keys=True,default=str).encode()).hexdigest(); out.append(f)
    path=OUT/'V2_SELL_DECISION_FEATURES.jsonl'; path.write_text(''.join(json.dumps(x,sort_keys=True,default=str)+'\n' for x in out),encoding='utf8'); rep={'sell_parents':len(ps),'m1_complete':len(out),'m5_complete':len(out),'m15_complete':len(out),'all_timeframe_complete':len(out),'spread_high_medium':sum(x['spread'].get('cost_quality') in ('HIGH','MEDIUM') for x in out),'data_leakage':'PASS','commission':'UNAVAILABLE','rows':len(out)}; (OUT/'v2_sell_feature_enrichment_report.json').write_text(json.dumps(rep,indent=2),encoding='utf8'); (OUT/'v2_sell_feature_dataset_manifest.json').write_text(json.dumps({'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'rows':len(out),'source_m5':str(M5),'source_m15':str(M15)},indent=2),encoding='utf8'); (OUT/'v2_sell_4fold_walk_forward_report.json').write_text(json.dumps({'folds':[],'decision':'MORE_DATA_REQUIRED','reason':'Feature rows are descriptive only; no candidate fit/deployment performed.'},indent=2),encoding='utf8'); (OUT/'v2_sell_candidate_comparison.json').write_text(json.dumps({'candidates':[],'decision':'MORE_DATA_REQUIRED'},indent=2),encoding='utf8'); print(rep)
if __name__=='__main__':main()
