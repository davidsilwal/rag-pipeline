#!/usr/bin/env python3
"""apps/control_api/routers/wiki.py — Read wiki output."""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text

from database import get_engine
from deps import require_any_token

router = APIRouter(prefix="/wiki", tags=["wiki"])


@router.get("/pages")
async def list_wiki_pages(limit: int = 50, _tok: str = Depends(require_any_token)):
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                """
                SELECT page_id, file_path, title, page_type, domain, status,
                       frontmatter, left(markdown_body, 200) AS markdown_preview,
                       git_commit_sha, created_at, updated_at
                FROM wiki_pages
                ORDER BY updated_at DESC
                LIMIT :lim
                """
            ),
            {"lim": limit},
        )
        rows = result.mappings().all()
    return [
        {
            "page_id": str(row["page_id"]),
            "file_path": row["file_path"],
            "title": row["title"],
            "page_type": row["page_type"],
            "domain": row["domain"],
            "status": row["status"],
            "frontmatter": row["frontmatter"],
            "markdown_preview": row["markdown_preview"],
            "git_commit_sha": row["git_commit_sha"],
            "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        }
        for row in rows
    ]


@router.post("/pages", summary="Upsert wiki pages with chunks")
async def upsert_wiki_pages(request: Request, _tok: str = Depends(require_any_token)):
    body = await request.json()
    items = body if isinstance(body, list) else body.get("pages") or body.get("items") or []
    if not items:
        raise HTTPException(status_code=400, detail="Expected list of pages or {pages:[...]}")

    engine = get_engine()
    async with engine.begin() as conn:
        for page in items:
            page_id = page.get("page_id")
            if not page_id:
                continue
            chunks = page.get("chunks") or []
            await conn.execute(
                text("""
                INSERT INTO wiki_pages
                    (page_id, file_path, title, page_type, domain, status, frontmatter, markdown_body, source_unit_ids)
                VALUES
                    (:page_id, :file_path, :title, :page_type, :domain, :status, :frontmatter, :markdown_body, :source_unit_ids)
                ON CONFLICT (page_id) DO UPDATE SET
                    file_path = EXCLUDED.file_path,
                    title = EXCLUDED.title,
                    page_type = EXCLUDED.page_type,
                    domain = EXCLUDED.domain,
                    status = EXCLUDED.status,
                    frontmatter = EXCLUDED.frontmatter,
                    markdown_body = EXCLUDED.markdown_body,
                    source_unit_ids = EXCLUDED.source_unit_ids,
                    updated_at = now()
                """),
                {
                    "page_id": page_id,
                    "file_path": page.get("file_path"),
                    "title": page.get("title"),
                    "page_type": page.get("page_type") or "page",
                    "domain": page.get("domain") or "docs",
                    "status": page.get("status", "active"),
                    "frontmatter": json.dumps(page.get("frontmatter") or {}),
                    "markdown_body": page.get("markdown_body") or "",
                    "source_unit_ids": page.get("source_unit_ids") or [],
                },
            )
            for chunk in chunks:
                # chunk_id is a UUID column; when the producer omits one, derive a
                # deterministic UUID from (page_id, chunk_index) so re-runs are
                # stable and unique per chunk.
                chunk_id = chunk.get("chunk_id") or str(
                    uuid.uuid5(uuid.NAMESPACE_URL, f"{page_id}:{chunk.get('chunk_index') or 0}")
                )
                await conn.execute(
                    text("""
                    INSERT INTO wiki_chunks
                        (chunk_id, page_id, file_path, heading_path, chunk_index, content, content_hash, dense_vector, sparse_weights, chunk_metadata)
                    VALUES
                        (:chunk_id, :page_id, :file_path, :heading_path, :chunk_index, :content, :content_hash, :dense_vector, :sparse_weights, :chunk_metadata)
                    ON CONFLICT (page_id, chunk_index) DO UPDATE SET
                        content = EXCLUDED.content,
                        content_hash = EXCLUDED.content_hash,
                        dense_vector = EXCLUDED.dense_vector,
                        sparse_weights = EXCLUDED.sparse_weights,
                        chunk_metadata = EXCLUDED.chunk_metadata
                    """),
                    {
                        "chunk_id": chunk_id,
                        "page_id": page_id,
                        "file_path": chunk.get("file_path") or page.get("file_path"),
                        "heading_path": chunk.get("heading_path") or [],
                        "chunk_index": int(chunk.get("chunk_index") or 0),
                        "content": chunk.get("content") or "",
                        "content_hash": chunk.get("content_hash") or "",
                        "dense_vector": chunk.get("dense_vector"),
                        "sparse_weights": chunk.get("sparse_weights"),
                        "chunk_metadata": json.dumps(chunk.get("metadata") or chunk.get("chunk_metadata") or {}),
                    },
                )
    return {"pages": len(items), "chunks": sum(len(page.get("chunks") or []) for page in items)}


@router.get("/pages/{page_id}")
async def get_wiki_page(page_id: str, _tok: str = Depends(require_any_token)):
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                """
                SELECT page_id, file_path, title, page_type, domain, status,
                       frontmatter, markdown_body, source_unit_ids, last_verified_at, created_at, updated_at
                FROM wiki_pages
                WHERE page_id = :pid
                """
            ),
            {"pid": page_id},
        )
        row = result.mappings().first()
    if not row:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Wiki page not found")
    return {
        "page_id": str(row["page_id"]),
        "file_path": row["file_path"],
        "title": row["title"],
        "page_type": row["page_type"],
        "domain": row["domain"],
        "status": row["status"],
        "frontmatter": row["frontmatter"],
        "markdown_body": row["markdown_body"],
        "source_unit_ids": row["source_unit_ids"],
        "last_verified_at": row["last_verified_at"].isoformat() if row["last_verified_at"] else None,
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
    }


@router.get("/by-file/{file_path:path}")
async def get_wiki_page_by_file(file_path: str, _tok: str = Depends(require_any_token)):
    """Resolve a wiki page by its source file_path. Returns the page if it
    has been compiled; 404 with a hint if it hasn't been generated yet."""
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                """
                SELECT page_id, file_path, title, page_type, domain, status,
                       frontmatter, markdown_body, source_unit_ids,
                       last_verified_at, created_at, updated_at
                FROM wiki_pages
                WHERE file_path = :fp
                """
            ),
            {"fp": file_path},
        )
        row = result.mappings().first()
        if row:
            return {
                "page_id": str(row["page_id"]),
                "file_path": row["file_path"],
                "title": row["title"],
                "page_type": row["page_type"],
                "domain": row["domain"],
                "status": row["status"],
                "frontmatter": row["frontmatter"],
                "markdown_body": row["markdown_body"],
                "source_unit_ids": row["source_unit_ids"],
                "last_verified_at": row["last_verified_at"].isoformat() if row["last_verified_at"] else None,
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
            }
        # Page not compiled yet — check the source so we can tell the dashboard
        # whether the compile is queued or simply never going to happen.
        src = (await conn.execute(
            text("SELECT source_id, status FROM sources WHERE file_path = :fp"),
            {"fp": file_path},
        )).mappings().first()
    if src is None:
        raise HTTPException(
            status_code=404,
            detail=f"No source found at {file_path}",
        )
    # Source exists, page is not yet compiled. Return a structured 404 so the
    # dashboard can render a helpful "compile pending" state instead of a
    # blank "Page not found" message.
    raise HTTPException(
        status_code=404,
        detail={
            "message": f"Wiki page for {file_path} has not been compiled yet",
            "source_id": str(src["source_id"]),
            "source_status": src["status"],
        },
    )



@router.get("/chunks")
async def list_wiki_chunks(limit: int = 50, page_id: str | None = None, _tok: str = Depends(require_any_token)):
    engine = get_engine()
    async with engine.connect() as conn:
        sql = "SELECT chunk_id, page_id, content, chunk_metadata AS metadata, created_at FROM wiki_chunks"
        params = {"lim": limit}
        if page_id:
            sql += " WHERE page_id = :pid"
            params["pid"] = page_id
        sql += " ORDER BY created_at DESC LIMIT :lim"
        result = await conn.execute(text(sql), params)
        rows = result.mappings().all()
    return [
        {
            "chunk_id": str(row["chunk_id"]),
            "page_id": str(row["page_id"]),
            "content": row["content"],
            "metadata": row["metadata"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        }
        for row in rows
    ]
