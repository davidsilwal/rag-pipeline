#!/usr/bin/env python3
"""apps/control_api/routers/tasks.py — Durable task queue (plan §4–§6).

  POST /tasks/claim            {worker_id, stages|stage, max_tasks, long_poll}
  POST /tasks/{id}/heartbeat   {lease_token}          → extend TTL
  POST /tasks/{id}/complete    {lease_token, result_meta}
  POST /tasks/{id}/fail        {lease_token, error_message, will_retry}
  POST /tasks/{id}/requeue     admin: dead_letter → queued
  GET  /tasks                  filters: stage, status, worker

Claim uses `FOR UPDATE SKIP LOCKED` so concurrent workers never double-assign.
Every claim/complete/fail is token-guarded (at-least-once, §2.1).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError

from database import get_engine
from deps import optional_worker_token, require_admin_token, require_any_token
from services.capabilities import stage_eligible
from services.queue import enqueue_stage

router = APIRouter(prefix="/tasks", tags=["tasks"])

# Plan §5 claim TTLs per stage (seconds).
STAGE_TTLS = {
    "discover": 15 * 60,
    "extract": 15 * 60,
    "chunk": 15 * 60,
    "embed": 10 * 60,
    "dedup": 10 * 60,
    "cluster": 60 * 60,
    "consensus": 20 * 60,
    "graphrag": 20 * 60,
    "compile": 20 * 60,
}
DEFAULT_TTL = 10 * 60

# Producer chaining: stage completion → downstream stage (plan §4.5).
NEXT_STAGE = {
    "extract": "chunk",
    "chunk": "embed",
    "embed": "dedup",
    "cluster": "consensus",
    "consensus": "graphrag",
    "graphrag": "compile",
}

# How long a claim request may block waiting for work (long_poll).
LONG_POLL_MAX_SECONDS = 25


class ClaimRequest(BaseModel):
    worker_id: uuid.UUID
    stages: list[str] = Field(default_factory=list)
    stage: str | None = None
    max_tasks: int = Field(1, ge=1, le=64)
    long_poll: bool = Field(False)


class HeartbeatRequest(BaseModel):
    lease_token: uuid.UUID


class CompleteRequest(BaseModel):
    lease_token: uuid.UUID
    result_meta: dict = Field(default_factory=dict)


class FailRequest(BaseModel):
    lease_token: uuid.UUID
    error_message: str = ""
    will_retry: bool = Field(True)


async def _load_worker(conn, worker_id: uuid.UUID, token: uuid.UUID | None):
    """Load a worker; verify it exists, is online, and (if token given) matches."""
    row = (
        await conn.execute(
            text("SELECT status, stages_enabled, capabilities, worker_token FROM workers WHERE worker_id = :wid"),
            {"wid": worker_id},
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Worker not found")
    status_, stages, caps, worker_token = row
    if status_ == "offline":
        raise HTTPException(status_code=403, detail="Worker is offline")
    if token is not None and worker_token is not None and token != worker_token:
        raise HTTPException(status_code=401, detail="Worker token mismatch")
    return {"stages": stages or [], "capabilities": caps or {}}


@router.post("/claim", summary="Claim tasks (race-free, SKIP LOCKED)")
async def claim(
    payload: ClaimRequest,
    token: uuid.UUID | None = Depends(optional_worker_token),
):
    stages = payload.stages or ([payload.stage] if payload.stage else [])

    async with get_engine().begin() as conn:
        worker = await _load_worker(conn, payload.worker_id, token)
        eligible = [s for s in stages if s in (worker["stages"] or []) and stage_eligible(worker["capabilities"], s)]
        if not eligible:
            return []
        return await _claim_once(conn, payload.worker_id, eligible, payload.max_tasks)


async def _claim_once(conn, worker_id: uuid.UUID, stages: list[str], max_tasks: int) -> list[dict]:
    """One SKIP LOCKED claim pass across the requested stages."""
    out: list[dict] = []
    for stage in stages:
        if len(out) >= max_tasks:
            break
        ttl = STAGE_TTLS.get(stage, DEFAULT_TTL)
        sql = text("""
            WITH candidate AS (
                SELECT task_id FROM task_queue
                WHERE stage = :stage
                  AND next_run_at <= now()
                  AND (
                      status = 'queued'
                      -- Lazy reclaim: a claimed task whose lease expired (crashed
                      -- / offline worker) is picked up by the next claim (§4.4).
                      OR (status = 'claimed' AND lease_expires_at IS NOT NULL
                          AND lease_expires_at < now())
                  )
                ORDER BY priority, created_at
                LIMIT :lim
                FOR UPDATE SKIP LOCKED
            )
            UPDATE task_queue t
               SET status = 'claimed',
                   leased_by = :wid,
                   lease_token = gen_random_uuid(),
                   lease_expires_at = now() + make_interval(secs => :ttl),
                   attempts = attempts + 1,
                   started_at = now(),
                   updated_at = now()
              FROM candidate c
             WHERE t.task_id = c.task_id
            RETURNING t.task_id, t.stage, t.scope_type, t.scope_id,
                      t.payload, t.lease_token, t.lease_expires_at, t.attempts
        """)
        rows = (
            await conn.execute(sql, {"stage": stage, "wid": worker_id, "lim": max_tasks - len(out), "ttl": ttl})
        ).mappings().all()
        for r in rows:
            out.append({
                "task_id": str(r["task_id"]),
                "stage": r["stage"],
                "scope_type": r["scope_type"],
                "scope_id": r["scope_id"],
                "payload": r["payload"] or {},
                "lease_token": str(r["lease_token"]),
                "lease_expires_at": r["lease_expires_at"].isoformat() if r["lease_expires_at"] else None,
                "attempts": r["attempts"],
            })
    return out


@router.post("/{task_id}/heartbeat", summary="Extend a task lease")
async def task_heartbeat(task_id: uuid.UUID, payload: HeartbeatRequest, _tok: str = Depends(require_any_token)):
    engine = get_engine()
    async with engine.begin() as conn:
        ttl = DEFAULT_TTL
        row = (
            await conn.execute(
                select(text("stage")).select_from(text("task_queue")).where(text("task_id = :id")).params(id=task_id)
            )
        ).first()
        if row:
            ttl = STAGE_TTLS.get(row[0], DEFAULT_TTL)
        result = await conn.execute(
            text("""
                UPDATE task_queue
                   SET lease_expires_at = now() + make_interval(secs => :ttl), updated_at = now()
                 WHERE task_id = :id AND lease_token = :tok AND status = 'claimed'
            """),
            {"id": task_id, "tok": payload.lease_token, "ttl": ttl},
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=409, detail="Stale or unknown lease")
    return {"status": "extended", "lease_expires_at": (datetime.utcnow() + timedelta(seconds=ttl)).isoformat()}


@router.post("/{task_id}/complete", summary="Mark a task succeeded (token-guarded)")
async def complete(
    task_id: uuid.UUID,
    payload: CompleteRequest,
    token: uuid.UUID | None = Depends(optional_worker_token),
):
    engine = get_engine()
    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                UPDATE task_queue
                   SET status = 'succeeded', result_meta = CAST(:meta AS jsonb),
                       completed_at = now(), updated_at = now(),
                       lease_token = NULL, lease_expires_at = NULL
                 WHERE task_id = :id AND lease_token = :tok AND status = 'claimed'
                RETURNING stage, scope_type, scope_id
            """),
            {"id": task_id, "tok": payload.lease_token, "meta": _json_dumps(payload.result_meta)},
        )
        row = result.first()
        if row is None:
            raise HTTPException(status_code=409, detail="Stale or unknown lease")
        stage, scope_type, scope_id = row

        # Producer chaining: enqueue the downstream stage (idempotent).
        next_stage = NEXT_STAGE.get(stage)
        if next_stage and scope_type == "source":
            await enqueue_stage(conn, next_stage, scope_type, scope_id)
        # Reflect source-level progress on the row itself (observability).
        if scope_type == "source":
            await _touch_source_status(conn, scope_id, stage)
    return {"status": "succeeded"}


async def _touch_source_status(conn, source_id: str, stage: str) -> None:
    """Advance a source's status as its stages complete (best-effort)."""
    mapping = {"extract": "extracted", "embed": "indexed"}
    new_status = mapping.get(stage)
    if not new_status:
        return
    try:
        await conn.execute(
            text("UPDATE sources SET status = :s, updated_at = now() WHERE source_id = :id"),
            {"s": new_status, "id": source_id},
        )
    except Exception:
        pass


@router.post("/{task_id}/fail", summary="Record failure; retry with backoff or dead-letter")
async def fail(
    task_id: uuid.UUID,
    payload: FailRequest,
    token: uuid.UUID | None = Depends(optional_worker_token),
):
    engine = get_engine()
    async with engine.begin() as conn:
        row = (
            await conn.execute(
                text("""
                    UPDATE task_queue
                       SET status = 'failed', error_message = :err, updated_at = now(),
                           lease_token = NULL, lease_expires_at = NULL
                     WHERE task_id = :id AND lease_token = :tok AND status = 'claimed'
                    RETURNING attempts, max_attempts, stage, scope_type, scope_id
                """),
                {"id": task_id, "tok": payload.lease_token, "err": payload.error_message},
            )
        ).first()
        if row is None:
            raise HTTPException(status_code=409, detail="Stale or unknown lease")
        attempts, max_attempts, stage, scope_type, scope_id = row

        # stage_not_supported (§9A.4): re-queue for an eligible worker, never
        # dead-letter a config mismatch silently.
        if payload.error_message.startswith("stage_not_supported"):
            await conn.execute(
                text("""
                    UPDATE task_queue
                       SET status = 'queued', next_run_at = now(), error_message = :err,
                           attempts = attempts - 1, updated_at = now()
                     WHERE task_id = :id
                """),
                {"id": task_id, "err": payload.error_message},
            )
            return {"status": "requeued", "reason": "stage_not_supported"}

        if attempts >= max_attempts or not payload.will_retry:
            await conn.execute(
                text("UPDATE task_queue SET status = 'dead_letter', updated_at = now() WHERE task_id = :id"),
                {"id": task_id},
            )
            return {"status": "dead_letter", "attempts": attempts, "max_attempts": max_attempts}

        # Exponential backoff: next_run_at = now() + 2^attempts (capped 5 min).
        backoff = min(2 ** min(attempts, 8), 300)
        await conn.execute(
            text("""
                UPDATE task_queue
                   SET status = 'queued', next_run_at = now() + make_interval(secs => :bo),
                       updated_at = now()
                 WHERE task_id = :id
            """),
            {"id": task_id, "bo": backoff},
        )
        return {"status": "queued", "retry_in_seconds": backoff}


@router.post("/{task_id}/requeue", summary="Admin: replay a dead-lettered task")
async def requeue(task_id: uuid.UUID, _admin: str = Depends(require_admin_token)):
    engine = get_engine()
    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                UPDATE task_queue
                   SET status = 'queued', next_run_at = now(), attempts = 0,
                       error_message = NULL, updated_at = now()
                 WHERE task_id = :id AND status = 'dead_letter'
            """),
            {"id": task_id},
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Task not found or not dead-lettered")
    return {"status": "requeued"}


@router.get("/", summary="List tasks with filters")
async def list_tasks(
    stage: str | None = None,
    status: str | None = None,
    worker_id: uuid.UUID | None = None,
    limit: int = Query(100, ge=1, le=1000),
    _admin: str = Depends(require_admin_token),
):
    engine = get_engine()
    sql = "SELECT * FROM task_queue WHERE 1=1"
    params: dict = {"lim": limit}
    if stage:
        sql += " AND stage = :stage"
        params["stage"] = stage
    if status:
        sql += " AND status = :status"
        params["status"] = status
    if worker_id:
        sql += " AND leased_by = :wid"
        params["wid"] = worker_id
    sql += " ORDER BY created_at DESC LIMIT :lim"
    async with engine.connect() as conn:
        rows = (await conn.execute(text(sql), params)).mappings().all()
    return [dict(r) for r in rows]


def _json_dumps(obj) -> str:
    import json
    return json.dumps(obj, default=str)
