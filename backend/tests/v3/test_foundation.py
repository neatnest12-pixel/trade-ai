from datetime import UTC, datetime, timedelta

from app.v3.contracts import DataOrigin, DecisionObservation, session_bucket
from app.v3.families import CandidateSignal, StrategyFamily
from app.v3.outcomes import RR_MATRIX, assert_rr


def test_observation_rejects_future_tick():
    now=datetime.now(UTC)
    try: DecisionObservation(now,"EURUSD",1,1.1,.1,100,now+timedelta(seconds=1),0).validate()
    except ValueError: pass
    else: raise AssertionError

def test_contracts_and_separation():
    x=DecisionObservation(datetime.now(UTC),"EURUSD",1,1.1,.1,100,datetime.now(UTC),0,DataOrigin.PROSPECTIVE_OBSERVED); x.validate()
    CandidateSignal(StrategyFamily.TREND,"SELL",.5, x.timestamp.isoformat()).validate(); assert RR_MATRIX==(1,1.5,2,3,5)

def test_sessions_and_rr():
    assert session_bucket(9)=="LONDON"; assert session_bucket(13)=="OVERLAP"; assert_rr(2)
