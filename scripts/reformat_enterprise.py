#!/usr/bin/env python3
"""Recompile pages that already have citations but lack enterprise formatting.
This is a targeted pass: only pages with structured content (user stories,
API endpoints, tickets) that still use flat prose get re-rendered.
"""
import argparse
import asyncio
import os
import re
import sys
import time
from pathlib import Path
import uuid
import asyncpg
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workers.gpu_worker.markdown_compiler import compile_page  # noqa: E402

CONTROL_API = os.getenv("CONTROL_API_URL", "http://localhost:8000/api/v1")
API_TOKEN = os.getenv("API_TOKEN") or "101b6aefc27961c62d5dd42acbac07923e1c340d45d9604e3766f52622a614aa"
DB_DSN = os.getenv("DATABASE_URL") or "postgresql://postgres:d93f1a7b2c4e5f608192a3b4c5d6e7f8@localhost:5432/knowledge_base"

# Pages that have citations but still need enterprise formatting.
# We only target pages that contain known structured artifacts:
#   - "As a ... I want ..." (user stories)
#   - "POST /" or "GET /" (API endpoints)
#   - "Acceptance Criteria" or "Criteria:"
#   - "| Field | Value |" NOT present (skip if already formatted)
NEEDS_REFORMAT_SQL = """
SELECT
    (frontmatter->>'source_id')::uuid AS source_id,
    file_path,
    title,
    markdown_body
FROM wiki_pages
WHERE
    markdown_body NOT LIKE '%Inferred%'
    AND markdown_body NOT LIKE '%```json%'
    AND markdown_body ~ '\\[\\^src_\\d+\\]'
    AND markdown_body NOT LIKE '%| Field | Value |%'
    AND (
        markdown_body ~* 'As a .* I want '
        OR markdown_body ~* 'Acceptance Criteria'
        OR markdown_body ~* 'Criteria:'
        OR markdown_body ~* 'POST /'
        OR markdown_body ~* 'GET /'
        OR markdown_body ~* '\\| .* \\| .* \\|'
    )
ORDER BY updated_at
"""

# Or simpler: skip already-formatted pages, catch all that have citations
# but lack enterprise formatting markers.
ALL_FLAT_SQL = """
SELECT
    (frontmatter->>'source_id')::uuid AS source_id,
    file_path,
    title,
    markdown_body
FROM wiki_pages
WHERE
    markdown_body NOT LIKE '%Inferred%'
    AND markdown_body NOT LIKE '%```json%'
    AND markdown_body ~ '\\[\\^src_\\d+\\]'
    AND markdown_body NOT LIKE '%| Field | Value |%'
ORDER BY updated_at
"""


def auth_headers() -> dict:
    return {"Authorization": f"Bearer {API_TOKEN}"}


async def get_units(client, source_id):
    units = []
    limit = 1000
    offset = 0
    while True:
        r = await client.get(
            f"{CONTROL_API}/units/?source_id={source_id}&limit={limit}&offset={offset}",
            headers=auth_headers(),
        )
        if r.status_code != 200:
            return None
        batch = r.json()
        if not batch:
            break
        units.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
    return units


async def write_page_to_db(pool, page_id, file_path, title, source_id, body, units):
    """Direct DB write of the wiki page and its chunks."""
    source_unit_ids = [u.get("unit_id") for u in units if u.get("unit_id")]
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("""
                INSERT INTO wiki_pages
                    (page_id, file_path, title, page_type, domain, status, frontmatter, markdown_body, source_unit_ids)
                VALUES
                    ($1, $2, $3, 'source', 'docs', 'active', $4, $5, $6)
                ON CONFLICT (file_path) DO UPDATE SET
                    title = EXCLUDED.title,
                    frontmatter = EXCLUDED.frontmatter,
                    markdown_body = EXCLUDED.markdown_body,
                    source_unit_ids = EXCLUDED.source_unit_ids,
                    updated_at = now()
            """,
                page_id, file_path, title,
                f'{{"source_id": "{source_id}", "source_path": "{file_path}", "title": "{title}"}}',
                body, source_unit_ids,
            )
            await conn.execute("DELETE FROM wiki_chunks WHERE page_id = $1", page_id)
            for idx, u in enumerate(units):
                txt = (u.get("clean_text") or "").strip()
                if not txt:
                    continue
                await conn.execute("""
                    INSERT INTO wiki_chunks
                        (page_id, file_path, chunk_index, content, heading_path, content_hash, chunk_metadata)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                    page_id, file_path, idx, txt[:8000],
                    u.get("heading_path") or [],
                    u.get("content_hash") or "",
                    f'{{"unit_id": "{u.get("unit_id") or ""}", "unit_type": "{u.get("unit_type")}"}}',
                )


async def process_one(client, pool, row, semaphore, log_path, args):
    source_id = row["source_id"]
    file_path = row["file_path"]
    title = row["title"]
    page_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, file_path))

    async with semaphore:
        units = await get_units(client, source_id)
        if not units:
            reason = "no_units"
            async with pool.acquire() as conn:
                await conn.execute("UPDATE wiki_pages SET status = $1 WHERE source_id = $2", reason, source_id)
            return {"source_id": str(source_id), "ok": False, "reason": reason}

        try:
            body = await compile_page(file_path, units, frontmatter={"source_id": str(source_id), "title": title})
        except Exception as exc:
            reason = f"compile_error: {exc}"
            async with pool.acquire() as conn:
                await conn.execute("UPDATE wiki_pages SET status = $1 WHERE source_id = $2", reason, source_id)
            return {"source_id": str(source_id), "ok": False, "reason": reason}

        # Skip if the new body matches config.bad_body_pattern
        import re as _re
        pat = os.getenv("BAD_BODY_PATTERN", r"```json.*?```")
        if _re.search(pat, body, _re.DOTALL):
            reason = "bad_body"
            async with pool.acquire() as conn:
                await conn.execute("UPDATE wiki_pages SET status = $1 WHERE source_id = $2", reason, source_id)
            return {"source_id": str(source_id), "ok": False, "reason": reason}

        await write_page_to_db(pool, page_id, file_path, title, source_id, body, units)

    return {"source_id": str(source_id), "ok": True, "file_path": file_path, "title": title, "body_len": len(body), "citations": body.count("[^src_")}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--structured-only", action="store_true",
                    help="Only reformat pages with known structured artifacts (user stories, API endpoints)")
    ap.add_argument("--all-flat", action="store_true",
                    help="Reformat ALL pages that are flat (no | Field | Value | tables)")
    args = ap.parse_args()

    sql = NEEDS_REFORMAT_SQL if args.structured_only else ALL_FLAT_SQL

    semaphore = asyncio.Semaphore(args.concurrency)
    pool = await asyncpg.create_pool(DB_DSN, min_size=1, max_size=args.concurrency + 1)
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
        rows = await pool.fetch(sql)
        total = min(len(rows), args.limit)
        print(f"Found {len(rows)} flat pages; processing {total} (concurrency={args.concurrency})")

        ok = bad = 0
        tasks = []
        for row in rows[:total]:
            tasks.append(process_one(client, pool, dict(row), semaphore, "/tmp/reformat_enterprise.log", args))

        for coro in asyncio.as_completed(tasks):
            result = await coro
            if result.get("ok"):
                ok += 1
            else:
                bad += 1
            pct = (ok + bad) * 100 // total if total else 0
            print(f"\r[{ok + bad}/{total}] ok={ok} bad={bad} ({pct}%)  ", end="", flush=True)

        print(f"\nDone: {ok} ok, {bad} bad out of {total}")

    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())