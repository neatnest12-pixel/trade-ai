"""Atomic, named single-worker lease backed by a Windows-safe file lock."""
from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

_PROCESS_GUARDS: dict[str, threading.RLock] = {}
_PROCESS_GUARDS_LOCK = threading.Lock()


class SingleWorkerLock:
    """One owner per lock name; stale owners are only replaceable after expiry."""

    def __init__(self, path: Path, worker_id: str, lease_seconds: int, lock_name: str = "navora-v1-paper-validation") -> None:
        self.path = path.resolve()
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.lock_name = lock_name
        self.handle = None
        self.acquired_at: str | None = None
        with _PROCESS_GUARDS_LOCK:
            self._guard = _PROCESS_GUARDS.setdefault(str(self.path), threading.RLock())

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    def _lock_file(self) -> bool:
        try:
            if os.name == "nt":
                import msvcrt
                self.handle.seek(0)
                if self.path.stat().st_size == 0:
                    self.handle.write(" ")
                    self.handle.flush()
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _unlock_file(self) -> None:
        if not self.handle:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None

    def _read(self) -> dict[str, str]:
        self.handle.seek(0)
        try:
            return json.loads(self.handle.read().strip() or "{}")
        except json.JSONDecodeError:
            return {}

    def _write(self, state: dict[str, str]) -> None:
        self.handle.seek(0)
        self.handle.truncate()
        self.handle.write(json.dumps(state, sort_keys=True))
        self.handle.flush()
        os.fsync(self.handle.fileno())

    def acquire(self) -> bool:
        with self._guard:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.handle = self.path.open("a+", encoding="utf-8")
            if not self._lock_file():
                self.handle.close()
                self.handle = None
                return False
            prior = self._read()
            expires = prior.get("lease_expires_at")
            if prior.get("lock_name") == self.lock_name and expires and datetime.fromisoformat(expires) > self._now():
                self._unlock_file()
                return False
            now = self._now()
            self.acquired_at = now.isoformat()
            self._write({"lock_name": self.lock_name, "owner_id": self.worker_id,
                         "acquired_at": self.acquired_at, "heartbeat_at": now.isoformat(),
                         "lease_expires_at": (now + timedelta(seconds=self.lease_seconds)).isoformat()})
            return True

    def is_owner(self) -> bool:
        if not self.handle:
            return False
        state = self._read()
        return state.get("lock_name") == self.lock_name and state.get("owner_id") == self.worker_id and datetime.fromisoformat(state.get("lease_expires_at", "1970-01-01T00:00:00+00:00")) > self._now()

    def refresh(self) -> bool:
        with self._guard:
            if not self.handle:
                return False
            state = self._read()
            if state and not self.is_owner():
                return False
            now = self._now()
            self._write({"lock_name": self.lock_name, "owner_id": self.worker_id,
                         "acquired_at": self.acquired_at or now.isoformat(), "heartbeat_at": now.isoformat(),
                         "lease_expires_at": (now + timedelta(seconds=self.lease_seconds)).isoformat()})
            return True

    def release(self) -> bool:
        with self._guard:
            if not self.handle or not self.is_owner():
                return False
            self._write({})
            self._unlock_file()
            return True

    def abandon_for_test(self) -> None:
        """Simulate a crashed process without clearing its lease; test-only helper."""
        with self._guard:
            self._unlock_file()
