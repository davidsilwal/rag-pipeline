#!/usr/bin/env python3
"""apps/control_api/routers/workers.py — Worker registry (plan §3).

POST /workers/register            → worker_id + worker_token (secret)
POST /workers/{id}/heartbeat      → refresh liveness + load; returns config flags
POST /workers/{id}/deregister     → graceful drain
GET  /workers                     → dashboard list
GET  /workers/{id}                → detail view
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from database import get_engine
from deps import require_admin_token, require_any_token
from models import Worker

router = APIRouter(prefix="/workers", tags=["workers"])

# Heartbeat TTL (seconds). Offline after 3 missed beats (plan §3.2).
HEARTBEAT_TTL_SECONDS = 30


class RegisterRequest(BaseModel):
    name: str = Field(..., description="Unique worker name, e.g. gpu-box-01")
    platform: str = Field("bare", description="docker | colab | deepnote | rust-thin | bare")
    hostname: str | None = None
    version: str | None = None
    capabilities: dict = Field(default_factory=dict)
    stages_enabled: list[str] = Field(default_factory=list)
    concurrency_max: int = Field(1, ge=1, le=64)


class HeartbeatRequest(BaseModel):
    load: dict = Field(default_factory=dict, description="{running, queue_len}")
    capabilities: dict | None = Field(None, description="optional refresh")


@router.post("/register", summary="Register a worker; returns worker_id + token")
async def register(payload: RegisterRequest, _admin: str = Depends(require_admin_token)):
    engine = get_engine()
    new_token = uuid.uuid4()
    async with engine.begin() as conn:
        stmt = pg_insert(Worker).values(
            name=payload.name,
            platform=payload.platform,
            hostname=payload.hostname,
            version=payload.version,
            capabilities=payload.capabilities,
            stages_enabled=payload.stages_enabled,
            concurrency_max=payload.concurrency_max,
            status="online",
            worker_token=new_token,
        )
        # Re-registering the same name refreshes the row and issues a NEW token
        # (old token dies — safe for rejoin-on-boot).
        stmt = stmt.on_conflict_do_update(
            index_elements=[Worker.name],
            set_={
                "platform": payload.platform,
                "hostname": payload.hostname,
                "version": payload.version,
                "capabilities": payload.capabilities,
                "stages_enabled": payload.stages_enabled,
                "concurrency_max": payload.concurrency_max,
                "status": "online",
                "last_heartbeat": func.now(),
                "worker_token": new_token,
            },
        ).returning(Worker.worker_id)
        row = (await conn.execute(stmt)).first()
        if row is None:
            raise HTTPException(status_code=409, detail="Worker registration conflict")
    return {"worker_id": str(row[0]), "token": str(new_token)}


@router.post("/{worker_id}/heartbeat", summary="Refresh liveness + load")
async def heartbeat(worker_id: uuid.UUID, payload: HeartbeatRequest, _tok: str = Depends(require_any_token)):
    engine = get_engine()
    async with engine.begin() as conn:
        stmt = update(Worker).where(Worker.worker_id == worker_id).values(last_heartbeat=func.now())
        if payload.capabilities is not None:
            stmt = stmt.values(capabilities=payload.capabilities)
        result = await conn.execute(stmt)
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Worker not found")

        # Return updated config / feature flags the worker should honor.
        row = (
            await conn.execute(
                select(Worker.status, Worker.concurrency_max, Worker.stages_enabled)
                .where(Worker.worker_id == worker_id)
            )
        ).first()
    return {
        "status": row[0],
        "concurrency_max": row[1],
        "stages_enabled": row[2],
        "heartbeat_ttl_seconds": HEARTBEAT_TTL_SECONDS,
    }


@router.post("/{worker_id}/deregister", summary="Graceful shutdown")
async def deregister(worker_id: uuid.UUID, _tok: str = Depends(require_any_token)):
    engine = get_engine()
    async with engine.begin() as conn:
        result = await conn.execute(
            update(Worker).where(Worker.worker_id == worker_id).values(status="offline")
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Worker not found")
    return {"status": "deregistered"}


@router.get("/", summary="List workers (dashboard)")
async def list_workers(_admin: str = Depends(require_admin_token)):
    engine = get_engine()
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text("""
                    SELECT w.*,
                           EXTRACT(EPOCH FROM (now() - w.last_heartbeat))::int AS heartbeat_age_seconds,
                           (SELECT count(*) FROM task_queue t
                             WHERE t.leased_by = w.worker_id AND t.status = 'claimed') AS running_tasks
                    FROM workers w
                    ORDER BY w.last_heartbeat DESC
                """)
            )
        ).mappings().all()
    return [dict(r) for r in rows]


@router.get("/{worker_id}", summary="Worker detail")
async def get_worker(worker_id: uuid.UUID, _admin: str = Depends(require_admin_token)):
    engine = get_engine()
    async with engine.connect() as conn:
        row = (
            await conn.execute(select(Worker).where(Worker.worker_id == worker_id))
        ).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Worker not found")
    return row
