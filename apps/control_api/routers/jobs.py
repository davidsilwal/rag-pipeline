#!/usr/bin/env python3
"""apps/control_api/routers/jobs.py — Checkpointing & job progress."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from datetime import datetime

from database import get_engine
from models import PipelineJob

router = APIRouter(prefix="/jobs", tags=["jobs"])


class CheckpointRequest(BaseModel):
    job_type: str
    status: str = "running"
    items_processed: int = 0
    items_failed: int = 0
    log_summary: str | None = None
    fingerprint: str | None = None


@router.post("/checkpoint", summary="Update job progress & lease heartbeat")
async def checkpoint(payload: CheckpointRequest):
    engine = get_engine()
    async with engine.begin() as conn:
        from sqlalchemy import insert
        stmt = insert(PipelineJob).values(
            job_type=payload.job_type,
            status=payload.status,
            items_processed=payload.items_processed,
            items_failed=payload.items_failed,
            log_summary=payload.log_summary,
            fingerprint=payload.fingerprint,
        )
        await conn.execute(stmt)
    return {"status": "checkpointed"}


@router.get("/", summary="List job runs")
async def list_jobs():
    engine = get_engine()
    async with engine.connect() as conn:
        from sqlalchemy import select
        result = await conn.execute(select(PipelineJob).order_by(PipelineJob.started_at.desc()))
        rows = result.mappings().all()
    return [dict(r) for r in rows]