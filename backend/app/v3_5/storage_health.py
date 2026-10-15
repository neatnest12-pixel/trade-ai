"""Operational disk reserve; never changes decision inputs or label policy."""
from __future__ import annotations

import errno
import shutil
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

# About a week of preliminary ~3.4 GB/day growth on this 255 GB volume, plus room
# for checkpoint/state writes. Both requirements apply; warning is earlier.
MIN_FREE_BYTES = 10 * 1024**3
MIN_FREE_PERCENT = 10.0
WARNING_FREE_PERCENT = 15.0


class StorageHealthError(RuntimeError):
    def __init__(self, reason, diagnostics):
        super().__init__(reason)
        self.diagnostics = diagnostics


def sample(path: str | Path) -> dict:
    location = Path(path).resolve()
    while not location.exists():
        location = location.parent
    usage = shutil.disk_usage(location)
    required = max(MIN_FREE_BYTES, int(usage.total * MIN_FREE_PERCENT / 100))
    percent = usage.free / usage.total * 100
    safe = usage.free >= MIN_FREE_BYTES and percent >= MIN_FREE_PERCENT
    return {"checked_at_utc": datetime.now(UTC).isoformat(), "volume": location.anchor,
            "capacity_bytes": usage.total, "used_bytes": usage.used, "free_bytes": usage.free,
            "free_percent": percent, "min_free_bytes": MIN_FREE_BYTES,
            "min_free_percent": MIN_FREE_PERCENT, "required_reserve_bytes": required,
            "headroom_bytes": usage.free - required, "storage_safe": safe,
            "status": "STORAGE_UNCERTIFIED" if not safe else "WARNING" if percent < WARNING_FREE_PERCENT else "PASS",
            "reason": None if safe else "FREE_SPACE_RESERVE_NOT_MET"}


def require(path: str | Path) -> dict:
    metrics = sample(path)
    if not metrics["storage_safe"]:
        raise StorageHealthError("FREE_SPACE_RESERVE_NOT_MET", metrics)
    return metrics


def is_storage_error(exc) -> bool:
    return isinstance(exc, StorageHealthError) or isinstance(exc, OSError) and exc.errno == errno.ENOSPC or isinstance(exc, sqlite3.Error) and getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_FULL


def wait_until_healthy(path, publish, *, sleep=time.sleep):
    """No data callback runs while paused. Caller recertifies DB/clock on return."""
    while True:
        metrics = sample(path)
        publish(metrics)
        if metrics["storage_safe"]:
            return metrics
        sleep(5)


class GuardedConnection(sqlite3.Connection):
    storage_path = None

    def _guard(self):
        if self.storage_path is not None:
            require(self.storage_path)

    def execute(self, sql, parameters=(), /):
        command = sql.lstrip().split(None, 1)[0].upper() if sql.strip() else ""
        if command in {"INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "ALTER", "DROP", "COMMIT", "END"}:
            self._guard()
        try:
            return super().execute(sql, parameters)
        except BaseException as exc:
            if is_storage_error(exc):
                self.rollback()
                raise StorageHealthError("DURABLE_WRITE_STORAGE_FAILURE", sample(self.storage_path or '.')) from exc
            raise

    def executescript(self, script, /):
        self._guard()
        try:
            return super().executescript(script)
        except BaseException as exc:
            if is_storage_error(exc):
                self.rollback()
                raise StorageHealthError("DURABLE_WRITE_STORAGE_FAILURE", sample(self.storage_path or '.')) from exc
            raise

    def executemany(self, sql, parameters, /):
        self._guard()
        try:
            return super().executemany(sql, parameters)
        except BaseException as exc:
            if is_storage_error(exc):
                self.rollback()
                raise StorageHealthError("DURABLE_WRITE_STORAGE_FAILURE", sample(self.storage_path or '.')) from exc
            raise

    def commit(self):
        try:
            self._guard()
            return super().commit()
        except BaseException as exc:
            self.rollback()
            if is_storage_error(exc) and not isinstance(exc, StorageHealthError):
                raise StorageHealthError("DURABLE_COMMIT_STORAGE_FAILURE", sample(self.storage_path or '.')) from exc
            raise

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is None and self.in_transaction:
            try:
                self._guard()
            except BaseException:
                self.rollback()
                raise
        return super().__exit__(exc_type, exc_value, traceback)


def connect(path, *, timeout=30, enforce=None):
    path = Path(path).resolve()
    # Synthetic test stores do not consume campaign-authorized certified writes.
    if enforce is None:
        enforce = 'runtime/v35_provenance' in path.as_posix()
    if enforce:
        require(path)
    db = sqlite3.connect(path, timeout=timeout, factory=GuardedConnection)
    db.storage_path = path if enforce else None
    return db
