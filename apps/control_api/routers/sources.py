#!/usr/bin/env python3
"""apps/control_api/routers/sources.py — Source registry + content access (plan §8/§13).

  POST /sources/register            idempotent; returns {source_id} (chaining → extract)
  GET  /sources/?status=&limit=     list by status
  GET  /sources/{drive_item_id}     get by OneDrive item id (legacy)
  HEAD /sources/by-hash/{sha256}    200 known / 404 new (upload dedup)
  GET  /sources/by-id/{source_id}   source metadata (thin client)
  POST /sources/{source_id}/blob    store raw bytes
  GET  /sources/{source_id}/blob    fetch raw bytes
  POST /sources/{source_id}/text    store extracted plain text
  GET  /sources/{source_id}/text    fetch extracted plain text
  POST /sources/{source_id}/status  guarded status transition
"""

from __future__ import annotations

import uuid
import json

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, text

from database import get_engine
from deps import require_any_token
from models import Source
from services.queue import enqueue_stage

router = APIRouter(prefix="/sources", tags=["sources"])


async def _get_engine():
    return get_engine()


async def _register_source(payload: RegisterRequest, conn=None) -> dict:
    if conn is None:
        engine = get_engine()
        async with engine.begin() as conn:
            return await _register_source(payload, conn=conn)

    result = await conn.execute(
        text("""
            INSERT INTO sources
                (drive_item_id, drive_id, source_type, source_url, file_path, file_name, mime_type,
                 size_bytes, sha256_hash, status, source_metadata)
            VALUES (:drive_item_id, :drive_id, :source_type, :source_url, :file_path, :file_name, :mime_type,
                    :size_bytes, :sha256_hash, :status, :source_metadata)
            ON CONFLICT (drive_item_id) DO UPDATE SET
                drive_id = EXCLUDED.drive_id,
                source_type = EXCLUDED.source_type,
                source_url = EXCLUDED.source_url,
                file_path = EXCLUDED.file_path,
                file_name = EXCLUDED.file_name,
                mime_type = EXCLUDED.mime_type,
                size_bytes = EXCLUDED.size_bytes,
                sha256_hash = EXCLUDED.sha256_hash,
                source_metadata = EXCLUDED.source_metadata,
                updated_at = now()
            RETURNING source_id
        """),
        {
            "drive_item_id": payload.drive_item_id,
            "drive_id": payload.drive_id,
            "source_type": payload.source_type,
            "source_url": payload.source_url,
            "file_path": payload.file_path,
            "file_name": payload.file_name,
            "mime_type": payload.mime_type,
            "size_bytes": payload.size_bytes,
            "sha256_hash": payload.sha256_hash,
            "status": payload.status,
            "source_metadata": json.dumps(payload.source_metadata or {}),
        },
    )
    row = result.first()
    source_id = str(row[0])
    await enqueue_stage(conn, "extract", "source", source_id, priority=0)
    return {"status": "registered", "source_id": source_id, "drive_item_id": payload.drive_item_id}


class RegisterRequest(BaseModel):
    drive_item_id: str | None = Field(None, description="OneDrive driveItem ID")
    drive_id: str | None = Field(None)
    file_path: str = Field(...)
    file_name: str = Field(...)
    mime_type: str = Field(...)
    size_bytes: int = Field(..., ge=0)
    sha256_hash: str = Field(..., min_length=64, max_length=64)
    status: str = Field("discovered")
    source_type: str = Field("local", description="local|github|onedrive")
    source_url: str | None = Field(None, description="GitHub repo URL or local folder path")
    source_metadata: dict | None = Field(None)


class StatusRequest(BaseModel):
    status: str = Field(..., description="discovered|downloaded|extracted|indexed|quarantine|error")
    error_message: str | None = None


class BatchRegisterRequest(BaseModel):
    items: list[RegisterRequest] = Field(..., min_length=1, max_length=500)


class DeleteResponse(BaseModel):
    status: str
    source_id: str
    deleted: bool


@router.post("/register", summary="Register a discovered item (idempotent; enqueues extract)")
async def register_item(payload: RegisterRequest, _tok: str = Depends(require_any_token)):
    result = await _register_source(payload)
    return result


@router.post("/register-batch", summary="Batch register sources (max 500)")
async def register_batch(payload: BatchRegisterRequest, _tok: str = Depends(require_any_token)):
    results = []
    async with (await _get_engine()).begin() as conn:
        for item in payload.items:
            try:
                res = await _register_source(item, conn=conn)
                results.append({"status": "ok", **res})
            except Exception as e:
                results.append({"status": "error", "drive_item_id": item.drive_item_id, "detail": str(e)})
    return {"batch": len(results), "results": results}


@router.delete("/{source_id}", summary="Delete a source and related data")
async def delete_source(source_id: uuid.UUID, _tok: str = Depends(require_any_token)):
    async with (await _get_engine()).begin() as conn:
        row = (
            await conn.execute(
                text("SELECT source_id FROM sources WHERE source_id = :id"), {"id": source_id}
            )
        ).first()
        if not row:
            raise HTTPException(status_code=404, detail="Source not found")
        await conn.execute(text("DELETE FROM source_blobs WHERE source_id = :id"), {"id": source_id})
        await conn.execute(text("DELETE FROM source_text WHERE source_id = :id"), {"id": source_id})
        await conn.execute(
            text("DELETE FROM sources WHERE source_id = :id RETURNING source_id"), {"id": source_id}
        )
    return DeleteResponse(status="deleted", source_id=str(source_id), deleted=True)


@router.delete("/by-drive-item/{drive_item_id}", summary="Delete a source by drive item ID")
async def delete_source_by_drive_item(drive_item_id: str, _tok: str = Depends(require_any_token)):
    async with (await _get_engine()).begin() as conn:
        row = (
            await conn.execute(
                text("SELECT source_id FROM sources WHERE drive_item_id = :id"), {"id": drive_item_id}
            )
        ).first()
        if not row:
            raise HTTPException(status_code=404, detail="Source not found")
        source_id = str(row[0])
        await conn.execute(text("DELETE FROM source_blobs WHERE source_id = :id"), {"id": source_id})
        await conn.execute(text("DELETE FROM source_text WHERE source_id = :id"), {"id": source_id})
        await conn.execute(
            text("DELETE FROM sources WHERE source_id = :id RETURNING source_id"), {"id": source_id}
        )
    return DeleteResponse(status="deleted", source_id=source_id, deleted=True)


@router.get("/", summary="List sources by status")
async def list_sources(status: str | None = None, limit: int = 50):
    engine = get_engine()
    async with engine.connect() as conn:
        query = (
            select(
                Source.source_id,
                Source.drive_item_id,
                Source.drive_id,
                Source.source_type,
                Source.source_url,
                Source.file_path,
                Source.file_name,
                Source.mime_type,
                Source.size_bytes,
                Source.sha256_hash,
                Source.status,
                Source.source_metadata,
            ).limit(limit)
        )
        if status:
            query = query.where(Source.status == status)
        result = await conn.execute(query)
        rows = result.mappings().all()
    return [
        {
            "source_id": str(row["source_id"]),
            "drive_item_id": row["drive_item_id"],
            "drive_id": row["drive_id"],
            "file_path": row["file_path"],
            "file_name": row["file_name"],
            "mime_type": row["mime_type"],
            "size_bytes": row["size_bytes"],
            "sha256_hash": row["sha256_hash"],
            "status": row["status"],
            "source_type": row.get("source_type") or "local",
            "source_url": row.get("source_url"),
            "source_metadata": row["source_metadata"] or {},
        }
        for row in rows
    ]


@router.get("/{drive_item_id}", summary="Get source by OneDrive item ID")
async def get_source(drive_item_id: str):
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(select(Source).where(Source.drive_item_id == drive_item_id))
        row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Source not found")
    return row


@router.head("/by-hash/{sha256}", summary="Is this content hash already known? (upload dedup)")
async def source_exists_by_hash(sha256: str):
    engine = get_engine()
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT source_id FROM sources WHERE sha256_hash = :h LIMIT 1"), {"h": sha256}
            )
        ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Unknown hash")
    return Response(status_code=200)


@router.get("/by-id/{source_id}", summary="Source metadata by UUID")
async def get_source_by_id(source_id: uuid.UUID):
    engine = get_engine()
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                select(Source).where(Source.source_id == source_id)
            )
        ).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Source not found")
    return row


@router.post("/{source_id}/blob", summary="Store raw source bytes")
async def put_blob(source_id: uuid.UUID, request: Request, _tok: str = Depends(require_any_token)):
    data = await request.body()
    engine = get_engine()
    async with engine.begin() as conn:
        # Verify source exists.
        src = (
            await conn.execute(
                text("SELECT sha256_hash, mime_type FROM sources WHERE source_id = :id"),
                {"id": source_id},
            )
        ).first()
        if not src:
            raise HTTPException(status_code=404, detail="Source not found")
        await conn.execute(
            text("""
                INSERT INTO source_blobs (source_id, sha256_hash, content_type, data, size_bytes)
                VALUES (:id, :sha, :ct, :data, :size)
                ON CONFLICT (source_id) DO UPDATE SET
                    data = EXCLUDED.data, size_bytes = EXCLUDED.size_bytes,
                    content_type = EXCLUDED.content_type
            """),
            {"id": source_id, "sha": src[0], "ct": src[1] or "application/octet-stream",
             "data": data, "size": len(data)},
        )
    return {"status": "stored", "bytes": len(data)}


@router.get("/{source_id}/blob", summary="Fetch raw source bytes")
async def get_blob(source_id: uuid.UUID):
    engine = get_engine()
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT data, content_type FROM source_blobs WHERE source_id = :id"),
                {"id": source_id},
            )
        ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Blob not found")
    return Response(content=row[0], media_type=row[1] or "application/octet-stream")


@router.post("/{source_id}/text", summary="Store extracted plain text")
async def put_source_text(source_id: uuid.UUID, request: Request, _tok: str = Depends(require_any_token)):
    body = await request.body()
    text_content = body.decode("utf-8", errors="replace")
    import hashlib
    content_hash = hashlib.sha256(text_content.encode("utf-8")).hexdigest()
    engine = get_engine()
    async with engine.begin() as conn:
        src = (
            await conn.execute(
                text("SELECT 1 FROM sources WHERE source_id = :id"), {"id": source_id}
            )
        ).first()
        if not src:
            raise HTTPException(status_code=404, detail="Source not found")
        await conn.execute(
            text("""
                INSERT INTO source_text (source_id, content_hash, text, char_count)
                VALUES (:id, :hash, :text, :len)
                ON CONFLICT (source_id) DO UPDATE SET
                    text = EXCLUDED.text, content_hash = EXCLUDED.content_hash,
                    char_count = EXCLUDED.char_count
            """),
            {"id": source_id, "hash": content_hash, "text": text_content, "len": len(text_content)},
        )
    return {"status": "stored", "chars": len(text_content)}


@router.get("/{source_id}/text", summary="Fetch extracted plain text")
async def get_source_text(source_id: uuid.UUID):
    engine = get_engine()
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT text FROM source_text WHERE source_id = :id"), {"id": source_id}
            )
        ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Extracted text not found")
    return Response(content=row[0], media_type="text/plain; charset=utf-8")


@router.post("/{source_id}/status", summary="Guard a source status transition")
async def set_source_status(source_id: uuid.UUID, payload: StatusRequest, _tok: str = Depends(require_any_token)):
    engine = get_engine()
    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                UPDATE sources
                   SET status = :s,
                       error_message = COALESCE(:err, error_message),
                       updated_at = now()
                 WHERE source_id = :id
                RETURNING source_id
            """),
            {"id": source_id, "s": payload.status, "err": payload.error_message},
        )
        if result.first() is None:
            raise HTTPException(status_code=404, detail="Source not found")
    return {"status": "updated", "source_id": str(source_id), "new_status": payload.status}
