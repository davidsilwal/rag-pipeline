#!/usr/bin/env python3
"""apps/control_api/deps.py — Shared auth + database dependencies.

Two credential types are accepted on mutating endpoints (plan §13):
  * API_TOKEN            — the control-plane admin/bearer token (config.api_token)
  * worker_token         — a per-worker UUID issued at /workers/register

Read-only dashboard endpoints may also be left open where noted, but every
mutation is guarded by one of the two above.
"""

from __future__ import annotations

import uuid

from fastapi import Depends, Header, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config import config

# Swagger scheme — the "Authorize" button uses this.  The raw header
# parameter is hidden from the OpenAPI spec so users can only auth via
# the Authorize button (not a broken text input field).
_bearer_scheme = HTTPBearer(auto_error=False)


def _parse_bearer(raw: str | None) -> str:
    """Extract the token from a ``Bearer <token>`` header value or a bare token."""
    if not raw:
        return ""
    parts = raw.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return raw.strip()


async def require_admin_token(
    creds: HTTPAuthorizationCredentials | None = Security(_bearer_scheme),
) -> str:
    """Require the control-plane API_TOKEN (admin-level)."""
    token = _parse_bearer(creds.credentials if creds else None)
    if not config.api_token or token != config.api_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API token",
        )
    return token


async def optional_worker_token(
    creds: HTTPAuthorizationCredentials | None = Security(_bearer_scheme),
) -> uuid.UUID | None:
    """Parse a worker token if present; returns None otherwise (caller decides)."""
    token = _parse_bearer(creds.credentials if creds else None)
    if not token:
        return None
    try:
        return uuid.UUID(token)
    except ValueError:
        return None


async def require_any_token(
    creds: HTTPAuthorizationCredentials | None = Security(_bearer_scheme),
) -> str:
    """Accept either the admin API_TOKEN or a valid worker token."""
    token = _parse_bearer(creds.credentials if creds else None)
    if not token:
        raise HTTPException(status_code=401, detail="Missing token")
    if config.api_token and token == config.api_token:
        return token
    try:
        uuid.UUID(token)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid token")
    # Verify it's a known worker token.
    from database import get_engine
    from sqlalchemy import text
    engine = get_engine()
    async with engine.connect() as conn:
        row = (await conn.execute(
            text("SELECT 1 FROM workers WHERE worker_token = :tok"), {"tok": token}
        )).first()
    if not row:
        raise HTTPException(status_code=401, detail="Invalid token")
    return token
