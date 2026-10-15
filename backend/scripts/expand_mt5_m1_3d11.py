import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import MetaTrader5 as mt5
import numpy as np
import pandas as pd

OUT=Path('campaign_v2_03d/mt5_canonical'); TERM=r'C:\Program Files\MetaTrader 5\terminal64.exe'
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1<<20),b''): h.update(b)
 return h.hexdigest()
if os.getenv('MT5_READ_ONLY')!='true' or os.getenv('TRADING_EXECUTION_ENABLED')!='false': raise RuntimeError('guard')
if not mt5.initialize(path=TERM): raise RuntimeError(mt5.last_error())
try:
 start=datetime(2022,1,1,tzinfo=UTC); end=datetime.now(UTC); chunks=[]; cur=start
 while cur<end:
  nxt=min(cur+timedelta(days=90),end)
  x=mt5.copy_rates_range('EURUSD',mt5.TIMEFRAME_M1,cur,nxt)
  if x is not None and len(x): chunks.append(x)
  cur=nxt+timedelta(minutes=1)
 if not chunks: raise RuntimeError(mt5.last_error())
 a=np.concatenate(chunks); df=pd.DataFrame(a); df['timestamp']=pd.to_datetime(df.time,unit='s',utc=True); df=df.drop(columns=['time']).sort_values('timestamp').drop_duplicates('timestamp')
 now=pd.Timestamp.now(tz='UTC'); df=df[df.timestamp+pd.Timedelta(minutes=1)<=now]
 p=OUT/'EURUSD_MT5_M1_V2.csv'; df.to_csv(p,index=False)
 out={'rows':len(df),'start':df.timestamp.min().isoformat(),'end':df.timestamp.max().isoformat(),'sha256':sha(p),'chunks':len(chunks),'retrieved':now.isoformat()}
 (OUT/'MT5_M1_V2_ACQUISITION.json').write_text(json.dumps(out,indent=2)); print(json.dumps(out,indent=2))
finally: mt5.shutdown()
