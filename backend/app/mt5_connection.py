"""Bounded, read-only MT5 connection plumbing; no execution or strategy API."""
from __future__ import annotations
import json
import os
import subprocess
from pathlib import Path

DEFAULT_TERMINAL = r'C:\Program Files\MetaTrader 5\terminal64.exe'


class MT5ConnectionError(RuntimeError):
    def __init__(self, code, **evidence):
        self.report = dict(status='FAIL', code=code, **evidence)
        super().__init__(json.dumps(self.report, default=str))


def terminal_processes():
    command = "@(Get-CimInstance Win32_Process -Filter \"Name='terminal64.exe'\" | Select-Object ProcessId,ParentProcessId,ExecutablePath) | ConvertTo-Json -Compress"
    result = subprocess.run([r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe',
        '-NoProfile', '-NonInteractive', '-Command', command], capture_output=True, text=True,
        timeout=10, check=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    value = json.loads(result.stdout.strip() or '[]')
    return value if isinstance(value, list) else [value]


def shutdown(mt5):
    try:
        mt5.shutdown()
        return dict(status='PASS')
    except Exception as exc:
        return dict(status='FAIL', code='SHUTDOWN_FAILED', exception=type(exc).__name__)


def initialize_checked(mt5, *, path=None, timeout=None, processes=None, exists=None):
    """Attach only by default. initialize can launch MT5, so inspect first.

    No automatic retries, process termination, login or symbol selection. The
    owner MUST call shutdown in finally after success; failed attempts clean up
    here. Repeated calls detach the caller's prior IPC session first.
    """
    path = path or os.getenv('V34_MT5_TERMINAL_PATH', DEFAULT_TERMINAL)
    timeout = int(timeout or os.getenv('V34_MT5_TIMEOUT_MS', '15000'))
    if not 1000 <= timeout <= 60000:
        raise MT5ConnectionError('INVALID_TIMEOUT', timeout_ms=timeout)
    if not (exists or Path.is_file)(Path(path)):
        raise MT5ConnectionError('EXECUTABLE_MISSING', terminal_path=path)
    try:
        running = (processes or terminal_processes)()
    except Exception as exc:
        raise MT5ConnectionError('PROCESS_INVENTORY_UNAVAILABLE', exception=type(exc).__name__) from exc
    matching = [p for p in running if str(p.get('ExecutablePath', '')).casefold() == path.casefold()]
    if len(running) > 1 or (running and len(matching) != 1):
        raise MT5ConnectionError('TERMINAL_OWNERSHIP_AMBIGUOUS', processes=running)
    if not matching and os.getenv('V34_MT5_ALLOW_LAUNCH', 'false').lower() != 'true':
        raise MT5ConnectionError('TERMINAL_NOT_RUNNING', launch_allowed=False)
    cleanup = shutdown(mt5)
    if cleanup['status'] != 'PASS':
        raise MT5ConnectionError('PRIOR_SESSION_CLEANUP_FAILED')
    evidence = dict(terminal_path=path, timeout_ms=timeout, processes=running,
                    launch_requested=not bool(matching))
    try:
        if not mt5.initialize(path=path, timeout=timeout):
            error = mt5.last_error()
            code = {-10005: 'IPC_TIMEOUT', -6: 'TERMINAL_AUTHORIZATION_FAILED'}.get(error[0] if error else None, 'INITIALIZE_FAILED')
            raise MT5ConnectionError(code, last_error=error, **evidence)
        terminal = mt5.terminal_info()
        if terminal is None:
            raise MT5ConnectionError('TERMINAL_INFO_UNAVAILABLE', **evidence)
        if not terminal.connected:
            raise MT5ConnectionError('TERMINAL_DISCONNECTED', **evidence)
        account = mt5.account_info()
        if account is None:
            raise MT5ConnectionError('ACCOUNT_INFO_UNAVAILABLE', **evidence)
        return dict(status='PASS', code='ACCOUNT_CONNECTED', server=account.server,
                    terminal_build=getattr(terminal, 'build', None), **evidence)
    except MT5ConnectionError:
        shutdown(mt5)
        raise
    except Exception as exc:
        shutdown(mt5)
        raise MT5ConnectionError('PYTHON_PACKAGE_FAILURE', exception=type(exc).__name__, **evidence) from exc
