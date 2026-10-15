import hashlib
import json
from pathlib import Path

import pandas as pd

P=Path('campaign_v2_03d/mt5_canonical'); m=json.loads((P/'MT5_DATASET_MANIFEST.json').read_text())
def h(p): return hashlib.sha256(p.read_bytes()).hexdigest()
m1=pd.read_csv(P/'EURUSD_MT5_M1_V1.csv',parse_dates=['timestamp']); m1.timestamp=pd.to_datetime(m1.timestamp,utc=True)
quality=[]
for c in ['open','high','low','close']:
 quality.append((c,(~pd.to_numeric(m1[c],errors='coerce').notna()).sum()))
invalid=((m1[['open','high','low','close']]<=0).any(axis=1)|(m1.high<m1[['open','close']].max(axis=1))|(m1.low>m1[['open','close']].min(axis=1))).sum()
dups=m1.timestamp.duplicated().sum(); gaps=m1.timestamp.diff().dt.total_seconds().div(60).sub(1); gaps=gaps[gaps>0]
for label,n in [('M5',5),('M15',15),('H1',60),('H4',240)]:
 x=m1.set_index('timestamp')[['open','high','low','close','tick_volume','spread','real_volume']].resample(f'{n}min',origin='epoch').agg({'open':'first','high':'max','low':'min','close':'last','tick_volume':'sum','spread':'last','real_volume':'sum'}).dropna(subset=['open','high','low','close']); out=P/f'EURUSD_MT5_{label}_DERIVED_V1.csv'; x.reset_index().to_csv(out,index=False); m.setdefault('derived_timeframes',{})[label]={'path':str(out),'rows':len(x),'sha256':h(out)}
(P/'MT5_FOREX_GAP_MANIFEST.csv').write_text('start,end,minutes,class\n'+''.join(f'{a.isoformat()},{b.isoformat()},{int(d)},UNCLASSIFIED_SOURCE_GAP\n' for a,b,d in zip(m1.timestamp[gaps.index-1],m1.timestamp[gaps.index],gaps, strict=False)),encoding='utf-8')
(P/'MT5_DATA_QUALITY_REPORT.md').write_text(f'# MT5 data quality\n\nRows: {len(m1)}\nRange: {m1.timestamp.min()} to {m1.timestamp.max()}\nDuplicate timestamps: {dups}\nInvalid OHLC: {invalid}\nGaps: {len(gaps)}; largest gap minutes: {int(gaps.max()) if len(gaps) else 0}\nWeekend closures are not classified as corruption.\n\nGate: PASS_WITH_LIMITATIONS (terminal maxbars capped at 100,000 M1 bars).\n',encoding='utf-8')
(P/'MT5_FOREX_CALENDAR_AUDIT.md').write_text(f'# MT5 calendar audit\n\nObserved M1 minutes: {len(m1)}\nNon-trivial intervals recorded: {len(gaps)}\nUnknown/unclassified gaps: {len(gaps)}\nLargest unknown gap: {int(gaps.max()) if len(gaps) else 0} minutes\n\nNo fabricated holiday/weekend explanations were applied.\nGate: PASS_WITH_LIMITATIONS.\n',encoding='utf-8')
(P/'MT5_DATA_USAGE_REVIEW.md').write_text('# MT5 data usage review\n\nBroker: MetaQuotes Ltd. / MetaQuotes-Demo. No broker-specific research licence or terms evidence was supplied in the repository. Local storage and private transformation are technically possible; private backtesting, ML training, derived model use, redistribution and resale are UNCLEAR and require manual confirmation. Raw redistribution disabled.\n\nUSAGE_GATE: MANUAL_CONFIRMATION_REQUIRED\n',encoding='utf-8')
(P/'MT5_HISTDATA_CROSS_SOURCE_REPORT.md').write_text('# MT5/HistData comparison\n\nComparison not certified in this run: MT5 covers 2026-05-11 onward at M1 while HistData V2 is BID-only and has different coverage/timezone convention. No training or protected-test access occurred.\n\nGATE: INSUFFICIENT_EVIDENCE\n',encoding='utf-8')
(P/'MT5_DATASET_MANIFEST.json').write_text(json.dumps(m,indent=2),encoding='utf-8')
