from pathlib import Path

import pytest

from app.services.worker_lock import SingleWorkerLock
from scripts.forward_paper import assert_official_write_guard

SNAPSHOT = "v1-paper-r4-0d891a2356e35464"
CONFIG = "0d891a2356e35464dbe3ba9f51c50fe240e40feff84fe4008d8b1564b55f9eac"


def test_write_guard_allows_current_owner(tmp_path: Path):
    lock = SingleWorkerLock(tmp_path / "worker.lock", "A", 60)
    assert lock.acquire()
    assert_official_write_guard(lock, SNAPSHOT, CONFIG, SNAPSHOT, CONFIG)
    assert lock.release()


@pytest.mark.parametrize("snapshot,config", [(None, CONFIG), (SNAPSHOT, None), ("wrong", CONFIG), (SNAPSHOT, "wrong")])
def test_write_guard_blocks_identity_mismatch(tmp_path: Path, snapshot: str | None, config: str | None):
    lock = SingleWorkerLock(tmp_path / "worker.lock", "A", 60)
    assert lock.acquire()
    with pytest.raises(RuntimeError, match="OFFICIAL_WRITE_GUARD_FAILED"):
        assert_official_write_guard(lock, snapshot, config, SNAPSHOT, CONFIG)
    assert lock.release()


def test_write_guard_blocks_zombie_owner(tmp_path: Path):
    path = tmp_path / "worker.lock"
    old = SingleWorkerLock(path, "A", 0)
    new = SingleWorkerLock(path, "B", 60)
    assert old.acquire()
    old.abandon_for_test()
    assert new.acquire()
    with pytest.raises(RuntimeError, match="OFFICIAL_WRITE_GUARD_FAILED"):
        assert_official_write_guard(old, SNAPSHOT, CONFIG, SNAPSHOT, CONFIG)
    assert new.release()


def test_contention_stress_has_one_owner_per_iteration(tmp_path: Path):
    doubles = 0
    for number in range(100):
        path = tmp_path / f"worker-{number}.lock"
        first = SingleWorkerLock(path, "A", 60)
        second = SingleWorkerLock(path, "B", 60)
        successes = [first.acquire(), second.acquire()]
        doubles += successes.count(True) > 1
        if first.is_owner():
            assert first.release()
        if second.is_owner():
            assert second.release()
    assert doubles == 0