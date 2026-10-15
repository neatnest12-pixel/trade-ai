import json, sqlite3
from datetime import UTC,datetime,timedelta
import pytest
from app.v3_5 import clock_lifecycle as c
from app.v3_5 import live_adapter as live
from app.v3_5 import outcome_resolver as resolver
from app.v3_5.state_io import install_trace_heartbeat
NOW=datetime(2026,10,1,10,tzinfo=UTC)
STATUS='Leap Indicator: 0\nStratum: 2\nSource: time.google.com,0x8\nLast Successful Sync Time: '+NOW.astimezone().strftime('%d-%m-%Y %H:%M:%S')
SERVICE='STATE : 4 RUNNING'
SAMPLES='\n'.join(['-0.171000s']*5)
def probe(status=STATUS,service=SERVICE,samples=SAMPLES,error=None):
 def query(*args):
  key='service' if args[0]=='sc.exe' else 'samples' if '/stripchart' in args else 'status'
  if key==error:raise c.ClockQueryError('temporary command failure')
  return {'status':status,'service':service,'samples':samples}[key]
 return c.certify(query,lambda:NOW,lambda **kw:{'clock_safe':True})
def test_clock_healthy_preserves_evidence_shape():
 evidence,diag=probe();assert evidence['clock_safe'];assert diag['status']=='PASS';assert evidence['ntp_mean_offset_seconds']==-.171
@pytest.mark.parametrize('kwargs,reason',[
 ({'error':'status'},'WINDOWS_TIME_STATUS_QUERY_FAILED'),({'error':'service'},'W32TIME_SERVICE_QUERY_FAILED'),({'error':'samples'},'INDEPENDENT_NTP_QUERY_FAILED'),
 ({'service':'STATE : 1 STOPPED'},'W32TIME_SERVICE_NOT_RUNNING'),
 ({'status':STATUS.replace('time.google.com','time.windows.com')},'WINDOWS_NTP_SOURCE_NOT_CERTIFIED'),
 ({'status':STATUS.replace('Indicator: 0','Indicator: 3')},'WINDOWS_CLOCK_UNSYNCHRONIZED_LEAP'),
 ({'status':STATUS.replace('Stratum: 2','Stratum: 0')},'WINDOWS_STRATUM_NOT_CERTIFIED'),
 ({'status':STATUS.replace('Leap Indicator','localized')},'WINDOWS_LEAP_INDICATOR_PARSE_FAILED'),
 ({'status':STATUS.replace(NOW.astimezone().strftime('%d-%m-%Y %H:%M:%S'),(NOW-timedelta(hours=2)).astimezone().strftime('%d-%m-%Y %H:%M:%S'))},'WINDOWS_TIME_SYNC_STALE'),
 ({'samples':'timeout'},'INDEPENDENT_NTP_SAMPLES_UNAVAILABLE'),({'samples':'+5.010000s\n'*5},'INDEPENDENT_NTP_OFFSET_OUTSIDE_CONTRACT'),
 ({'samples':'+0.000000s\n+0.300000s\n+0.100000s'},'INDEPENDENT_NTP_SPREAD_OUTSIDE_CONTRACT')])
def test_exact_failure_subcheck(kwargs,reason):
 with pytest.raises(c.ClockHealthError) as exc:probe(**kwargs)
 assert str(exc.value)==reason;assert exc.value.diagnostics['failed_subcheck']==reason
@pytest.mark.parametrize('kind',['status','samples','source','unsync'])
def test_transient_recovers_bounded_without_data_callbacks(kind):
 ticks=[0.];phases=[];calls=[]
 initial=c.ClockHealthError('initial',{'failed_subcheck':kind})
 def check(deadline):
  calls.append(deadline)
  if len(calls)<3:raise c.ClockHealthError(kind,{'failed_subcheck':kind})
  return {'clock_safe':True}
 result=c.recertify(check,initial,lambda phase,*a:phases.append(phase),monotonic=lambda:ticks[0],sleep=lambda s:ticks.__setitem__(0,ticks[0]+s))
 assert result['clock_safe'];assert len(calls)==3;assert ticks[0]==10;assert phases[-1]=='HEALTHY';assert set(phases)=={'HEALTHY','CLOCK_UNCERTIFIED_TRANSIENT'}
def test_persistent_clock_stops_at_hard_deadline():
 ticks=[0.];phases=[]
 def check(deadline):raise c.ClockHealthError('unsync',{})
 with pytest.raises(c.PersistentClockFailure):c.recertify(check,c.ClockHealthError('initial',{}),lambda phase,*a:phases.append(phase),monotonic=lambda:ticks[0],sleep=lambda s:ticks.__setitem__(0,ticks[0]+s),window=12)
 assert ticks[0]==12;assert phases[-1]=='FAILED_PERSISTENT'
def test_non_clock_persistence_failure_never_retried():
 calls=[]
 def check(deadline):calls.append(deadline);raise PermissionError('persistent state error')
 with pytest.raises(PermissionError):c.recertify(check,c.ClockHealthError('initial',{}),lambda *a:None)
 assert len(calls)==1
def test_late_success_cannot_certify():
 ticks=[0.]
 def check(deadline):ticks[0]=deadline+1;return {'clock_safe':True}
 with pytest.raises(c.PersistentClockFailure):c.recertify(check,c.ClockHealthError('initial',{}),lambda *a:None,monotonic=lambda:ticks[0],window=1)

def test_publish_crossing_deadline_never_attempts_negative_sleep():
 ticks=[0.];phases=[];sleep_calls=[];publish_calls=[0]
 def publish(phase,*args):
  phases.append(phase);publish_calls[0]+=1
  if publish_calls[0]==2:ticks[0]=3.
 def check(deadline):raise c.ClockHealthError('still_unsynchronized',{})
 def sleep(seconds):
  assert seconds>=0
  sleep_calls.append(seconds);ticks[0]+=seconds
 with pytest.raises(c.PersistentClockFailure):
  c.recertify(check,c.ClockHealthError('initial',{}),publish,monotonic=lambda:ticks[0],sleep=sleep,window=2,attempts=3)
 assert sleep_calls==[]
 assert phases[-1]=='FAILED_PERSISTENT'

def test_suspend_sized_monotonic_jump_fails_closed_without_negative_sleep():
 ticks=[0.];sleep_calls=[];publish_calls=[0]
 def publish(*args):
  publish_calls[0]+=1
  if publish_calls[0]==2:ticks[0]+=10*60*60
 def check(deadline):raise c.ClockHealthError('clock_still_uncertified',{})
 def sleep(seconds):
  assert seconds>=0
  sleep_calls.append(seconds);ticks[0]+=seconds
 with pytest.raises(c.PersistentClockFailure):
  c.recertify(check,c.ClockHealthError('initial',{}),publish,monotonic=lambda:ticks[0],sleep=sleep,window=30,attempts=4)
 assert sleep_calls==[]

def test_resolver_deadline_sleep_helper_skips_elapsed_boundary():
 ticks=[9.];sleep_calls=[]
 assert not c.sleep_until(9.,monotonic=lambda:ticks[0],sleep=sleep_calls.append,max_sleep=5.)
 assert sleep_calls==[]

def test_plain_value_error_is_not_classified_as_persistent_clock_failure():
 assert live._failure_lifecycle_status(ValueError('bad sleep'),storage_failure=False)=='FAILED'
 assert live._failure_lifecycle_status(c.ClockHealthError('unsynchronized',{}),storage_failure=False)=='FAILED_PERSISTENT'
 assert live._failure_lifecycle_status(ValueError('disk'),storage_failure=True)=='PAUSED'

def test_trace_heartbeat_preserves_single_thread_and_surfaces_fault():
 db=sqlite3.connect(':memory:');ticks=[0.];calls=[]
 errors=install_trace_heartbeat(db,lambda:calls.append(1),monotonic=lambda:ticks[0]);ticks[0]=6;db.execute('select 1');assert calls==[1];assert not errors
 def fail():raise PermissionError('writer')
 errors=install_trace_heartbeat(db,fail,monotonic=lambda:ticks[0]);ticks[0]=12;db.execute('select 1');assert isinstance(errors[0],PermissionError)
def test_resolver_pauses_with_live_owner_and_resumes(tmp_path,monkeypatch):
 path=tmp_path/'runtime/v35_provenance';path.mkdir(parents=True);monkeypatch.setattr(resolver,'ROOT',tmp_path);monkeypatch.setattr(resolver,'_pid_exists',lambda pid:True)
 state={'pid':123,'worker_id':'one','heartbeat_at_utc':NOW.isoformat(),'status':'CLOCK_UNCERTIFIED_TRANSIENT','last_error':'clock','observations_count':0}
 (path/'live_adapter.lock').write_text(json.dumps(state));(path/'live_adapter_state.json').write_text(json.dumps(state));db=sqlite3.connect(':memory:');db.execute('create table observations(id)')
 with pytest.raises(resolver.CollectorClockPaused):resolver.verify_collector_health(db,NOW,certify_clock=False)
 state.update(status='RUNNING',last_error=None);(path/'live_adapter_state.json').write_text(json.dumps(state));assert resolver.verify_collector_health(db,NOW,certify_clock=False)['status']=='PASS'
 monkeypatch.setattr(resolver,'_pid_exists',lambda pid:False)
 with pytest.raises(RuntimeError,match='PID_NOT_RUNNING'):resolver.verify_collector_health(db,NOW,certify_clock=False)
def test_resolver_pause_does_not_register_or_resolve(monkeypatch):
 calls=[];monkeypatch.setattr(resolver,'observe_setups',lambda *a:calls.append(1))
 def pause():raise resolver.CollectorClockPaused('pause',{})
 with pytest.raises(resolver.CollectorClockPaused):resolver.resolver_cycle(None,None,None,health_guard=pause)
 assert calls==[]

def test_clock_command_timeout_is_structured_and_bounded(tmp_path,monkeypatch):
 import subprocess
 monkeypatch.setattr(live,'ROOT',tmp_path);seen=[]
 def command(*args,timeout_seconds):
  seen.append(timeout_seconds);raise subprocess.TimeoutExpired(args,timeout_seconds)
 monkeypatch.setattr(live,'_command',command);monkeypatch.setattr(live.time,'monotonic',lambda:10.)
 with pytest.raises(live.ClockCertificationError) as exc:live.clock_evidence(deadline_monotonic=12.)
 assert seen==[2.];assert exc.value.diagnostics['failed_subcheck']=='WINDOWS_TIME_STATUS_QUERY_FAILED'
 logs=list((tmp_path/'runtime/v35_provenance/lifecycle').glob('*.jsonl'));assert len(logs)==1
 assert json.loads(logs[0].read_text())['diagnostics']['commands']['WINDOWS_TIME_STATUS']['error_type']=='ClockQueryError'
def test_clock_heartbeat_permission_error_is_fatal(tmp_path,monkeypatch):
 def fail():raise PermissionError('state persistence')
 token=live._clock_heartbeat.set(fail)
 try:
  with pytest.raises(PermissionError):live.clock_evidence()
 finally:live._clock_heartbeat.reset(token)
def test_v34_frozen_files_unchanged():
 import hashlib
 from pathlib import Path
 manifest=Path(__file__).resolve().parents[3]/'runtime/v35_provenance/v34_freeze_manifest.json'
 frozen=json.loads(manifest.read_text())['immutable_file_manifest'];assert len(frozen)==52
 assert all(hashlib.sha256(Path(v['path']).read_bytes()).hexdigest()==v['sha256'] for v in frozen.values())
