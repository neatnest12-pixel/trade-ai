import unittest
from dataclasses import replace
from datetime import timedelta
from app.v3_5.market_context import utc,Sample,Bar,_intervals,Config
from app.v3_5.broker_context import *

class BrokerContextTests(unittest.TestCase):
    def setup_evidence(self,when="2026-10-04T13:00:00Z"):
        t=utc(when)
        term=TerminalEvidence(t,t,True,True,True,"ICMarketsSC-Demo",True,True,True,"DEMO")
        tick=TickEvidence(t-timedelta(seconds=120),t,1.1,1.1001)
        prev=replace(tick,available_at=t-timedelta(seconds=10))
        cal=CalendarEvidence("EURUSD","ICMarkets","ICMarketsSC-Demo","CLOSED_WEEKEND",t-timedelta(days=3),t-timedelta(days=2),t+timedelta(days=1),"TEST_BROKER_NOTICE",True)
        return t,term,tick,prev,cal
    def run_context(self,t,term,tick,prev,cal):
        return snapshot(t,broker="ICMarkets",expected_server="ICMarketsSC-Demo",terminal=term,tick=tick,previous_tick=prev,calendar=cal)
    def test_weekend_authoritative(self):
        t,a,b,p,c=self.setup_evidence();r=self.run_context(t,a,b,p,c)
        self.assertEqual(r.context['current_session'],'OFF_MARKET');self.assertEqual(r.quote_health['feed_state'],'EXPECTED_MARKET_CLOSURE')
    def test_weekend_unknown(self):
        t,a,b,p,c=self.setup_evidence();r=self.run_context(t,a,b,p,replace(c,authoritative=False))
        self.assertEqual(r.quote_health['feed_state'],'INSUFFICIENT_EVIDENCE');self.assertEqual(r.market['effective_market_state'],'UNKNOWN')
    def test_open_stale(self):
        t,a,b,p,c=self.setup_evidence("2026-10-05T10:00:00Z")
        r=self.run_context(t,a,b,p,replace(c,state='OPEN'))
        self.assertEqual(r.quote_health['feed_state'],'STALE_FEED_MARKET_EXPECTED_OPEN')
        self.assertEqual(r.market['effective_market_state'],'OPEN_BUT_NO_FRESH_TICKS')
    def test_live(self):
        t,a,b,p,c=self.setup_evidence();b=replace(b,timestamp=t-timedelta(seconds=1))
        r=self.run_context(t,a,b,p,None);self.assertEqual(r.quote_health['feed_state'],'HEALTHY_LIVE_FEED')
    def test_disconnected(self):
        t,a,b,p,c=self.setup_evidence();r=self.run_context(t,replace(a,initialized=False),b,p,c)
        self.assertEqual(r.quote_health['feed_state'],'MT5_DISCONNECTED')
    def test_unavailable(self):
        t,a,b,p,c=self.setup_evidence();self.assertEqual(self.run_context(t,replace(a,symbol_available=False),b,p,c).quote_health['feed_state'],'SYMBOL_UNAVAILABLE')
    def test_invalid(self):
        t,a,b,p,c=self.setup_evidence();self.assertEqual(self.run_context(t,a,replace(b,ask=0),p,c).quote_health['feed_state'],'INVALID_QUOTE')
    def test_future_tick_clock(self):
        t,a,b,p,c=self.setup_evidence();self.assertEqual(self.run_context(t,a,replace(b,timestamp=t+timedelta(seconds=1)),p,c).quote_health['feed_state'],'CLOCK_OR_TIMESTAMP_ERROR')
    def test_server_uncertainty(self):
        t,a,b,p,c=self.setup_evidence();r=self.run_context(t,replace(a,server='OTHER'),b,p,c)
        self.assertFalse(r.market['broker_calendar_authority']);self.assertEqual(r.quote_health['feed_state'],'INSUFFICIENT_EVIDENCE')
    def test_expired_calendar(self):
        t,a,b,p,c=self.setup_evidence();r=self.run_context(t,a,b,p,replace(c,valid_until=t))
        self.assertEqual(r.market['broker_market_state'],'UNKNOWN')
    def test_future_contamination_rejected(self):
        t,a,b,p,c=self.setup_evidence()
        for field,values in [('spread_history',[Sample('EURUSD',t+timedelta(seconds=1),t+timedelta(seconds=1),.001,'OFF_MARKET')]),('volatility_history',[Sample('EURUSD',t,t+timedelta(seconds=1),.001,'OFF_MARKET')]),('bars',[Bar('EURUSD',t+timedelta(seconds=1),t+timedelta(seconds=1),2,1,1.5)])]:
            with self.assertRaises(FutureEvidenceError):snapshot(t,broker='ICMarkets',expected_server='ICMarketsSC-Demo',**{field:values})
        with self.assertRaises(FutureEvidenceError):self.run_context(t,a,replace(b,available_at=t+timedelta(seconds=1)),p,c)
        with self.assertRaises(FutureEvidenceError):self.run_context(t,a,b,p,replace(c,available_at=t+timedelta(seconds=1)))
    def test_no_outcome_api(self):
        t,a,b,p,c=self.setup_evidence()
        with self.assertRaises(TypeError):snapshot(t,broker='ICMarkets',expected_server='ICMarketsSC-Demo',outcomes=[])
    def test_shadow_isolation(self):
        t,a,b,p,c=self.setup_evidence();records=[]
        r=shadow_observation('existing-id',t,broker='ICMarkets',expected_server='ICMarketsSC-Demo',terminal=a,tick=b,previous_tick=p,calendar=c,recorder=records.append)
        self.assertEqual(len(records),1);self.assertNotIn('decision',records[0]);self.assertEqual(status_view(r)['signal_integration'],'SHADOW_MODE_NOT_USED_FOR_DECISIONS')
    def test_contradictory_closed_live(self):
        t,a,b,p,c=self.setup_evidence();r=self.run_context(t,a,replace(b,timestamp=t-timedelta(seconds=1)),p,c)
        self.assertEqual(r.market['effective_market_state'],'UNKNOWN');self.assertNotEqual(r.quote_health['feed_state'],'HEALTHY_LIVE_FEED')
    def test_dst_session_boundaries(self):
        for day,london,ny in [('2026-03-06',8,13),('2026-03-09',8,12),('2026-03-30',7,12),('2026-10-23',7,12),('2026-10-26',8,12),('2026-11-02',8,13)]:
            t=utc(day+'T12:30:00Z');intervals=[x for x in _intervals(t,Config()) if x[0].date()==t.date()]
            for name,hour in [('LONDON',london),('NEW_YORK',ny)]:
                start,end,_=next(x for x in intervals if x[2]==name)
                self.assertEqual(start.hour,hour);self.assertEqual((end-start).total_seconds(),9*3600)
            f=context(t,'EURUSD');self.assertEqual(f['is_london_ny_overlap'],ny==12)
    def test_terminal_evidence_stale(self):
        t,a,b,p,c=self.setup_evidence();a=replace(a,observed_at=t-timedelta(minutes=1))
        self.assertEqual(self.run_context(t,a,b,p,c).quote_health['feed_state'],'INSUFFICIENT_EVIDENCE')
    def test_unknown_symbol_not_verified_closure(self):
        t,a,b,p,c=self.setup_evidence()
        self.assertEqual(self.run_context(t,replace(a,symbol_available=None),b,p,c).quote_health['feed_state'],'INSUFFICIENT_EVIDENCE')

if __name__=='__main__':unittest.main()
