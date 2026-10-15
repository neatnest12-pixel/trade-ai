"""Conservative ownership: an existing lock is never stolen on a TTL alone."""
from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .certified_store import atomic_json


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() != 87  # access denied is NOT proof of death
        code = wintypes.DWORD()
        try:
            return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class Lease:
    def __init__(self, lock: Path, state: Path):
        self.lock, self.state = lock, state
        self.owner = {"pid": os.getpid(), "worker_id": str(uuid.uuid4()),
                      "acquired_at": datetime.now(UTC).isoformat()}
        self.extra = {}

    def __enter__(self):
        from .worker_audit import event
        event(self.state.stem,'startup',worker_id=self.owner['worker_id'])
        self.lock.parent.mkdir(parents=True, exist_ok=True)
        # Dead/unknown legacy locks require external process+command-line proof.
        fd = os.open(self.lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w") as handle:
            json.dump(self.owner, handle)
            handle.flush()
            os.fsync(handle.fileno())
        self.beat("STARTING")
        event(self.state.stem,'lock_acquired',lock_owned=True)
        return self

    def beat(self, status="RUNNING", **extra):
        self.extra.update(extra)
        atomic_json(self.state, {**self.owner, "status": status, "last_heartbeat": datetime.now(UTC).isoformat(),
                                 "fit_model": 0, "order_send": 0, **self.extra})
        from .worker_audit import event
        event(self.state.stem,'heartbeat',status=status,worker_id=self.owner['worker_id'],**{k:v for k,v in self.extra.items() if k in ['certified_resolved','pending']})

    def __exit__(self, typ, exc, tb):
        from .worker_audit import event,exception
        if exc is not None:exception(self.state.stem,exc)
        self.extra.update(exit_reason='NORMAL_RETURN' if exc is None else type(exc).__name__,exit_code=0 if exc is None else 1)
        self.beat("STOPPED" if typ is None else "FAILED", last_error=None if exc is None else str(exc))
        try:
            owner = json.loads(self.lock.read_text())
            if owner == self.owner:
                self.lock.unlink()
                event(self.state.stem,'lock_released',lock_owned=True)
        except FileNotFoundError:
            pass
        event(self.state.stem,'exit',exit_code=0 if exc is None else 1,exit_reason=self.extra['exit_reason'])


def effective_status(state: dict, lock: dict | None, now: datetime | None = None) -> str:
    """Never trust a historical RUNNING string after process death or stalled heartbeat."""
    if not lock or state.get("pid") != lock.get("pid") or not pid_alive(int(lock.get("pid", 0))):
        return "STOPPED_OR_STALE_OWNER"
    if state.get("worker_id") != lock.get("worker_id"):
        return "OWNERSHIP_MISMATCH"
    try:
        stamp = datetime.fromisoformat(state["last_heartbeat"])
        age = ((now or datetime.now(UTC))-stamp).total_seconds()
        if not 0 <= age <= 180:
            return "HEARTBEAT_STALE"
    except (KeyError, ValueError, TypeError):
        return "HEARTBEAT_INVALID"
    return state.get("status", "UNKNOWN")
