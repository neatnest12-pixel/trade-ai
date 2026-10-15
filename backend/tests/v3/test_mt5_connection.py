from types import SimpleNamespace as NS
import pytest
from app.mt5_connection import initialize_checked, shutdown, MT5ConnectionError, DEFAULT_TERMINAL

class Client:
    def __init__(self):self.calls=[];self.ok=True;self.error=(-1,'test');self.terminal=NS(connected=True,build=6230);self.account=NS(server='test')
    def initialize(self,**kwargs):self.calls.append(('initialize',kwargs));return self.ok
    def terminal_info(self):return self.terminal
    def account_info(self):return self.account
    def last_error(self):return self.error
    def shutdown(self):self.calls.append(('shutdown',))
    def order_send(self,*a,**k):raise AssertionError('ORDER_PATH_FORBIDDEN')

def connect(c,**kw):
    return initialize_checked(c,processes=lambda:[{'ExecutablePath':DEFAULT_TERMINAL}],exists=lambda p:True,**kw)

@pytest.mark.parametrize('state,code',[('false','INITIALIZE_FAILED'),('ipc','IPC_TIMEOUT'),('auth','TERMINAL_AUTHORIZATION_FAILED'),('terminal','TERMINAL_INFO_UNAVAILABLE'),('account','ACCOUNT_INFO_UNAVAILABLE'),('disconnected','TERMINAL_DISCONNECTED')])
def test_failure_cleanup(state,code):
    c=Client()
    if state in ('false','ipc','auth'):c.ok=False
    if state=='auth':c.error=(-6,'Terminal: Authorization failed')
    if state=='ipc':c.error=(-10005,'IPC timeout')
    if state=='terminal':c.terminal=None
    if state=='account':c.account=None
    if state=='disconnected':c.terminal.connected=False
    with pytest.raises(MT5ConnectionError) as exc:connect(c)
    assert exc.value.report['code']==code and c.calls[-1]==('shutdown',)

def test_executable_missing():
    c=Client()
    with pytest.raises(MT5ConnectionError,match='EXECUTABLE_MISSING'):
        initialize_checked(c,exists=lambda p:False)
    assert not c.calls

def test_no_launch_without_permission(monkeypatch):
    monkeypatch.delenv('V34_MT5_ALLOW_LAUNCH',raising=False)
    c=Client()
    with pytest.raises(MT5ConnectionError,match='TERMINAL_NOT_RUNNING'):
        initialize_checked(c,exists=lambda p:True,processes=lambda:[])
    assert not c.calls

def test_duplicate_terminals():
    with pytest.raises(MT5ConnectionError,match='OWNERSHIP_AMBIGUOUS'):
        initialize_checked(Client(),exists=lambda p:True,processes=lambda:[{'ExecutablePath':DEFAULT_TERMINAL}]*2)

def test_repeated_initialization_and_final_cleanup():
    c=Client()
    try:
        assert connect(c)['status']=='PASS'
        assert connect(c)['status']=='PASS'
    finally:assert shutdown(c)['status']=='PASS'
    assert [x[0] for x in c.calls]==['shutdown','initialize','shutdown','initialize','shutdown']
    assert c.calls[1][1]==dict(path=DEFAULT_TERMINAL,timeout=15000)

def test_package_exception():
    c=Client()
    def broken(**kwargs):raise OSError('test')
    c.initialize=broken
    with pytest.raises(MT5ConnectionError,match='PYTHON_PACKAGE_FAILURE'):connect(c)
    assert c.calls[-1]==('shutdown',)

def test_shutdown_exception():
    c=Client()
    def broken():raise OSError('test')
    c.shutdown=broken
    assert shutdown(c)['status']=='FAIL'

@pytest.mark.parametrize('timeout',[0,-1,999,60001])
def test_bad_timeout(timeout):
    if timeout==0:timeout=-2
    with pytest.raises(MT5ConnectionError,match='INVALID_TIMEOUT'):connect(Client(),timeout=timeout)
