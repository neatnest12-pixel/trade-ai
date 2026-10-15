"""Minimal private-dashboard API authentication and process-local rate limiting."""
from __future__ import annotations

import hmac
import time
from collections import defaultdict, deque

from fastapi import Header, HTTPException, Request, status

from app.core.config import get_settings

_requests: dict[str, deque[float]] = defaultdict(deque)


def require_private_api(request: Request, x_navora_api_key: str | None = Header(default=None)) -> None:
    settings = get_settings()
    if not settings.api_auth_required:
        return
    if not settings.navora_api_secret or not x_navora_api_key or not hmac.compare_digest(x_navora_api_key, settings.navora_api_secret):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    now = time.monotonic()
    key = request.client.host if request.client else "unknown"
    hits = _requests[key]
    while hits and hits[0] <= now - 60:
        hits.popleft()
    if len(hits) >= settings.api_rate_limit_per_minute:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Rate limit exceeded")
    hits.append(now)