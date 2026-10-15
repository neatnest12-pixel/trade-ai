from datetime import datetime,UTC,timedelta
from app.v3.health_checkpoints import update

def test_no_premature_checkpoint_or_unobserved_gap(tmp_path):
    p=tmp_path/'state.json';t=datetime(2026,9,26,tzinfo=UTC)
    r={'collector_health':{'effective_status':'RUNNING','pid':1},'resolver_health':{'effective_status':'RUNNING','pid':2}}
    assert update(p,r,t)['checkpoints']['15_MIN']=='NOT_REACHED'
    assert update(p,r,t+timedelta(days=1))['observed_continuous_seconds']==0
    for seconds in range(60,901,60):s=update(p,r,t+timedelta(days=1,seconds=seconds))
    assert s['checkpoints']['15_MIN']=='OBSERVED' and s['checkpoints']['1_HOUR']=='NOT_REACHED'
    r['collector_health']['effective_status']='STOPPED_OR_STALE_OWNER'
    assert update(p,r,t+timedelta(days=1,seconds=930))['continuous_start'] is None
