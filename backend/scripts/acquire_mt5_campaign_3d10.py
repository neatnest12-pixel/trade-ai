"""Immutable, read-only MT5 acquisition for campaign 3D.10."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import MetaTrader5 as mt5
import numpy as np
import pandas as pd

OUT=Path('campaign_v2_03d/mt5_canonical')
TERM=r'C:\Program Files\MetaTrader 5\terminal64.exe'
TFS={'M1':mt5.TIMEFRAME_M1,'M5':mt5.TIMEFRAME_M5,'M15':mt5.TIMEFRAME_M15,'H1':mt5.TIMEFRAME_H1,'H4':mt5.TIMEFRAME_H4}
MIN={'M1':1,'M5':5,'M15':15,'H1':60,'H4':240}
def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1<<20),b''): h.update(b)
    return h.hexdigest()
def pages(sym,tf,n=50000):
    a=[]
    for pos in range(1,1000001,n):
        x=mt5.copy_rates_from_pos(sym,tf,pos,n)
        if x is None or len(x)==0: break
        a.append(x)
        if len(x)<n: break
    return np.concatenate(a) if a else np.empty(0)
def main():
    if os.getenv('MT5_READ_ONLY')!='true' or os.getenv('TRADING_EXECUTION_ENABLED')!='false': raise RuntimeError('read-only guard failed')
    if not mt5.initialize(path=TERM): raise RuntimeError(mt5.last_error())
    try:
        ti,ai,ver=mt5.terminal_info(),mt5.account_info(),mt5.version()
        syms=[s for s in (mt5.symbols_get() or []) if 'EURUSD' in s.name.upper().replace('/','').replace('.','').replace('-','')]
        syms.sort(key=lambda s:(not s.visible,len(s.name),s.name)); s=syms[0].name; spec=mt5.symbol_info(s)
        OUT.mkdir(parents=True,exist_ok=True)
        retrieved=datetime.now(UTC); manifest={'campaign':'V2 #3D.10','read_only':True,'terminal_path':ti.path,'terminal_version':ver,'broker':ai.company,'server':ai.server,'account_mode':'DEMO' if ai.trade_mode==mt5.ACCOUNT_TRADE_MODE_DEMO else 'LIVE','account_currency':ai.currency,'symbol':s,'symbol_specification':{k:getattr(spec,k) for k in ('description','currency_base','currency_profit','digits','point','trade_contract_size','volume_min','volume_step','spread_float')},'retrieval_timestamp':retrieved.isoformat(),'datasets':{}}
        for label,tf in TFS.items():
            r=pages(s,tf); raw=OUT/f'EURUSD_MT5_{label}_RAW_V1.csv'; norm=OUT/f'EURUSD_MT5_{label}_V1.csv'
            if not len(r): manifest['datasets'][label]={'status':'UNAVAILABLE','error':str(mt5.last_error())}; continue
            df=pd.DataFrame(r); df['timestamp']=pd.to_datetime(df.time,unit='s',utc=True); df=df.drop(columns=['time']); df=df[df.timestamp+pd.Timedelta(minutes=MIN[label])<=retrieved]; df=df.sort_values('timestamp').drop_duplicates('timestamp',keep='first'); df.to_csv(raw,index=False)
            df.to_csv(norm,index=False)
            manifest['datasets'][label]={'raw_path':str(raw),'normalized_path':str(norm),'raw_sha256':sha(raw),'normalized_sha256':sha(norm),'rows':len(df),'start':df.timestamp.min().isoformat(),'end':df.timestamp.max().isoformat()}
        (OUT/'MT5_DATASET_MANIFEST.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
        print(json.dumps(manifest,indent=2))
    finally: mt5.shutdown()
if __name__=='__main__': main()
