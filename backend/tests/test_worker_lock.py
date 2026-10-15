import time
from pathlib import Path

from app.services.worker_lock import SingleWorkerLock


def test_atomic_contention_and_clean_release(tmp_path: Path):
    path = tmp_path / "worker.lock"
    first = SingleWorkerLock(path, "A", 60)
    second = SingleWorkerLock(path, "B", 60)
    assert first.acquire()
    assert not second.acquire()
    assert first.release()
    assert second.acquire()
    assert second.release()


def test_heartbeat_and_zombie_owner_rejection(tmp_path: Path):
    path = tmp_path / "worker.lock"
    owner = SingleWorkerLock(path, "A", 1)
    successor = SingleWorkerLock(path, "B", 1)
    assert owner.acquire()
    owner.abandon_for_test()
    assert not successor.acquire()
    time.sleep(1.1)
    assert successor.acquire()
    assert not owner.refresh()
    assert not owner.release()
    assert successor.release()


def test_stale_lease_takeover(tmp_path: Path):
    path = tmp_path / "worker.lock"
    first = SingleWorkerLock(path, "A", 1)
    second = SingleWorkerLock(path, "B", 1)
    assert first.acquire()
    first.abandon_for_test()
    assert not second.acquire()
    time.sleep(1.1)
    assert second.acquire()
    assert second.is_owner()
    assert second.release()