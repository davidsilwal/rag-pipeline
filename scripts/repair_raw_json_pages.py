#!/usr/bin/env python3
"""Repair wiki pages whose markdown_body holds the compiler's raw LLM JSON
(```json {title, frontmatter, body}```) instead of the extracted page body.

This is a *no-LLM* pass: it recovers the body already sitting inside the
stored JSON (fully for complete JSON, up to the cut for truncated responses)
and upserts the clean markdown via the control API. Existing chunks are left
untouched. Run the LLM-based `reprocess_bad_pages.py` afterwards to fully
recompile the truncated pages end-to-end.

Usage:
    API_TOKEN=... CONTROL_API_URL=... DATABASE_URL=... \
        python3 scripts/repair_raw_json_pages.py [--limit N] [--dry-run]
"""
import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

import asyncpg
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workers.gpu_worker.markdown_compiler import (  # noqa: E402
    _extract_body_field,
)

CONTROL_API = os.getenv("CONTROL_API_URL", "http://localhost:8000/api/v1")
API_TOKEN = os.getenv("API_TOKEN") or ""
DB_DSN = (
    os.getenv("DATABASE_URL")
    or "postgresql://postgres:d93f1a7b2c4e5f608192a3b4c5d6e7f8@localhost:5432/knowledge_base"
)

RAW_JSON_SQL = """
SELECT page_id, file_path, title, page_type, domain, status, frontmatter,
       markdown_body, source_unit_ids
FROM wiki_pages
WHERE markdown_body LIKE '```json%'
ORDER BY length(markdown_body) DESC
LIMIT $1
"""

FENCE_RE = re.compile(r"^```[a-zA-Z0-9_-]*\n?", re.M)


def recover_body(raw: str) -> str | None:
    """Extract the `body` field from raw compiler JSON (full or truncated)."""
    trimmed = raw.strip()
    inner = FENCE_RE.sub("", trimmed, count=1).rstrip()
    if inner.endswith("```"):
        inner = inner[:-3].rstrip()
    # Complete JSON first
    try:
        obj = json.loads(inner)
        if isinstance(obj, dict) and isinstance(obj.get("body"), str) and obj["body"].strip():
            return obj["body"]
    except Exception:
        pass
    # Truncated JSON — recover the body string value directly
    return _extract_body_field(trimmed)


def auth_headers() -> dict:
    return {"Authorization": f"Bearer {API_TOKEN}"}


async def find_raw_pages(pool, limit: int) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(RAW_JSON_SQL, limit)
    return [dict(r) for r in rows]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=100000)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    pool = await asyncpg.create_pool(DB_DSN, min_size=1, max_size=4)
    rows = await find_raw_pages(pool, args.limit)
    await pool.close()
    print(f"Found {len(rows)} pages with raw JSON bodies", flush=True)

    fixed = 0
    skipped = 0
    examples: list[str] = []

    async def do_update(conn, r: dict, body: str) -> None:
        # Direct UPDATE — equivalent to the API's ON CONFLICT upsert, but a
        # few orders of magnitude faster for a bulk pass. Chunks are left
        # untouched (the API upsert only touches chunks it is given).
        await conn.execute(
            "UPDATE wiki_pages SET markdown_body = $1, updated_at = now() "
            "WHERE page_id = $2",
            body,
            r["page_id"],
        )

    pool = await asyncpg.create_pool(DB_DSN, min_size=4, max_size=8)
    try:
        async with pool.acquire() as conn:
            for i, r in enumerate(rows):
                raw = r["markdown_body"] or ""
                body = recover_body(raw)
                if body:
                    # The model occasionally emits lone surrogates (\uD8xx)
                    # that aren't valid UTF-8 for the TEXT column.
                    body = body.encode("utf-8", "replace").decode("utf-8")
                if not body or len(body) < 20:
                    skipped += 1
                    if len(examples) < 5:
                        examples.append(f"{r['file_path']}: could not recover ({len(raw)} chars)")
                    continue
                if body.strip() == raw.strip():
                    skipped += 1
                    continue
                if args.dry_run:
                    fixed += 1
                    if fixed <= 3:
                        print(f"  [{fixed}] {r['file_path']}: {len(raw)} -> {len(body)} chars")
                    continue
                await do_update(conn, r, body)
                fixed += 1
                if fixed % 200 == 0:
                    print(f"  ...{fixed}/{len(rows)} fixed", flush=True)
    finally:
        await pool.close()

    print(f"\n=== done: fixed={fixed} skipped={skipped} of {len(rows)} ===", flush=True)
    for ex in examples:
        print(f"  NOTE: {ex}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
