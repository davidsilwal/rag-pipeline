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


@router.get("/by-source", summary="Bulk: source metadata for graphrag")
async def units_by_source(
    min_chars: int = Query(300, ge=0),
    limit: int = Query(10000, ge=1, le=100000),
):
    """Return one row per source with unit_ids and char counts.
    Text is NOT returned here to keep the query fast; the worker
    fetches text per-batch via the existing ``?source_id=`` filter.
    """
    engine = get_engine()
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text("""
                    SELECT
                        source_id,
                        SUM(length(clean_text)) AS total_chars,
                        COUNT(*) AS unit_count,
                        array_agg(unit_id ORDER BY unit_index) AS unit_ids
                    FROM units
                    WHERE clean_text IS NOT NULL
                    GROUP BY source_id
                    HAVING SUM(length(COALESCE(clean_text, ''))) >= :min_chars
                    ORDER BY SUM(length(clean_text)) DESC
                    LIMIT :lim
                """),
                {"min_chars": min_chars, "lim": limit},
            )
        ).mappings().all()

    return [{
        "source_id": str(r["source_id"]),
        "unit_ids": [str(u) for u in (r["unit_ids"] or [])],
        "unit_count": r["unit_count"],
        "total_chars": r["total_chars"],
    } for r in rows]


@router.get("/text-batch", summary="Bulk: fetch combined text for multiple sources")
async def text_batch(
    source_ids: str = Query(..., commaSeparated=True),
    max_chars_per_source: int = Query(2000, ge=100, le=20000),
):
    """Return combined clean_text for a list of source_ids in one SQL call.
    ``source_ids`` is a comma-separated list of UUIDs.
    """
    ids = [s.strip() for s in source_ids.split(",") if s.strip()]
    if not ids:
        return []
    # Validate UUIDs to prevent injection
    import uuid as _uuid
    valid_ids = []
    for i in ids:
        try:
            valid_ids.append(_uuid.UUID(i))
        except (TypeError, ValueError):
            pass
    if not valid_ids:
        return []
    engine = get_engine()
    placeholders = ",".join(f":id{j}" for j in range(len(valid_ids)))
    params = {f"id{j}": v for j, v in enumerate(valid_ids)}
    params["max_chars"] = max_chars_per_source
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(f"""
                    SELECT
                        source_id,
                        SUM(length(clean_text)) AS total_chars,
                        array_agg(unit_id ORDER BY unit_index) AS unit_ids,
                        LEFT(string_agg(
                            CASE WHEN clean_text IS NOT NULL THEN clean_text ELSE '' END,
                            ' ' ORDER BY unit_index
                        ), :max_chars) AS combined_text
                    FROM units
                    WHERE source_id IN ({placeholders})
                      AND clean_text IS NOT NULL
                    GROUP BY source_id
                """),
                params,
            )
        ).mappings().all()

    return [{
        "source_id": str(r["source_id"]),
        "unit_ids": [str(u) for u in (r["unit_ids"] or [])],
        "total_chars": r["total_chars"],
        "combined_text": r["combined_text"] or "",
    } for r in rows]


@router.get("/unembedded", summary="Fetch units not yet in embed_cache (paginated)")
async def unembedded_units(
    limit: int = Query(5000, ge=1, le=50000),
    offset: int = Query(0, ge=0),
    _token=None,
):
    """Return units whose content_hash is NOT in embed_cache.
    This lets the worker embed many sources in one task instead of one-at-a-time."""
    engine = get_engine()
    async with engine.connect() as conn:
        rows = (await conn.execute(
            text("""
                SELECT u.unit_id, u.doc_id AS source_id, u.content_hash, u.clean_text, u.unit_index
                FROM units u
                LEFT JOIN embed_cache ec ON ec.content_hash = u.content_hash
                WHERE u.clean_text IS NOT NULL AND u.clean_text <> ''
                  AND ec.content_hash IS NULL
                ORDER BY u.doc_id, u.unit_index
                LIMIT :lim OFFSET :off
            """),
            {"lim": limit, "off": offset},
        )).mappings().all()
        total_row = (await conn.execute(
            text("""
                SELECT count(*) AS n
                FROM units u
                LEFT JOIN embed_cache ec ON ec.content_hash = u.content_hash
                WHERE u.clean_text IS NOT NULL AND u.clean_text <> ''
                  AND ec.content_hash IS NULL
            """),
        )).first()
    total = total_row[0] if total_row else 0
    return {
        "units": [{
            "unit_id": str(r["unit_id"]),
            "source_id": str(r["source_id"]),
            "content_hash": r["content_hash"],
            "clean_text": r["clean_text"],
            "unit_index": r["unit_index"],
        } for r in rows],
        "total": total,
        "offset": offset,
        "limit": limit,
    }

