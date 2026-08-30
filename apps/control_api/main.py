#!/usr/bin/env python3
"""apps/control_api/main.py — FastAPI Control Plane entry point.

Routers: sources, units, wiki, search, jobs, workers, tasks, embed_cache, system.
Background tasks (lifespan): lease sweeper + corpus scheduler (plan §5/§10).
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from database import get_engine
from routers import sources, units, wiki, search, jobs, workers, tasks, embed_cache, system, export, ingest, dedup, incremental, ask

log = logging.getLogger("control-api")

SWEEPER_INTERVAL_SECONDS = 30
SCHEDULER_INTERVAL_SECONDS = 60
HEARTBEAT_TTL_SECONDS = 30
EXPORT_INTERVAL_SECONDS = 7 * 24 * 3600  # weekly


async def _sweep() -> None:
    """Mark workers offline after 3 missed beats and force-expire their leases
    so the next claim reclaims the work (plan §5)."""
    try:
        engine = get_engine()
        async with engine.begin() as conn:
            await conn.execute(
                text("""
                    UPDATE workers SET status = 'offline'
                    WHERE status = 'online'
                      AND last_heartbeat < now() - make_interval(secs => :ttl)
                """),
                {"ttl": HEARTBEAT_TTL_SECONDS * 3},
            )
            # Force-expire leases held by offline workers and return them to
            # 'queued' so the next claim reclaims them (§5).
            await conn.execute(
                text("""
                    UPDATE task_queue t
                       SET status = 'queued', lease_expires_at = now(),
                           leased_by = NULL, lease_token = NULL, updated_at = now()
                    FROM workers w
                    WHERE t.leased_by = w.worker_id AND w.status = 'offline'
                      AND t.status = 'claimed'
                """),
            )
    except Exception as e:  # pragma: no cover
        log.warning("sweep failed: %s", e)


async def _schedule_corpus() -> None:
    """When sources are indexed and no corpus task is active, enqueue the
    cluster → consensus → graphrag → compile chain (plan §4.5)."""
    try:
        engine = get_engine()
        async with engine.begin() as conn:
            has_indexed = (
                await conn.execute(
                    text("SELECT 1 FROM sources WHERE status IN ('indexed','extracted') LIMIT 1")
                )
            ).first()
            if not has_indexed:
                return
            for stage in ("cluster", "consensus", "graphrag", "compile"):
                active = (
                    await conn.execute(
                        text("""
                            SELECT 1 FROM task_queue
                            WHERE stage = :s AND scope_type = 'corpus'
                              AND status IN ('queued','claimed','running')
                            LIMIT 1
                        """),
                        {"s": stage},
                    )
                ).first()
                if not active:
                    # Enqueue next corpus stage (idempotent via partial unique index).
                    from services.queue import enqueue_stage
                    await enqueue_stage(conn, stage, "corpus", "corpus", priority=40)
                    log.info("enqueued corpus stage %s", stage)
    except Exception as e:  # pragma: no cover
        log.warning("scheduler failed: %s", e)


async def _schedule_export() -> None:
    """Run a weekly full data export and store on disk."""
    try:
        from routers.export import run_scheduled_export
        await run_scheduled_export()
    except Exception as e:  # pragma: no cover
        log.warning("export scheduler failed: %s", e)


async def _background_loop() -> None:
    sweep_count = 0
    export_count = 0
    while True:
        await _sweep()
        await asyncio.sleep(SWEEPER_INTERVAL_SECONDS)
        sweep_count += 1
        # Run corpus scheduler every SWEEPER_INTERVAL
        await _schedule_corpus()
        # Run export scheduler weekly (every EXPORT_INTERVAL / SWEEPER_INTERVAL sweeps)
        export_count += SWEEPER_INTERVAL_SECONDS
        if export_count >= EXPORT_INTERVAL_SECONDS:
            export_count = 0
            await _schedule_export()
        await asyncio.sleep(SCHEDULER_INTERVAL_SECONDS - SWEEPER_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_background_loop())
    log.info("background sweeper/scheduler started")
    yield
    task.cancel()


app = FastAPI(
    title="LLM Markdown Wiki Control API",
    version="2.3",
    description="Control API for the LLM Markdown Wiki Pipeline backend (multi-worker).",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Explicit route registration to avoid router import aliasing issues.
app.include_router(sources.router, prefix="/api/v1", tags=["sources"])
app.include_router(units.router, prefix="/api/v1", tags=["units"])
app.include_router(wiki.router, prefix="/api/v1", tags=["wiki"])
app.include_router(search.router, prefix="/api/v1", tags=["search"])
app.include_router(jobs.router, prefix="/api/v1", tags=["jobs"])
app.include_router(workers.router, prefix="/api/v1", tags=["workers"])
app.include_router(tasks.router, prefix="/api/v1", tags=["tasks"])
app.include_router(embed_cache.router, prefix="/api/v1", tags=["embed_cache"])
app.include_router(system.router, prefix="/api/v1", tags=["system"])
app.include_router(export.router, prefix="/api/v1", tags=["export"])
app.include_router(ingest.router, prefix="/api/v1", tags=["ingest"])
app.include_router(dedup.router, prefix="/api/v1", tags=["dedup"])
app.include_router(incremental.router, prefix="/api/v1", tags=["incremental"])
app.include_router(ask.router, prefix="/api/v1", tags=["ask"])


# --- OpenAPI patch: global security so Swagger "Authorize" button appears ----
_original_openapi = app.openapi


def _patched_openapi():
    schema = _original_openapi()
    comps = schema.setdefault("components", {})
    comps.setdefault("securitySchemes", {}).setdefault(
        "HTTPBearer", {"type": "http", "scheme": "bearer"}
    )
    schema.setdefault("security", [{"HTTPBearer": []}])
    return schema


app.openapi = _patched_openapi

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
