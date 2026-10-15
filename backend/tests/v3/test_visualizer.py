"""Synthetic UI-only fixtures; never written to any campaign dataset."""
import copy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS
import pytest
from app.v3.visualizer import (market_status, normalize_candles, plan_geometry,
    project_features, project_zones, market_snapshot, ro, historical_display_fallback)

NOW = datetime(2026, 9, 26, 12, tzinfo=UTC)


@pytest.mark.parametrize('side,e,s,t,rr', [('BUY', 10, 9, 12, 2), ('SELL', 10, 12, 6, 2)])
def test_overlay(side, e, s, t, rr):
    p = plan_geometry(side, e, s, t)
    assert p['rr'] == rr and p['risk_distance'] > 0


@pytest.mark.parametrize('side,e,s,t', [('BUY', 10, 10, 11), ('BUY', 10, 11, 12),
    ('BUY', 10, 9, 8), ('SELL', 10, 9, 8), ('SELL', 10, 11, 12),
    ('NO_TRADE', 10, 9, 12), ('SELL', float('nan'), 11, 8), ('BUY', 0, -1, 2)])
def test_invalid_overlay(side, e, s, t):
    assert plan_geometry(side, e, s, t) is None


@pytest.mark.parametrize('seconds,connected,valid,status', [(0,True,True,'LIVE'),
    (300,True,True,'LIVE'), (301,True,True,'MARKET CLOSED'),
    (-1,True,True,'UNAVAILABLE'), (1,False,True,'UNAVAILABLE'), (1,True,False,'UNAVAILABLE')])
def test_freshness(seconds, connected, valid, status):
    assert market_status(NOW-timedelta(seconds=seconds),NOW,connected,valid) == status


def test_weekday_stale():
    now=NOW-timedelta(days=1)
    assert market_status(now-timedelta(seconds=301),now,True,True)=='STALE'


def bar(seconds):
    return dict(time=NOW.timestamp()-seconds+10800,open=10.,high=12.,low=9.,close=11.)


def test_real_candle_order_and_normalization():
    result=normalize_candles([bar(60),bar(120)],'M1',10800,NOW)
    assert [x['time'] for x in result]==[NOW.timestamp()-120,NOW.timestamp()-60]


@pytest.mark.parametrize('rows', [[bar(59)],[bar(-60)],[bar(60),bar(60)],
    [{**bar(60),'high':9}], [{**bar(60),'close':float('nan')}], [{**bar(60),'low':12}]])
def test_candle_rejection(rows):
    with pytest.raises(ValueError):normalize_candles(rows,'M1',10800,NOW)


def test_snapshot_zones_unchanged_by_later_candles():
    row={'v3_2_confluence':{'support_resistance':[
        {'level_price':10,'level_type':'recent swing support','level_created_at':'2026-09-25T10:00:00+00:00'},
        {'level_price':12,'level_type':'recent swing resistance','level_created_at':'2026-09-26T10:00:00+00:00'}]}}
    before=copy.deepcopy(row)
    zones=project_zones(row,'2026-09-25T11:00:00+00:00')
    assert len(zones)==1 and zones[0]['lower']==zones[0]['upper']==10
    normalize_candles([bar(60)],'M1',10800,NOW)
    assert zones==project_zones(row,'2026-09-25T11:00:00+00:00') and before==row


@pytest.mark.parametrize('proof,status', [({},'NOT AVAILABLE'),
    ({'reason':'NOT_AVAILABLE_AT_DECISION'},'UNCERTIFIED'),
    ({'point_in_time_safe':True},'UNCERTIFIED'),
    ({'point_in_time_safe':True,'future_derived':True},'DENIED'),
    ({'synthetic':True},'DENIED')])
def test_provenance(proof,status):
    rows=project_features({'bid':1.2},{'bid':proof},['bid'],'decision')
    assert rows[0]['status']==status and rows[0]['value']==1.2


def test_complete_provenance_and_future_acquisition():
    stamp=NOW.isoformat()
    proof=dict(point_in_time_safe=True, ingestion_timestamp=stamp, availability_timestamp=stamp, raw_source_timestamp=stamp)
    assert project_features({'bid':1.2},{'bid':proof},['bid'],stamp)[0]['status']=='CERTIFIED'
    proof['ingestion_timestamp']=(NOW+timedelta(seconds=1)).isoformat()
    assert project_features({'bid':1.2},{'bid':proof},['bid'],stamp)[0]['status']=='UNCERTIFIED'


def test_read_only_database(tmp_path):
    import sqlite3
    path=tmp_path/'test.sqlite3'
    with sqlite3.connect(path) as db:db.execute('create table evidence(id integer)')
    with ro(path) as db:
        with pytest.raises(sqlite3.OperationalError):db.execute('insert into evidence values(1)')


class FakeMT5:
    """Test adapter intentionally has no order or mutation methods."""
    def initialize(self, **kwargs):return True
    def shutdown(self):pass
    def terminal_info(self):return NS(connected=True)
    def account_info(self):return NS(server='ICMarketsSC-Demo',currency='USD',trade_mode=0)
    def symbol_info(self,symbol):return NS(select=True,digits=5,point=.00001,trade_tick_size=.00001,
        trade_tick_value_loss=1,trade_contract_size=100000,volume_min=.01,volume_max=100,
        volume_step=.01,swap_long=-8,swap_short=1,swap_mode=1)
    def symbol_info_tick(self,symbol):return NS(time=NOW.timestamp()+10800-500,
        time_msc=0,bid=1.1,ask=1.1001)
    def copy_rates_from_pos(self,symbol,timeframe,start,count):return [bar(86400)]
    def __getattr__(self,name):
        if name.startswith('TIMEFRAME_'):return name
        raise AttributeError(name)


def test_historical_market_is_not_live(monkeypatch):
    import app.v3.visualizer as v
    from app.mt5_connection import initialize_checked, DEFAULT_TERMINAL
    monkeypatch.setattr(v,'initialize_checked',lambda mt5: initialize_checked(mt5,
        processes=lambda:[{'ExecutablePath':DEFAULT_TERMINAL}],exists=lambda p:True))
    monkeypatch.setenv('V3_MT5_SERVER_UTC_OFFSET_SECONDS','10800')
    r=market_snapshot(FakeMT5(),now=NOW)
    assert r['status']=='MARKET CLOSED' and len(r['candles'])==1
    assert r['display_mode']=='HISTORICAL DISPLAY DATA' and all(r['timeframes'].values())
    assert r['costs']['commission']=='UNAVAILABLE'


def test_missing_offset_fails_closed(monkeypatch):
    import app.core.mt5_offset as offset_settings
    from app.core.config import Settings
    monkeypatch.setattr(offset_settings,'get_settings',lambda:Settings(_env_file=None))
    r=market_snapshot(FakeMT5(),now=NOW)
    assert r['status']=='UNAVAILABLE' and not r['candles']


def test_symbol_unavailable_fails_closed(monkeypatch):
    import app.v3.visualizer as v
    monkeypatch.setenv('V3_MT5_SERVER_UTC_OFFSET_SECONDS','10800')
    monkeypatch.setattr(v,'initialize_checked',lambda mt5: {'status':'PASS'})
    mt5=FakeMT5();mt5.symbol_info=lambda symbol:None
    r=market_snapshot(mt5,now=NOW)
    assert r['status']=='UNAVAILABLE' and r['error']=='SYMBOL_OR_TICK_UNAVAILABLE'


def test_historical_archive_cannot_create_live_status_or_metadata(tmp_path,monkeypatch):
    import json,hashlib
    import app.v3.visualizer as v
    monkeypatch.setattr(v,'CAMPAIGN',tmp_path)
    folder=tmp_path/'offline_visualizer';folder.mkdir()
    p=folder/'display_snapshot.json'
    saved={'market':{'symbol':'EURUSD','timeframe':'M1','checked_at':NOW.isoformat(),
        'candles':normalize_candles([bar(60)],'M1',10800,NOW),'metadata':{'tick_value_loss':1}}}
    p.write_text(json.dumps(saved));p.with_suffix('.sha256').write_text(hashlib.sha256(p.read_bytes()).hexdigest())
    current=dict(status='UNAVAILABLE',symbol='EURUSD',timeframe='M1',candles=[],metadata=None)
    r=historical_display_fallback(current)
    assert len(r['candles'])==1 and r['status']=='UNAVAILABLE' and r['metadata'] is None
    assert current['candles']==[]
    assert historical_display_fallback(dict(current,timeframe='H6'))['candles']==[]
    p.write_text('{}')
    assert historical_display_fallback(current)['candles']==[]
