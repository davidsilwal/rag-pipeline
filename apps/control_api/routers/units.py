#!/usr/bin/env python3
"""apps/control_api/routers/units.py — Bulk upsert of canonical extracted units."""

from fastapi import APIRouter
from sqlalchemy import insert
from pydantic import BaseModel, Field
from typing import Optional

from database import get_engine
from models import Unit

router = APIRouter(prefix="/units", tags=["units"])


class UnitIn(BaseModel):
    source_id: str
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
    bbox_coords: Optional[dict] = None
    content_hash: str = Field(..., length=64)
    disposition: str = "authoritative"
    quality_score: float = 1.0


class BatchRequest(BaseModel):
    units: list[UnitIn]


@router.post("/batch", summary="Bulk upsert canonical extracted units")
async def batch_upsert(payload: BatchRequest):
    engine = get_engine()
    async with engine.begin() as conn:
        rows = [u.model_dump() for u in payload.units]
        await conn.execute(insert(Unit), rows)
    return {"inserted": len(payload.units)}