#!/usr/bin/env python3
"""apps/control_api/routers/sources.py — Register discovered OneDrive items."""

from fastapi import APIRouter, HTTPException
from sqlalchemy import insert, select

from database import get_engine
from models import Source
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/v1/sources", tags=["sources"])


class RegisterRequest(BaseModel):
    drive_item_id: str = Field(..., description="OneDrive driveItem ID")
    drive_id: str = Field(..., description="OneDrive drive ID")
    file_path: str = Field(..., description="Full file path from root")
    file_name: str = Field(..., description="File name")
    mime_type: str = Field(..., description="MIME type")
    size_bytes: int = Field(..., ge=0)
    sha256_hash: str = Field(..., length=64)
    status: str = Field("discovered", description="discovered|downloaded|extracted|indexed|quarantine|error")


@router.post("/register", summary="Register a discovered OneDrive item")
async def register_item(payload: RegisterRequest):
    engine = get_engine()
    async with engine.begin() as conn:
        stmt = insert(Source).values(
            drive_item_id=payload.drive_item_id,
            drive_id=payload.drive_id,
            file_path=payload.file_path,
            file_name=payload.file_name,
            mime_type=payload.mime_type,
            size_bytes=payload.size_bytes,
            sha256_hash=payload.sha256_hash,
            status=payload.status,
        )
        await conn.execute(stmt)
    return {"status": "registered", "drive_item_id": payload.drive_item_id}


@router.get("/", summary="List sources by status")
async def list_sources(status: str = "discovered", limit: int = 50):
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(select(Source).where(Source.status == status).limit(limit))
        rows = result.scalars().all()
    return rows


@router.get("/{drive_item_id}", summary="Get source by OneDrive item ID")
async def get_source(drive_item_id: str):
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(select(Source).where(Source.drive_item_id == drive_item_id))
        row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Source not found")
    return row