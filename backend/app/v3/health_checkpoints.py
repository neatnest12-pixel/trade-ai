"""Conservative continuity evidence: gaps, changed owners or unhealthy samples reset it."""
from datetime import UTC,datetime
from .certified_store import atomic_json

def update(path,report,now=None):
    import json
    now=now or datetime.now(UTC)
    workers=[report[k] for k in ['collector_health','resolver_health']]
    good=all(w.get('effective_status')=='RUNNING' for w in workers)
    owners=[w.get('pid') for w in workers]
    prior=json.loads(path.read_text()) if path.exists() else {}
    gap=(now-datetime.fromisoformat(prior['last_sample'])).total_seconds() if prior else None
    continuous=good and prior.get('owners')==owners and gap is not None and 0<=gap<=120 and prior.get('healthy')
    start=prior['continuous_start'] if continuous else now.isoformat() if good else None
    elapsed=(now-datetime.fromisoformat(start)).total_seconds() if start else 0
    state={'last_sample':now.isoformat(),'healthy':good,'owners':owners,'continuous_start':start,
      'observed_continuous_seconds':elapsed,'checkpoints':{name:('OBSERVED' if elapsed>=seconds else 'NOT_REACHED')
       for name,seconds in [('15_MIN',900),('1_HOUR',3600),('6_HOURS',21600),('24_HOURS',86400)]},
      'limitation':'Requires ongoing samples <=120s apart; observed heartbeat continuity is not proof against every between-sample failure',
      'health_only':report}
    atomic_json(path,state)
    for name,status in state['checkpoints'].items():
        if status=='OBSERVED':
            target=path.parent/(name+'_'+start.replace(':','').replace('+','_')+'.json')
            if not target.exists():atomic_json(target,state)
    return state
