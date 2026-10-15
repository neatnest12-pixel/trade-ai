import hashlib
import json
from pathlib import Path

import pandas as pd

P=Path('campaign_v2_03d/mt5_canonical'); src=P/'EURUSD_MT5_M1_V2.csv'
df=pd.read_csv(src); df.timestamp=pd.to_datetime(df.timestamp,utc=True); df=df.sort_values('timestamp').drop_duplicates('timestamp')
out={'source_rows':len(df),'source_start':df.timestamp.min().isoformat(),'source_end':df.timestamp.max().isoformat(),'source_duplicates':0,'timeframes':{}}
for label,n in [('M5',5),('M15',15),('H1',60),('H4',240)]:
 x=df.set_index('timestamp'); base=x[['open','high','low','close','tick_volume','real_volume']]
 groups=base.resample(f'{n}min',origin='epoch',label='left',closed='left')
 rows=[]; missing=0; discarded=0
 for ts,g in groups:
  if len(g)==0: continue
  expected=pd.date_range(ts,ts+pd.Timedelta(minutes=n-1),freq='min',tz='UTC')
  if len(g)!=n or not g.index.equals(expected):
   missing+=1; discarded+=1; continue
  rows.append({'timestamp':ts,'open':g.open.iloc[0],'high':g.high.max(),'low':g.low.min(),'close':g.close.iloc[-1],'tick_volume':g.tick_volume.sum(),'real_volume':g.real_volume.sum()})
 y=pd.DataFrame(rows); path=P/f'EURUSD_MT5_{label}_DERIVED_V1.csv'; y.to_csv(path,index=False)
 h=hashlib.sha256(path.read_bytes()).hexdigest(); out['timeframes'][label]={'rows':len(y),'start':y.timestamp.min().isoformat() if len(y) else None,'end':y.timestamp.max().isoformat() if len(y) else None,'missing_source_groups':missing,'discarded_incomplete_groups':discarded,'sha256':h}
(P/'MT5_DERIVED_TIMEFRAME_MANIFEST.json').write_text(json.dumps(out,indent=2),encoding='utf-8'); print(json.dumps(out,indent=2))
