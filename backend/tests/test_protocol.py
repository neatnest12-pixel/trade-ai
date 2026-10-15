import pandas as pd
import pytest

from app.research.protocol import (
    Promotion,
    chronological_split,
    cost_adjusted_return,
    promotion_gate,
)


def test_split_is_chronological_and_disjoint():
    x=pd.DataFrame({'timestamp':pd.date_range('2020-01-01',periods=6,freq='D',tz='UTC')})
    a,b,c=chronological_split(x,'2020-01-02','2020-01-04'); assert len(a)==2 and len(b)==2 and len(c)==2
def test_split_rejects_shuffle():
    x=pd.DataFrame({'timestamp':pd.to_datetime(['2020-01-02','2020-01-01'],utc=True)})
    with pytest.raises(ValueError): chronological_split(x,'2020-01-02','2020-01-03')
def test_cost_and_promotion():
    assert cost_adjusted_return(.01,.001,.001,.001,1.5)==pytest.approx(.0055)
    assert promotion_gate(.2,.1,True,True,0)==Promotion.RESEARCH
    assert promotion_gate(.2,.1,True,True,1)==Promotion.INSUFFICIENT
