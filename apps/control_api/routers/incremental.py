#!/usr/bin/env python3
"""apps/control_api/routers/incremental.py — Incremental update endpoints."""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path as FastAPIPath, Query
from pydantic import BaseModel, Field

from database import get_engine
from deps import require_any_token
from services.incremental import (
    detect_source_changes,
    execute_incremental_update,
    get_stale_sources,
    mark_source_processed,
    plan_incremental_update,
)

log = logging.getLogger("incremental")
router = APIRouter(prefix="/incremental", tags=["incremental"])


class IncrementalUpdateRequest(BaseModel):
    processed_by: str = Field(..., description="Worker ID or name performing the update")
    reembed: bool = Field(True, description="Re-embed new/changed units")
    rededupe: bool = Field(True, description="Run dedup against existing units")
    reextract: bool = Field(False, description="Run GraphRAG extraction (expensive)")
    recluster: bool = Field(True, description="Re-cluster affected nodes only")


@router.get("/stale", summary="List sources needing incremental processing")
async def list_stale_sources(
    _tok: str = Depends(require_any_token),
    limit: int = Query(50, ge=1, le=200),
):
    """Get sources that have changed since last processing."""
    async with get_engine().connect() as conn:
        sources = await get_stale_sources(conn, limit)
        return {"sources": sources, "limit": limit, "count": len(sources)}


@router.get("/plan/{source_id}", summary="Get incremental update plan for a source")
async def get_update_plan(
    source_id: UUID = FastAPIPath(..., description="Source UUID"),
    _tok: str = Depends(require_any_token),
):
    """Plan what needs to be re-processed for an incremental update."""
    async with get_engine().connect() as conn:
        plan = await plan_incremental_update(conn, str(source_id))
        if "error" in plan:
            raise HTTPException(status_code=404, detail=plan["error"])
        return plan


@router.get("/detect/{source_id}", summary="Detect changes in a source")
async def detect_changes(
    source_id: UUID = FastAPIPath(..., description="Source UUID"),
    _tok: str = Depends(require_any_token),
):
    """Detect changes in a source since last processing."""
    async with get_engine().connect() as conn:
        changes = await detect_source_changes(conn, str(source_id))
        if "error" in changes:
            raise HTTPException(status_code=404, detail=changes["error"])
        return changes


@router.post("/execute/{source_id}", summary="Execute incremental update for a source")
async def execute_update(
    source_id: UUID = FastAPIPath(..., description="Source UUID"),
    payload: IncrementalUpdateRequest = ...,
    _tok: str = Depends(require_any_token),
):
    """Execute incremental update for a source."""
    async with get_engine().connect() as conn:
        result = await execute_incremental_update(
            conn,
            str(source_id),
            payload.processed_by,
            reembed=payload.reembed,
            rededupe=payload.rededupe,
            reextract=payload.reextract,
            recluster=payload.recluster,
        )
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return result


@router.post("/mark-processed/{source_id}", summary="Mark source as processed")
async def mark_processed(
    source_id: UUID = FastAPIPath(..., description="Source UUID"),
    processed_by: str = Query(..., description="Worker ID or name"),
    notes: str = Query("", description="Processing notes"),
    _tok: str = Depends(require_any_token),
):
    """Mark a source as processed (increment version)."""
    async with get_engine().connect() as conn:
        result = await mark_source_processed(
            conn,
            str(source_id),
            processed_by,
            notes,
        )
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        return result