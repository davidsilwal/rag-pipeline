#!/usr/bin/env python3
"""apps/control_api/routers/system.py — Real health, metrics, alerts, onboarding.

  GET  /health    real PG + Redis + queue checks (plan §10 / G9)
  GET  /metrics   queue depth per stage, dead-letters, workers (plan §10)
  GET  /alerts    computed alert conditions (plan §10)
  POST /onboard   Colab/Deepnote onboarding: creates a worker + returns token/env
"""

from __future__ import annotations

import os
import shutil
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from database import get_engine
from deps import require_admin_token

router = APIRouter(tags=["system"])

HEARTBEAT_TTL_SECONDS = 30


@router.get("/health", summary="Real health check")
async def health():
    checks = {"postgres": False, "redis": False, "disk_space_gb": 0.0, "queue": {}}
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["postgres"] = True
    except Exception:
        pass

    try:
        import redis as redis_lib
        r = redis_lib.Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"), socket_timeout=2)
        checks["redis"] = bool(r.ping())
    except Exception:
        pass

    try:
        usage = shutil.disk_usage("/")
        checks["disk_space_gb"] = round(usage.free / (1024 ** 3), 1)
    except Exception:
        pass

    if checks["postgres"]:
        try:
            engine = get_engine()
            async with engine.connect() as conn:
                rows = (
                    await conn.execute(text("""
                        SELECT stage, count(*) FILTER (WHERE status = 'queued') AS queued,
                               count(*) FILTER (WHERE status = 'claimed') AS claimed,
                               count(*) FILTER (WHERE status = 'dead_letter') AS dead_letter
                        FROM task_queue GROUP BY stage
                    """))
                ).mappings().all()
            checks["queue"] = {r["stage"]: {"queued": r["queued"], "claimed": r["claimed"],
                                            "dead_letter": r["dead_letter"]} for r in rows}
        except Exception:
            pass

    ok = checks["postgres"] and checks["redis"]
    return {"status": "ok" if ok else "degraded", **checks}


@router.get("/metrics", summary="Queue + worker metrics")
async def metrics(_admin: str = Depends(require_admin_token)):
    engine = get_engine()
    async with engine.connect() as conn:
        queue = (
            await conn.execute(text("""
                SELECT stage, status, count(*) AS n
                FROM task_queue GROUP BY stage, status
            """))
        ).mappings().all()
        workers = (
            await conn.execute(text("""
                SELECT status, count(*) AS n FROM workers GROUP BY status
            """))
        ).mappings().all()
        stale_leases = (
            await conn.execute(text("""
                SELECT count(*) AS n FROM task_queue
                WHERE status = 'claimed' AND lease_expires_at < now()
            """))
        ).scalar()
    return {
        "queue_by_stage_status": [dict(r) for r in queue],
        "workers_by_status": [dict(r) for r in workers],
        "stale_leases": stale_leases,
    }


@router.get("/alerts", summary="Computed alert conditions")
async def alerts(_admin: str = Depends(require_admin_token)):
    engine = get_engine()
    conditions = []
    async with engine.connect() as conn:
        # 1. Workers offline > 3× heartbeat TTL.
        offline = (
            await conn.execute(text("""
                SELECT name FROM workers
                WHERE status = 'online'
                  AND last_heartbeat < now() - make_interval(secs => :ttl)
            """), {"ttl": HEARTBEAT_TTL_SECONDS * 3})
        ).scalars().all()
        for name in offline:
            conditions.append({"severity": "warning", "kind": "worker_offline", "worker": name})

        # 2. Task queued > 30 min (starvation / scheduler bug).
        starved = (
            await conn.execute(text("""
                SELECT task_id, stage FROM task_queue
                WHERE status = 'queued' AND created_at < now() - interval '30 minutes'
                LIMIT 20
            """))
        ).mappings().all()
        for r in starved:
            conditions.append({"severity": "warning", "kind": "task_starved",
                               "task_id": str(r["task_id"]), "stage": r["stage"]})

        # 3. Retries > 2 (poison).
        poison = (
            await conn.execute(text("""
                SELECT task_id, stage, attempts FROM task_queue
                WHERE status IN ('failed','queued') AND attempts > 2 LIMIT 20
            """))
        ).mappings().all()
        for r in poison:
            conditions.append({"severity": "warning", "kind": "task_poison",
                               "task_id": str(r["task_id"]), "stage": r["stage"], "attempts": r["attempts"]})

        # 4. Dead letters exist.
        dl = (
            await conn.execute(text("SELECT count(*) FROM task_queue WHERE status = 'dead_letter'"))
        ).scalar()
        if dl:
            conditions.append({"severity": "error", "kind": "dead_letter", "count": dl})

        # 5. Deep queue (scale signal).
        deep = (
            await conn.execute(text("""
                SELECT stage, count(*) AS n FROM task_queue
                WHERE status = 'queued' GROUP BY stage HAVING count(*) > 50
            """))
        ).mappings().all()
        for r in deep:
            conditions.append({"severity": "info", "kind": "queue_depth",
                               "stage": r["stage"], "count": r["n"]})

    return {"alerts": conditions, "count": len(conditions)}


class OnboardRequest(BaseModel):
    name: str = Field(..., description="Worker name, e.g. colab-session-7f")
    platform: str = Field("colab", description="docker | colab | deepnote | rust-thin | bare")
    stages_enabled: list[str] = Field(default_factory=list)
    capabilities: dict = Field(default_factory=dict)


@router.post("/onboard", summary="Onboard an ephemeral worker (Colab/Deepnote)")
async def onboard(payload: OnboardRequest, _admin: str = Depends(require_admin_token)):
    """Create a worker and return its token + env overrides so ephemeral
    runtimes can join without hard-coding VPS IPs/secrets (plan §9 / G8)."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from models import Worker

    new_token = uuid.uuid4()
    engine = get_engine()
    async with engine.begin() as conn:
        stmt = pg_insert(Worker).values(
            name=payload.name,
            platform=payload.platform,
            capabilities=payload.capabilities,
            stages_enabled=payload.stages_enabled,
            concurrency_max=1,
            status="online",
            worker_token=new_token,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[Worker.name],
            set_={
                "platform": payload.platform,
                "capabilities": payload.capabilities,
                "stages_enabled": payload.stages_enabled,
                "status": "online",
                "worker_token": new_token,
                "last_heartbeat": text("now()"),
            },
        ).returning(Worker.worker_id)
        row = (await conn.execute(stmt)).first()
        if row is None:
            raise HTTPException(status_code=409, detail="Onboard conflict")
        worker_id = str(row[0])

    return {
        "worker_id": worker_id,
        "token": str(new_token),
        "env": {
            "WORKER_ID": worker_id,
            "WORKER_TOKEN": str(new_token),
            "TASK_LEASE_TTL": "120",
            "MAX_CONCURRENT_TASKS": "1",
            "CONTROL_API_URL": os.getenv("CONTROL_API_URL", ""),
        },
    }
