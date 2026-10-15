import unittest
import json
import sqlite3
import tempfile
from pathlib import Path
from datetime import datetime, timedelta, time, date
from dataclasses import replace
from contextlib import closing
from app.v3_5.market_context import context, utc, Config, Sample, Bar, INSTRUMENTS, status_view
from app.v3_5.session_analysis import read_outcomes, analyze_rows


class MarketContextTests(unittest.TestCase):
    def ctx(self,t,**kwargs):return context(t,"EURUSD",**kwargs)

    def test_utc_conversion(self):
        f=self.ctx("2026-10-01T14:30:00+05:30")
        self.assertEqual(f["hour_utc"],9);self.assertEqual(f["source_timestamp"],"2026-10-01T14:30:00+05:30")
    def test_naive_rejected(self):
        with self.assertRaises(ValueError):self.ctx("2026-10-01T12:00:00")
    def test_asia(self):self.assertEqual(self.ctx("2026-01-05T02:00:00Z")["current_session"],"ASIA_ONLY")
    def test_london_only(self):self.assertEqual(self.ctx("2026-01-05T10:00:00Z")["current_session"],"LONDON_ONLY")
    def test_overlap(self):self.assertTrue(self.ctx("2026-01-05T14:00:00Z")["is_london_ny_overlap"])
    def test_ny_only(self):self.assertEqual(self.ctx("2026-01-05T19:00:00Z")["current_session"],"NEW_YORK_ONLY")
    def test_asia_london(self):self.assertEqual(self.ctx("2026-07-06T07:30:00Z")["current_session"],"ASIA_LONDON_TRANSITION")
    def test_rollover(self):self.assertTrue(self.ctx("2026-01-05T22:00:00Z")["is_rollover_window"])
    def test_london_dst(self):
        self.assertFalse(self.ctx("2026-03-27T07:30:00Z")["session_london"])
        self.assertTrue(self.ctx("2026-03-30T07:30:00Z")["session_london"])
        self.assertTrue(self.ctx("2026-10-23T07:30:00Z")["session_london"])
        self.assertFalse(self.ctx("2026-10-26T07:30:00Z")["session_london"])
    def test_ny_dst(self):
        self.assertFalse(self.ctx("2026-03-06T12:30:00Z")["session_new_york"])
        self.assertTrue(self.ctx("2026-03-09T12:30:00Z")["session_new_york"])
        self.assertTrue(self.ctx("2026-10-30T12:30:00Z")["session_new_york"])
        self.assertFalse(self.ctx("2026-11-02T12:30:00Z")["session_new_york"])
    def test_mismatched_dst_weeks(self):
        self.assertTrue(self.ctx("2026-03-16T12:30:00Z")["is_london_ny_overlap"])
        self.assertTrue(self.ctx("2026-10-26T12:30:00Z")["is_london_ny_overlap"])
    def test_dst_exact_sundays(self):
        for t in ["2026-03-08T06:59:59Z","2026-03-08T07:00:00Z","2026-03-29T00:59:59Z","2026-03-29T01:00:00Z","2026-10-25T01:00:00Z","2026-11-01T06:00:00Z"]:
            self.assertEqual(self.ctx(t),self.ctx(t))
    def test_utc_boundary_year(self):
        f=self.ctx("2027-01-01T00:00:00Z")
        self.assertEqual((f["day_of_week"],f["quarter"],f["iso_week_year"]),(4,1,2026))
    def test_cyclical(self):
        f=self.ctx("2026-01-05T06:00:00Z")
        self.assertAlmostEqual(f["hour_sin"],1);self.assertAlmostEqual(f["hour_cos"],0)
    def test_weekend_calendar(self):
        s=replace(INSTRUMENTS["EURUSD"],verified=True,evidence="TEST_ONLY")
        self.assertFalse(self.ctx("2026-01-09T22:00:00Z",schedule=s)["is_market_open"])
        self.assertFalse(self.ctx("2026-01-10T12:00:00Z",schedule=s)["is_market_open"])
        self.assertTrue(self.ctx("2026-01-11T22:00:00Z",schedule=s)["is_market_open"])
        self.assertTrue(self.ctx("2026-01-12T00:00:00Z",schedule=s)["is_monday"])
    def test_instrument_specific_break(self):
        t="2026-01-05T22:30:00Z"
        fx=replace(INSTRUMENTS["EURUSD"],verified=True,evidence="TEST")
        metal=replace(INSTRUMENTS["XAUUSD"],verified=True,evidence="TEST")
        self.assertTrue(context(t,"EURUSD",schedule=fx)["is_market_open"])
        self.assertFalse(context(t,"XAUUSD",schedule=metal)["is_market_open"])
        self.assertEqual(context(t,"XAGUSD")["asset_class"],"PRECIOUS_METAL")
    def test_unverified_calendar_unknown(self):self.assertIsNone(self.ctx("2026-01-05T12:00:00Z")["is_market_open"])
    def test_calendar_evidence_required(self):
        with self.assertRaises(ValueError):replace(INSTRUMENTS["EURUSD"],verified=True)
    def test_holiday(self):
        s=replace(INSTRUMENTS["EURUSD"],verified=True,evidence="TEST",closed_dates=frozenset({date(2026,1,5)}))
        self.assertEqual(self.ctx("2026-01-05T12:00:00Z",schedule=s)["current_session"],"CLOSED")
    def test_transitions_windows(self):
        f=self.ctx("2026-01-05T13:05:00Z")
        self.assertTrue(f["is_new_york_open_window"]);self.assertEqual(f["minutes_since_session_open"],5)
        self.assertEqual(f["previous_session"],"LONDON_ONLY");self.assertEqual(f["next_session"],"NEW_YORK_ONLY")
    def test_configurable_windows(self):
        self.assertFalse(self.ctx("2026-01-05T13:05:00Z",config=Config(open_window_minutes=2))["is_new_york_open_window"])
    def test_spread_evidence(self):
        t=utc("2026-01-05T14:00:00Z");session=self.ctx(t)["current_session"]
        hist=[Sample("EURUSD",t-timedelta(minutes=i+1),t-timedelta(minutes=i+1),.0001,session) for i in range(20)]
        f=self.ctx(t,bid=1,ask=1.001,quote_time=t,quote_available_at=t,spread_history=hist)
        self.assertEqual(f["spread_state"],"EXTREME");self.assertAlmostEqual(f["spread_vs_session_median"],10)
        self.assertEqual(self.ctx(t,bid=1,ask=1.001,quote_time=t,quote_available_at=t)["spread_state"],"UNKNOWN")
    def test_spread_no_lookahead(self):
        t=utc("2026-01-05T14:00:00Z");s=self.ctx(t)["current_session"]
        future=[Sample("EURUSD",t+timedelta(seconds=1),t,.0001,s)]*30
        delayed=[Sample("EURUSD",t-timedelta(seconds=1),t+timedelta(seconds=1),.0001,s)]*30
        self.assertEqual(self.ctx(t,spread_history=future+delayed)["spread_reference_samples"],0)
        self.assertIsNone(self.ctx(t,bid=1,ask=2,quote_time=t+timedelta(seconds=1),quote_available_at=t)["current_spread"])
    def test_instrument_reference_isolation(self):
        t=utc("2026-01-05T14:00:00Z");s=self.ctx(t)["current_session"]
        hist=[Sample("XAUUSD",t-timedelta(minutes=1),t,1,s)]*30
        self.assertEqual(self.ctx(t,spread_history=hist)["spread_reference_samples"],0)
    def test_stale_quote(self):
        t=utc("2026-01-05T14:00:00Z")
        self.assertIsNone(self.ctx(t,bid=1,ask=1.001,quote_time=t-timedelta(seconds=301),quote_available_at=t)["current_spread"])
    def test_equal_reference_not_extreme(self):
        t=utc("2026-01-05T14:00:00Z");s=self.ctx(t)["current_session"];v=1.001-1
        refs=[Sample("EURUSD",t-timedelta(minutes=i+1),t,v,s) for i in range(20)]
        self.assertEqual(self.ctx(t,bid=1,ask=1.001,quote_time=t,quote_available_at=t,spread_history=refs)["spread_state"],"NORMAL")
    def test_invalid_configuration(self):
        with self.assertRaises(ValueError):Config(spread_percentiles=(99,90))
        with self.assertRaises(ValueError):Config(open_window_minutes=0)
    def test_missing_bar_cadence_unknown(self):
        t=utc("2026-01-05T14:00:00Z")
        bars=[Bar("EURUSD",t-timedelta(minutes=60-i*2),t,1.2,.9,1+.001*i) for i in range(25)]
        f=self.ctx(t,bars=bars)
        self.assertIsNone(f["atr"]);self.assertIsNone(f["short_term_realized_volatility"])
    def test_atr_volatility_and_leakage(self):
        t=utc("2026-01-05T14:00:00Z");s=self.ctx(t)["current_session"]
        bars=[Bar("EURUSD",t-timedelta(minutes=30-i),t-timedelta(minutes=30-i),1.2+i*.001,.9+i*.001,1+i*.001) for i in range(25)]
        hist=[Sample("EURUSD",t-timedelta(minutes=i+1),t,.00001,s) for i in range(20)]
        f=self.ctx(t,bars=bars,volatility_history=hist)
        self.assertAlmostEqual(f["atr"],.3);self.assertIsNotNone(f["short_term_realized_volatility"])
        future=Bar("EURUSD",t+timedelta(minutes=1),t,9,.1,8)
        delayed=Bar("EURUSD",t-timedelta(minutes=1),t+timedelta(minutes=1),9,.1,8)
        self.assertEqual(f,self.ctx(t,bars=bars+[future,delayed],volatility_history=hist))
    def test_volatility_states(self):
        t=utc("2026-01-05T14:00:00Z");session=self.ctx(t)["current_session"]
        bars=[Bar("EURUSD",t-timedelta(minutes=30-i),t,1.2,.9,1+.001*(i%2)) for i in range(25)]
        rv=self.ctx(t,bars=bars)["short_term_realized_volatility"]
        for count,expected in [(0,"LOW"),(10,"NORMAL"),(16,"HIGH"),(20,"EXTREME")]:
            refs=[Sample("EURUSD",t-timedelta(minutes=i+1),t,rv/2 if i<count else rv*2,session) for i in range(20)]
            self.assertEqual(self.ctx(t,bars=bars,volatility_history=refs)["volatility_state"],expected)
    def test_status_view(self):self.assertEqual(status_view(self.ctx("2026-01-05T14:00:00Z"))["instrument"],"EURUSD")
    def test_historical_outcome_immutability(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"results.sqlite3"
            row=dict(status="WIN",gross_r=1,decision_time_utc="2026-01-05T14:00:00Z",symbol="EURUSD",direction="BUY")
            with closing(sqlite3.connect(path)) as d:
                d.execute("create table outcomes(outcome_id,setup_id,status,payload,payload_sha256,appended_at_utc)")
                d.execute("insert into outcomes values(?,?,?,?,?,?)",("o","s","WIN",json.dumps(row),"hash","time"))
                d.commit()
            before=path.read_bytes();rows,digest=read_outcomes(path);report=analyze_rows(rows)
            self.assertEqual(before,path.read_bytes());self.assertEqual(read_outcomes(path)[1],digest)
            self.assertTrue(report["total"]["small_sample"])
            self.assertEqual(report["total"]["sample_size"],1)

if __name__=="__main__":unittest.main()
