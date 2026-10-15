"""Canonical typed configuration for the broker's MT5 server UTC offset."""
from __future__ import annotations

from pydantic import ValidationError

from app.core.config import Settings, get_settings

OFFSET_ENV = "V3_MT5_SERVER_UTC_OFFSET_SECONDS"


def configured_offset_seconds(settings: Settings | None = None) -> int:
    """Return the explicitly configured broker offset or fail closed."""
    try:
        offset = (settings or get_settings()).v3_mt5_server_utc_offset_seconds
    except ValidationError as exc:
        raise RuntimeError(f"{OFFSET_ENV}_INVALID") from exc
    if offset is None:
        raise RuntimeError(f"{OFFSET_ENV}_REQUIRED")
    return offset
