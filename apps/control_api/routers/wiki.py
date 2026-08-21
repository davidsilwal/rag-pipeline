#!/usr/bin/env python3
"""apps/control_api/routers/wiki.py — Read wiki output."""

from __future__ import annotations

from fastapi import APIRouter, Depends
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
