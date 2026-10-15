from datetime import UTC, datetime

import numpy as np

from app.v1_1.mt5_adapter import MT5Adapter


class Mock:
    TIMEFRAME_M1=1; TIMEFRAME_M5=5; TIMEFRAME_M15=15
    def __init__(self, tick): self.tick=tick; self.calls=[]
    def initialize(self): return True
    def account_info(self): return type('A',(),{'company':'Raw Trading Ltd','server':'ICMarketsSC-Demo'})()
    def symbol_select(self,symbol,flag): return True
    def symbol_info_tick(self,symbol): return type('T',(),{'time':self.tick})()
    def copy_rates_from_pos(self,symbol,tf,pos,count):
        self.calls.append(tf)
        return np.array([(self.tick-3600,1.,2.,.5,1.5,10,2,3)],dtype=[('time','i8'),('open','f8'),('high','f8'),('low','f8'),('close','f8'),('tick_volume','i8'),('spread','i8'),('real_volume','i8')])

def test_active_utc_and_frames():
    now=datetime(2026,1,1,12,tzinfo=UTC); m=Mock(int(now.timestamp())); a=MT5Adapter(m,now=lambda:now)
    assert a.connect()=='CONNECTED'; assert a.latest_completed_candle('M1').complete
    assert a.recent_completed_candles('M5',2)[0].timeframe=='M5'; assert a.latest_completed_candle('M15')

def test_offset_and_guards():
    now=datetime(2026,1,1,12,tzinfo=UTC)
    assert MT5Adapter(Mock(int(now.timestamp()+3*3600)),now=lambda:now).connect()=='CONNECTED'
    assert MT5Adapter(Mock(int(now.timestamp()+20*3600)),now=lambda:now).connect()=='IMPLAUSIBLE_SERVER_OFFSET'

def test_current_candle_excluded_and_no_data():
    now=datetime(2026,1,1,12,tzinfo=UTC); m=Mock(int(now.timestamp())); a=MT5Adapter(m,now=lambda:now); a.connect(); assert a.latest_completed_candle() is not None



def test_future_candle_status_fails_closed():
    now=datetime(2026,1,1,12,tzinfo=UTC); m=Mock(int(now.timestamp()+3*3600)); a=MT5Adapter(m,now=lambda:now)
    m.copy_rates_from_pos = lambda symbol, tf, pos, count: __import__('numpy').array([(int(now.timestamp()+3*3600+60),1.,2.,.5,1.5,10,2,3)], dtype=[('time','i8'),('open','f8'),('high','f8'),('low','f8'),('close','f8'),('tick_volume','i8'),('spread','i8'),('real_volume','i8')])
    assert a.connect()=='CONNECTED'; assert a.latest_completed_candle() is None; assert a.status=='FUTURE_CANDLE'


