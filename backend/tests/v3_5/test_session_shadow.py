import copy
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from datetime import datetime,timedelta,timezone
from pathlib import Path
from app.v3_5.session_shadow import (Candle,Reference,NewsEvent,ShadowConfig,build,checked_bars,session_boundaries,
    range_features,structure,candle_geometry,sweeps,fvgs,displacement,macro,percentile,utc)
from app.v3_5.session_shadow_store import ShadowStore,isolated_observe,canonical,ROOT,prospective_status
from app.v3_5.shadow_risk import BrokerMechanics,position_size

CFG=ShadowConfig();UTC=timezone.utc

def bar(at='2026-10-05T07:00:00Z',o=1.1,h=1.1001,l=1.0999,c=1.1,tf='M1',volume=10):
    from app.v3_5.session_shadow import DURATIONS
    start=utc(at);end=start+timedelta(seconds=DURATIONS[tf])
    return Candle(tf,start,end,end,o,h,l,c,volume,at)

def run_bars(start,count,**kw):
    start=utc(start);return [bar((start+timedelta(minutes=i)).isoformat(),**kw) for i in range(count)]

class ShadowTests(unittest.TestCase):
    def test_dst_and_mismatch_boundaries(self):
        for day,london,ny in [('2026-03-16T13:00:00Z',8,12),('2026-03-30T13:00:00Z',7,12),('2026-10-26T13:00:00Z',8,12),('2026-11-02T14:00:00Z',8,13)]:
            b=session_boundaries(utc(day),CFG)
            self.assertEqual(utc(b['LONDON']['session_open_utc']).hour,london)
            self.assertEqual(utc(b['NEW_YORK']['session_open_utc']).hour,ny)
    def test_weekend_has_no_daily_range(self):
        b=session_boundaries(utc('2026-10-04T13:00:00Z'),CFG)
        self.assertIsNone(b['ASIA']);self.assertIsNone(b['LONDON_CLOSE'])
    def test_asian_range_complete_and_partial(self):
        bars=run_bars('2026-10-05T00:00:00Z',420);t=utc('2026-10-05T07:00:00Z')
        r=range_features(bars,bars[0].open_time,t,t,1.1,(),'ASIA_RANGE_PIPS',CFG)
        self.assertTrue(r['valid']);self.assertAlmostEqual(r['range_pips'],2);self.assertEqual(r['state'],'UNKNOWN')
        self.assertFalse(range_features(bars[1:],bars[0].open_time,t,t,1.1,(),'ASIA_RANGE_PIPS',CFG)['valid'])
    def test_pre_ny_range_uses_dst(self):
        bars=run_bars('2026-03-16T11:00:00Z',60)
        r=build('2026-03-16T12:00:00Z','ny',bars)
        self.assertTrue(r['pre_ny']['valid']);self.assertEqual(r['pre_ny']['start_utc'],'2026-03-16T11:00:00+00:00')
    def test_range_distribution_and_future_reference(self):
        refs=[Reference(utc('2026-09-01T00:00:00Z')+timedelta(days=i),utc('2026-09-01T00:00:00Z')+timedelta(days=i),i,'ASIA_RANGE_PIPS') for i in range(20)]
        self.assertEqual(percentile(10,refs,utc('2026-10-05T07:00:00Z'),'ASIA_RANGE_PIPS',CFG)[0],52.5)
        with self.assertRaisesRegex(ValueError,'FUTURE_REFERENCE'):percentile(10,[replace(refs[0],available_at=utc('2026-10-06T00:00:00Z'))],utc('2026-10-05T00:00:00Z'),'ASIA_RANGE_PIPS',CFG)
    def test_structure_reuses_causal_pivots(self):
        bars=[bar((utc('2026-10-05T07:00:00Z')+timedelta(minutes=i)).isoformat(),h=h) for i,h in enumerate([1.1001,1.1002,1.1005,1.1002,1.1001])]
        self.assertEqual(structure(bars[:4],CFG)['state'],'UNKNOWN')
        st=structure(bars,CFG);self.assertEqual(st['latest_swing_high'],1.1005)
        event=next(e for e in st['events'] if e['flags']['swing_high']);self.assertEqual(event['timestamp_utc'],bars[4].close_time.isoformat())
    def test_bos_and_mss_alias(self):
        bars=[bar((utc('2026-10-05T07:00:00Z')+timedelta(minutes=i)).isoformat(),h=h) for i,h in enumerate([1.1001,1.1002,1.1005,1.1002,1.1001])]
        bars.append(bar('2026-10-05T07:05:00Z',h=1.1008,c=1.1007))
        st=structure(bars,CFG);self.assertTrue(st['BOS_UP']);self.assertEqual(st['MSS_BULLISH'],st['CHoCH']['bullish'])
    def test_late_swing_confirmation_not_backdated(self):
        bars=run_bars('2026-10-05T07:00:00Z',5)
        bars[1]=replace(bars[1],available_at=utc('2026-10-05T07:10:00Z'))
        st=structure(bars,CFG);self.assertTrue(all(utc(e['available_at'])>=utc('2026-10-05T07:10:00Z') for e in st['events']))
    def test_mss_bearish_is_canonical_choch(self):
        highs=[1.1,1.2,1.12,1.13,1.3,1.12,1.1];lows=[1.04,1.0,1.06,1.07,1.05,1.06,1.03]
        bars=[bar((utc('2026-10-05T07:00:00Z')+timedelta(minutes=i)).isoformat(),o=1.07 if i==0 else 1.08 if i==6 else 1.1,h=h,l=l,c=1.07 if i==0 else 1.04 if i==6 else 1.1) for i,(h,l) in enumerate(zip(highs,lows))]
        st=structure(bars,replace(CFG,swing_window=1));self.assertTrue(st['MSS_BEARISH']);self.assertTrue(st['BOS_DOWN'])
    def test_sweep_reclaim_is_not_signal(self):
        x=bar(h=1.1005,c=1.1);events=sweeps([x],1.1002,'ASIA_HIGH','HIGH',x.open_time)
        self.assertTrue(events[0]['close_back_inside']);self.assertAlmostEqual(events[0]['penetration_pips'],3)
        self.assertIsNone(events[0]['time_beyond_level']);self.assertNotIn('BUY',events[0])
    def test_level_not_known_at_bar_open(self):
        x=bar(h=1.1005);self.assertEqual(sweeps([x],1.1002,'ASIA_HIGH','HIGH',x.close_time),[])
    def test_sweep_episode_cannot_bridge_missing_bars(self):
        bars=[bar(h=1.1005,c=1.1003),bar('2026-10-05T07:03:00Z',h=1.1004,c=1.1)]
        events=sweeps(bars,1.1002,'ASIA_HIGH','HIGH',bars[0].open_time)
        self.assertEqual(len(events),2);self.assertEqual(events[0]['continuity'],'UNKNOWN_ACROSS_DATA_GAP')
        self.assertEqual(events[0]['observed_episode_span_seconds'],60)
    def test_asia_london_candidate_and_shadow_isolation(self):
        bars=run_bars('2026-10-05T00:00:00Z',420)+[bar(h=1.1005)]
        before=copy.deepcopy(bars);r=build('2026-10-05T07:01:00Z','observation',bars)
        self.assertTrue(r['candidates']['ASIA_LONDON_SWEEP_REVERSAL']['candidate_present']);self.assertIsNone(r['signal_output'])
        self.assertEqual(bars,before);self.assertEqual(r['macro']['NEWS_STATE'],'UNKNOWN')
    def test_ny_breakout_and_retest(self):
        bars=run_bars('2026-10-05T11:00:00Z',60)+[bar('2026-10-05T12:00:00Z',h=1.1005,c=1.1004),bar('2026-10-05T12:01:00Z',o=1.1004,h=1.1005,l=1.10005,c=1.1003)]
        r=build('2026-10-05T12:02:00Z','ny',bars)['candidates']['NY_OVERLAP_CONTINUATION']
        self.assertTrue(r['candidate_present']);self.assertTrue(r['retest_present']);self.assertEqual(r['direction'],'UP')
    def test_london_close_unknown_without_references(self):
        r=build('2026-10-05T15:45:00Z','close',[bar('2026-10-05T15:44:00Z')])
        self.assertIsNone(r['candidates']['LONDON_CLOSE_MEAN_REVERSION']['candidate_present'])
    def test_london_close_evidence_candidate_without_signal(self):
        bars=run_bars('2026-10-05T07:00:00Z',525)
        bars[-1]=replace(bars[-1],high=1.103,close=1.101)
        refs=[Reference(utc('2026-09-01T00:00:00Z')+timedelta(days=i),utc('2026-09-01T00:00:00Z')+timedelta(days=i),i/10,'LONDON_EXTENSION_PIPS') for i in range(20)]
        r=build('2026-10-05T15:45:00Z','close_fixture',bars,references=refs)
        self.assertTrue(r['candidates']['LONDON_CLOSE_MEAN_REVERSION']['candidate_present']);self.assertIsNone(r['signal_output'])
    def test_fvg_and_causal_fill(self):
        bars=[bar('2026-10-05T07:00:00Z',o=1.0,h=1.1,l=.9,c=1.0),bar('2026-10-05T07:01:00Z',o=1.0,h=1.15,l=.99,c=1.14),bar('2026-10-05T07:02:00Z',o=1.13,h=1.18,l=1.12,c=1.16)]
        r=fvgs(bars,1.16)[0];self.assertTrue(r['bullish_fvg']);self.assertEqual(r['fill_percentage'],0)
        bars.append(bar('2026-10-05T07:03:00Z',o=1.16,h=1.17,l=1.09,c=1.11));self.assertTrue(fvgs(bars,1.11)[0]['fully_filled'])
    def test_candle_geometry_and_patterns(self):
        x=bar(o=1.1,h=1.1002,l=1.0994,c=1.1002);r=candle_geometry([x],CFG)
        self.assertAlmostEqual(r['lower_wick'],.0006);self.assertIn('hammer',r['patterns'])
        engulf=[bar(o=1.1002,c=1.1,h=1.1003,l=1.0999),bar('2026-10-05T07:01:00Z',o=1.0999,c=1.1003,h=1.1004,l=1.0998)]
        self.assertIn('bullish_engulfing',candle_geometry(engulf,CFG)['patterns'])
    def test_displacement_distribution_and_unknown(self):
        self.assertEqual(displacement([bar()],CFG)['state'],'UNKNOWN')
        bars=run_bars('2026-10-05T07:00:00Z',40)
        bars.append(bar('2026-10-05T07:40:00Z',h=1.102,c=1.1019))
        self.assertEqual(displacement(bars,CFG)['state'],'BULLISH_DISPLACEMENT')
    def test_future_bar_and_late_data_rejected(self):
        x=bar()
        for b in [x,replace(x,close_time=utc('2026-10-05T06:59:00Z'),available_at=utc('2026-10-05T07:02:00Z'))]:
            with self.assertRaisesRegex(ValueError,'FUTURE_OR_LATE_BAR'):build('2026-10-05T07:00:00Z','future',[b])
    def test_duplicate_bar_rejected(self):
        x=bar()
        with self.assertRaisesRegex(ValueError,'DUPLICATE_BAR'):checked_bars([x,x],x.close_time)
    def test_future_tick_and_distribution_rejected(self):
        with self.assertRaisesRegex(ValueError,'FUTURE_OR_INVALID_TICK'):build('2026-10-05T07:00:00Z','future',[],bid=1.1,ask=1.1001,tick_time=utc('2026-10-05T07:01:00Z'),tick_available_at=utc('2026-10-05T07:01:00Z'))
        from app.v3_5.market_context import Sample
        r=Sample('EURUSD',utc('2026-10-05T08:00:00Z'),utc('2026-10-05T08:00:00Z'),.0001,'LONDON_ONLY')
        with self.assertRaisesRegex(ValueError,'FUTURE_CONTEXT_REFERENCE'):build('2026-10-05T07:00:00Z','future',[],spread_history=[r])
    def test_htf_alignment_and_unknown(self):
        t=utc('2026-10-05T08:00:00Z');b=bar('2026-10-05T07:00:00Z',tf='H1')
        r=build(t,'htf',[b]);self.assertEqual(r['HTF']['H1']['latest_completed'],t.isoformat());self.assertEqual(r['HTF']['D1']['trend'],'UNKNOWN')
        with self.assertRaisesRegex(ValueError,'FUTURE_OR_LATE_BAR'):build(t-timedelta(seconds=1),'htf',[b])
    def test_macro_unavailable_and_future_actual(self):
        t=utc('2026-10-05T07:00:00Z');self.assertFalse(macro([],t)['MACRO_DATA_AVAILABLE'])
        e=NewsEvent(t+timedelta(hours=1),t,'USD','fixture','HIGH','TEST_ONLY',forecast=1)
        self.assertTrue(macro([e],t)['events'][0]['USD_RELEVANT'])
        with self.assertRaisesRegex(ValueError,'FUTURE_MACRO_ACTUAL'):macro([replace(e,actual=2,actual_available_at=t+timedelta(hours=1))],t)
    def test_missing_spread_volatility_unknown(self):
        r=build('2026-10-05T07:01:00Z','empty',[bar()]);self.assertEqual(r['execution_context']['spread_state'],'UNKNOWN');self.assertEqual(r['volatility']['volatility_state'],'UNKNOWN')
    def test_qualified_historical_spread_and_volatility(self):
        from app.v3_5.market_context import Sample
        t=utc('2026-10-05T10:00:00Z')
        samples=[Sample('EURUSD',t-timedelta(minutes=i+1),t-timedelta(minutes=i+1),.00001,'LONDON_ONLY') for i in range(20)]
        vol=[replace(x,value=1e-9) for x in samples]
        bars=run_bars('2026-10-05T09:30:00Z',30)
        bars=[replace(b,close=1.1001 if i%2 else 1.1) for i,b in enumerate(bars)]
        r=build(t,'references',bars,bid=1.1,ask=1.1002,tick_time=t,tick_available_at=t,spread_history=samples,volatility_history=vol)
        self.assertEqual(r['execution_context']['spread_state'],'EXTREME');self.assertEqual(r['volatility']['volatility_state'],'EXTREME')
    def test_store_append_only_idempotent_and_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'shadow.sqlite3';store=ShadowStore(p,fixture=True);r=build('2026-10-05T07:01:00Z','id',[bar()])
            self.assertEqual(store.append(r,'FIXTURE')['status'],'RECORDED');self.assertEqual(store.append(r,'FIXTURE')['status'],'ALREADY_RECORDED')
            with closing(sqlite3.connect(p)) as d:
                self.assertEqual(d.execute('select count(*) from shadow_records').fetchone()[0],1)
                for sql in ('update shadow_records set version="bad"','delete from shadow_records'):
                    with self.assertRaisesRegex(sqlite3.IntegrityError,'APPEND_ONLY'):d.execute(sql)
            modified=dict(r,session='DIFFERENT')
            with self.assertRaisesRegex(ValueError,'SHADOW_IDENTITY_CONFLICT'):store.append(modified,'FIXTURE')
    def test_failure_isolation_and_protected_path(self):
        class Broken:
            def append(self,*a,**k):raise OSError('unavailable')
        before={'unchanged_decision':'NONE'};frozen=copy.deepcopy(before)
        self.assertEqual(isolated_observe(lambda:before,Broken())['status'],'SHADOW_FAILED');self.assertEqual(before,frozen)
        with self.assertRaisesRegex(ValueError,'FIXTURE_CANNOT_TARGET_PROJECT'):ShadowStore(ROOT/'runtime/forbidden.sqlite3',fixture=True)
    def test_prospective_checkpoints_exclude_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'shadow.sqlite3';store=ShadowStore(path,fixture=True)
            record=build('2026-10-05T07:01:00Z','fixture_prospective',[bar()])
            store.append(record,'REPLAY');self.assertEqual(prospective_status(path)['prospective_eligible_observations'],0)
            store.append(dict(record,observation_id='fixture_prospective_2'),'PROSPECTIVE')
            status=prospective_status(path);self.assertEqual(status['unique_days'],1);self.assertEqual(status['checkpoints']['50']['remaining'],49);self.assertFalse(status['edge_validated'])
    def test_risk_calculator_broker_values_and_limits(self):
        b=BrokerMechanics(.00001,1,.01,100,.01,source='TEST_ONLY',account_currency='USD')
        r=position_size(10000,1,1.1,1.099,b)
        self.assertAlmostEqual(r['raw_lot_size'],1);self.assertTrue(r['risk_budget_respected']);self.assertFalse(r['execution_enabled'])
        self.assertEqual(position_size(1,.1,1.1,1.099,b)['broker_normalized_lot_size'],0)
        self.assertEqual(position_size(10000,1,1.1,1.099,replace(b,point=.00001,stops_level=200))['broker_normalized_lot_size'],0)
        with self.assertRaises(ValueError):position_size(10000,1,1.1,1.1,b)

if __name__=='__main__':unittest.main()
