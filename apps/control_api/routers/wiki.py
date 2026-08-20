#!/usr/bin/env python3
"""apps/control_api/routers/wiki.py — Push compiled Markdown to Git + pgvector."""

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import insert

from services.git_ops import GitPublisher
from database import get_engine
from models import WikiPage, WikiChunk

router = APIRouter(prefix="/wiki", tags=["wiki"])


class PublishRequest(BaseModel):
    pages: list[dict] = Field(
        ...,
        description="List of {file_path, title, page_type, domain, frontmatter, markdown_body, source_unit_ids}",
    )


@router.post("/publish", summary="Push compiled Markdown -> Git commit + pgvector index")
async def publish(payload: PublishRequest):
    engine = get_engine()
    publisher = GitPublisher("/var/data/wiki")
    results = []
    async with engine.begin() as conn:
        for page in payload.pages:
            page_id = publisher.commit_page(page, upsert_db=True)
            for chunk in page.get("chunks", []):
                stmt = insert(WikiChunk).values(
                    page_id=page_id,
                    file_path=chunk["file_path"],
                    heading_path=chunk["heading_path"],
                    chunk_index=chunk["chunk_index"],
                    content=chunk["content"],
                    content_hash=chunk["content_hash"],
                    dense_vector=chunk.get("dense_vector"),
                    metadata=chunk.get("metadata", {}),
                )
                await conn.execute(stmt)
            results.append({"page": page["file_path"], "status": "published"})
    return {"published": len(results), "results": results}


@router.get("/page/{file_path:path}", summary="Get a wiki page by file path")
async def get_page(file_path: str):
    engine = get_engine()
    async with engine.connect() as conn:
        from sqlalchemy import select
        result = await conn.execute(select(WikiPage).where(WikiPage.file_path == file_path))
        row = result.scalar_one_or_none()
        if not row:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Page not found")
        return row