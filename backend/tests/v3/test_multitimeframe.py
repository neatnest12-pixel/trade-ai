from datetime import UTC, datetime, timedelta

from app.v3.multitimeframe import CompletedCandle, alignment, completed_before, hierarchical


def c(tf, minutes): return CompletedCandle(tf,datetime(2026,1,1,tzinfo=UTC)+timedelta(minutes=minutes),1,1,1,1,"DIRECT_MT5")
def test_completed_higher_timeframe_join():
    decision=datetime(2026,1,1,2,1,tzinfo=UTC)
    assert completed_before([c("H1",0),c("H1",60)],decision,"H1").timestamp.hour==1
    assert completed_before([c("H6",0)],decision,"H6") is None
    assert completed_before([c("H12",0)],decision,"H12") is None
def test_alignment_and_hierarchy():
    s={"M1":"BEARISH","M5":"BEARISH","M15":"BEARISH","H1":"BULLISH","H6":"BEARISH","H12":"BEARISH"}; a=alignment(s); assert a["BULLISH_TIMEFRAME_COUNT"]==1; assert hierarchical(s)["HIGHER_TIMEFRAME_DIRECTION"]=="BEARISH"
