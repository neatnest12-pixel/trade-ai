"""Fail-closed worker shutdown helpers for V3.5 diagnostic workers."""
from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from app.v3_5.state_io import assert_owner, read_text_shared


def finalize_worker(
    *,
    owner: dict[str, Any],
    lock_path: Path,
    cleanup: Iterable[tuple[str, Callable[[], None]]],
    publish_terminal: Callable[[list[dict[str, str]]], None],
    record_event: Callable[[dict[str, Any]], None],
) -> list[dict[str, str]]:
    """Close protected resources, publish terminal state, then release own lock.

    A failed terminal-state replacement deliberately leaves its complete temp
    file and retains the lock. Cleanup failures are surfaced in terminal state
    and lifecycle diagnostics without replacing a worker's primary failure.
    """
    errors: list[dict[str, str]] = []

    def record(event: dict[str, Any]) -> None:
        try:
            record_event(event)
        except Exception:
            logging.exception("V3.5 worker lifecycle diagnostic could not be persisted: %s", event)

    for name, action in cleanup:
        try:
            action()
        except BaseException as exc:  # cleanup must continue for remaining resources
            errors.append({"operation": name, "error": f"{type(exc).__name__}:{exc}"})

    try:
        publish_terminal(errors)
    except BaseException as exc:
        message = f"{type(exc).__name__}:{exc}"
        record({"event": "WORKER_TERMINAL_STATE_WRITE_FAILED", "worker_id": owner.get("worker_id"),
                "pid": owner.get("pid"), "error": message, "cleanup_errors": errors})
        # Do not release the lock: canonical state is still non-terminal and
        # the preserved temp is evidence for an explicit recovery decision.
        return errors + [{"operation": "publish_terminal_state", "error": message}]

    try:
        if lock_path.exists():
            assert_owner(lock_path, owner)
            current = json.loads(read_text_shared(lock_path))
            if current.get("pid") != owner.get("pid") or current.get("worker_id") != owner.get("worker_id"):
                raise RuntimeError("V35_LOCK_OWNERSHIP_LOST")
            lock_path.unlink()
    except BaseException as exc:
        message = f"{type(exc).__name__}:{exc}"
        errors.append({"operation": "release_owned_lock", "error": message})
        record({"event": "WORKER_LOCK_RELEASE_FAILED", "worker_id": owner.get("worker_id"),
                "pid": owner.get("pid"), "error": message, "cleanup_errors": errors})
        # If the lock is still provably ours, make the cleanup failure visible
        # in canonical state too. Never update state after an ownership change.
        try:
            assert_owner(lock_path, owner)
            publish_terminal(errors)
        except BaseException as state_exc:
            record({"event": "WORKER_CLEANUP_ERROR_STATE_UPDATE_FAILED",
                    "worker_id": owner.get("worker_id"), "pid": owner.get("pid"),
                    "error": f"{type(state_exc).__name__}:{state_exc}", "cleanup_errors": errors})
    return errors
