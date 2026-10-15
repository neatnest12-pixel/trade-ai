"""Windows state persistence only; no market or decision behavior."""
from __future__ import annotations

import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path


def read_text_shared(path: Path) -> str:
    """Read a complete old/new inode, permitting Windows atomic replacement."""
    if os.name != 'nt':
        return path.read_text(encoding='utf8')
    import ctypes
    import msvcrt
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong,
                                 ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.CreateFileW(str(path), 0x80000000, 7, None, 3, 0, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle(handle)
        raise
    with os.fdopen(fd, 'r', encoding='utf8') as reader:
        return reader.read()


@contextmanager
def exclusive(path: Path):
    """Nonblocking OS byte lock; released automatically on process death."""
    import msvcrt
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as guard:
        if guard.seek(0, 2) == 0:
            guard.write(b'0'); guard.flush()
        guard.seek(0)
        try:
            msvcrt.locking(guard.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise RuntimeError('V35_CONCURRENT_WRITER_OR_LOCK_ACQUISITION') from exc
        try:
            yield
        finally:
            guard.seek(0)
            msvcrt.locking(guard.fileno(), msvcrt.LK_UNLCK, 1)


def serialized_acquisition(function):
    """Serialize stale-owner verification and exclusive lock creation."""
    def wrapped(*args, **kwargs):
        path = function.__globals__['LOCK_PATH']
        with exclusive(path.with_name(path.name + '.acquisition.guard')):
            return function(*args, **kwargs)
    return wrapped


def assert_owner(path: Path, owner: dict):
    current = json.loads(read_text_shared(path))
    if (current.get('pid'), current.get('worker_id')) != (owner.get('pid'), owner.get('worker_id')):
        raise RuntimeError('V35_LOCK_OWNERSHIP_LOST')


def write_json_atomic(path: Path, value: dict, *, preserve_failed_temp: bool = False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive(path.with_name(path.name + '.writer.guard')):
        fd, name = tempfile.mkstemp(prefix=f'.{path.name}.{os.getpid()}.', suffix='.tmp', dir=path.parent)
        temporary = Path(name)
        replaced = False
        try:
            with os.fdopen(fd, 'w', encoding='utf8') as handle:
                json.dump(value, handle, sort_keys=True, indent=2)
                handle.write('\n'); handle.flush(); os.fsync(handle.fileno())
            for attempt in range(6):
                try:
                    os.replace(temporary, path)
                    replaced = True
                    break
                except OSError as exc:
                    if os.name != 'nt' or getattr(exc, 'winerror', None) not in (5, 32, 33) or attempt == 5:
                        raise
                    time.sleep(0.05 * 2 ** attempt)
        finally:
            # During worker finalization this file may be the only complete
            # record of the intended terminal state. Keep it for forensic
            # recovery if the atomic replacement did not complete.
            if replaced or not preserve_failed_temp:
                temporary.unlink(missing_ok=True)


def cleanup_stale_temps(path: Path, pid_exists):
    """Remove only our uniquely named files whose PID is proven dead."""
    for temp in path.parent.glob(f'.{path.name}.*.tmp'):
        try:
            pid = int(temp.name[len(path.name) + 2:].split('.')[0])
        except ValueError:
            continue
        if pid_exists(pid) is False:
            temp.unlink(missing_ok=True)


def install_trace_heartbeat(db, refresh, *, monotonic=time.monotonic):
    """Heartbeat between short SQL statements; capture callback failures for caller."""
    errors = []
    last = monotonic()

    def trace(_statement):
        nonlocal last
        if errors or monotonic() - last < 5:
            return
        try:
            refresh()
            last = monotonic()
        except Exception as exc:
            errors.append(exc)

    db.set_trace_callback(trace)
    return errors
