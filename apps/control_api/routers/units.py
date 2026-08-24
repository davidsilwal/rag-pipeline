#!/usr/bin/env python3
"""apps/control_api/routers/units.py — Canonical unit registry.

  POST /units/batch   bulk upsert (existing shape: {units:[...]})
  POST /units         thin-worker shape: {source_id, units:[...]} — idempotent
  GET  /?source_id=   list units for a source (used by the notebook worker)
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import insert, select, text
from pydantic import BaseModel, Field
from typing import Optional

from database import get_engine
from deps import require_any_token
from models import Unit

router = APIRouter(prefix="/units", tags=["units"])


class UnitIn(BaseModel):
    # source_id is required only in the legacy /units/batch shape; the thin
    # shape (POST /units) carries it at the top level instead.
    source_id: Optional[str] = None
    doc_id: str
    unit_index: int
    parent_unit_id: Optional[str] = None
    heading_path: list[str] = Field(default_factory=list)
    unit_type: str
    raw_text: str
    clean_text: str
    char_start: Optional[int] = None
    char_end: Optional[int] = None
    page_number: Optional[int] = None
    # JSON column: a single dict (legacy) or a list of per-item bboxes emitted
    # by the Docling chunker for visual grounding.
    bbox_coords: dict | list | None = None
    content_hash: str = Field(..., min_length=64, max_length=64)
    disposition: str = "authoritative"
    quality_score: float = 1.0


class BatchRequest(BaseModel):
    units: list[UnitIn]


class ThinBatchRequest(BaseModel):
    source_id: str
    units: list[UnitIn]


@router.post("/batch", summary="Bulk upsert canonical extracted units")
async def batch_upsert(payload: BatchRequest, _tok: str = Depends(require_any_token)):
    engine = get_engine()
    async with engine.begin() as conn:
        rows = [u.model_dump() for u in payload.units]
        await conn.execute(insert(Unit), rows)
    return {"inserted": len(payload.units)}


@router.post("/", summary="Thin-worker shape: register units for a source (idempotent)")
async def thin_upsert(payload: ThinBatchRequest, _tok: str = Depends(require_any_token)):
    engine = get_engine()
    inserted = 0
    async with engine.begin() as conn:
        src = (
            await conn.execute(
                text("SELECT 1 FROM sources WHERE source_id = :id"), {"id": payload.source_id}
            )
        ).first()
        if not src:
            raise HTTPException(status_code=404, detail="Source not found")
        for u in payload.units:
            result = await conn.execute(
                text("""
                    INSERT INTO units
                        (source_id, doc_id, unit_index, parent_unit_id, heading_path,
                         unit_type, raw_text, clean_text, char_start, char_end,
                         page_number, bbox_coords, content_hash, disposition, quality_score)
                    VALUES
                        (:source_id, :doc_id, :unit_index, :parent_unit_id, CAST(:heading AS text[]),
                         :unit_type, :raw_text, :clean_text, :char_start, :char_end,
                         :page_number, CAST(:bbox AS jsonb), :content_hash, :disposition, :quality_score)
                    ON CONFLICT (source_id, unit_index) DO NOTHING
                    RETURNING unit_id
                """),
                {
                    "source_id": payload.source_id,
                    "doc_id": u.doc_id,
                    "unit_index": u.unit_index,
                    "parent_unit_id": u.parent_unit_id,
                    "heading": u.heading_path,
                    "unit_type": u.unit_type,
                    "raw_text": u.raw_text,
                    "clean_text": u.clean_text,
                    "char_start": u.char_start,
                    "char_end": u.char_end,
                    "page_number": u.page_number,
                    "bbox": u.bbox_coords,
                    "content_hash": u.content_hash,
                    "disposition": u.disposition,
                    "quality_score": u.quality_score,
                },
            )
            if result.first():
                inserted += 1
    return {"inserted": inserted, "total": len(payload.units)}


@router.get("/", summary="List units, optionally filtered by source_id")
async def list_units(source_id: str | None = None, limit: int = Query(1000, ge=1, le=10000)):
    engine = get_engine()
    async with engine.connect() as conn:
        if source_id:
            # Guard: source_id is a UUID column; a non-UUID (e.g. corpus scope)
            # must yield an empty list, never a Postgres 500.
            try:
                uuid.UUID(str(source_id))
            except (TypeError, ValueError):
                return []
            rows = (
                await conn.execute(
                    text("""
                        SELECT * FROM units WHERE source_id = :id ORDER BY unit_index LIMIT :lim
                    """),
                    {"id": source_id, "lim": limit},
                )
            ).mappings().all()
        else:
            rows = (
                await conn.execute(
                    text("SELECT * FROM units ORDER BY created_at DESC LIMIT :lim"), {"lim": limit}
                )
            ).mappings().all()
    return [dict(r) for r in rows]



