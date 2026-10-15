from datetime import UTC, datetime, timedelta

from app.research.integrity import Event, canonical_event_id, embargo_events, purge_events


def test_mixed_horizon_purge():
    b=datetime(2026,1,2,tzinfo=UTC)
    events=[Event("a",b-timedelta(minutes=5),b-timedelta(minutes=1)),Event("b",b-timedelta(minutes=5),b),Event("c",b,b+timedelta(minutes=20))]
    kept,removed=purge_events(events,b)
    assert [e.event_id for e in kept]==["a"] and removed==2

def test_embargo_boundary():
    b=datetime(2026,1,2,tzinfo=UTC)
    events=[Event("a",b+timedelta(minutes=19),b+timedelta(minutes=20)),Event("b",b+timedelta(minutes=20),b+timedelta(minutes=21))]
    kept,removed=embargo_events(events,b,20)
    assert [e.event_id for e in kept]==["b"] and removed==1

def test_canonical_id_ignores_generator():
    a=canonical_event_id("EURUSD","1m","2026-01-01T00:00:00Z","LONG",1.1,1.09,1.115,20)
    b=canonical_event_id("EURUSD","1m","2026-01-01T00:00:00Z","LONG",1.1,1.09,1.115,20)
    assert a==b
