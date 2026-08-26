#!/usr/bin/env python3
"""Direct DB recompile - bypasses the control-api to avoid 500s.

1. Queries the DB for bad page source_ids
2. Calls the LLM (NVIDIA direct) for each
3. Writes the result directly to wiki_pages table via asyncpg
4. Concurrent with rate-limiting
"""
import argparse
import asyncio
import os
import re
import sys
import time
import uuid
from pathlib import Path

import asyncpg
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workers.gpu_worker.markdown_compiler import compile_page  # noqa: E402

CONTROL_API = os.getenv("CONTROL_API_URL", "http://localhost:8000/api/v1")
API_TOKEN = os.getenv("API_TOKEN") or "101b6aefc27961c62d5dd42acbac07923e1c340d45d9604e3766f52622a614aa"
DB_DSN = os.getenv("DATABASE_URL") or "postgresql://postgres:d93f1a7b2c4e5f608192a3b4c5d6e7f8@localhost:5432/knowledge_base"

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def auth_headers() -> dict:
    return {"Authorization": f"Bearer {API_TOKEN}"}


BAD_SQL = """
SELECT
    (frontmatter->>'source_id')::uuid AS source_id,
    file_path,
    title
FROM wiki_pages
WHERE
    (
        markdown_body LIKE '```json%'
        OR markdown_body LIKE '> [!WARNING] Inferred%'
        OR title ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
        OR (markdown_body NOT LIKE '%```json%'
            AND markdown_body NOT LIKE '%Inferred%'
            AND markdown_body !~ '\\[\\^src_\\d+\\]'
            AND markdown_body NOT LIKE '%cross-KB consolidation%'
            AND markdown_body NOT LIKE '%cross-KB%'
            AND markdown_body NOT LIKE '%Originals remain canonical%')
    )
ORDER BY updated_at
LIMIT $1
"""


async def get_bad_source_ids(pool, limit):
    async with pool.acquire() as conn:
        rows = await conn.fetch(BAD_SQL, limit)
    return [dict(r) for r in rows if r["source_id"]]


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


async def process_one(client, pool, source_id, file_path, page_title):
    units = await get_units(client, source_id)
    if not units:
        return {"source_id": str(source_id), "ok": False, "err": "no units", "file_path": file_path}

    title_seed = (file_path or str(source_id)).split("/")[-1].removesuffix(".md") or str(source_id)

    # Compile with retry
    body = ""
    last_err = ""
    for attempt in range(3):
        page = await compile_page(title_seed, units, {"source_id": str(source_id), "source_path": file_path})
        body = page.markdown or ""
        last_err = ""
        if body.startswith("```json") or body.startswith("> [!WARNING]") or body.count("[^src_") == 0:
            last_err = "bad body" if body.startswith("```json") or body.startswith("> [!WARNING]") else "no citations"
            await asyncio.sleep(2 + attempt * 2)
            continue
        break

    if last_err:
        return {"source_id": str(source_id), "ok": False, "err": f"after 3 retries: {last_err}", "file_path": file_path}

    # Derive title
    title = page_title
    if body.startswith("# "):
        first_line = body.split("\n", 1)[0]
        if 2 < len(first_line) < 120:
            title = first_line[2:].strip()
    if UUID_RE.match(title):
        title = title_seed

    # Look up the existing page_id for this file_path
    async with pool.acquire() as conn:
        existing = await conn.fetchval(
            "SELECT page_id FROM wiki_pages WHERE file_path = $1", file_path
        )
    page_id = str(existing) if existing else str(uuid.uuid5(uuid.NAMESPACE_URL, f"wiki-page/{file_path or str(source_id)}"))

    try:
        await write_page_to_db(pool, page_id, file_path, title, source_id, body, units)
    except Exception as e:
        return {"source_id": str(source_id), "ok": False, "err": f"db: {e}", "file_path": file_path}

    return {"source_id": str(source_id), "ok": True, "file_path": file_path, "title": title, "body_len": len(body), "citations": body.count("[^src_")}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20000)
    ap.add_argument("--concurrency", type=int, default=1)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    pool = await asyncpg.create_pool(DB_DSN, min_size=2, max_size=4)
    try:
        bad = await get_bad_source_ids(pool, args.limit)
        print(f"Found {len(bad)} bad pages", flush=True)
        if args.dry_run:
            return
        if not bad:
            return
    finally:
        # Don't close - we'll use the same pool for processing
        pass

    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        sem = asyncio.Semaphore(args.concurrency)
        results = []
        t0 = time.time()

        async def run_one(task):
            async with sem:
                return await process_one(client, pool, task["source_id"], task["file_path"], task["title"])

        tasks = [asyncio.create_task(run_one(t)) for t in bad]
        for i, fut in enumerate(asyncio.as_completed(tasks), 1):
            res = await fut
            results.append(res)
            if i % 10 == 0:
                ok = sum(1 for r in results if r.get("ok"))
                elapsed = time.time() - t0
                rate = i / elapsed if elapsed > 0 else 0
                print(f"[{i}/{len(bad)}] ok={ok} bad={i-ok} elapsed={elapsed:.1f}s rate={rate:.1f}/s", flush=True)

    await pool.close()

    ok = sum(1 for r in results if r.get("ok"))
    bad_count = len(results) - ok
    elapsed = time.time() - t0
    print(f"\n=== done: {len(results)} processed in {elapsed:.1f}s ({len(results)/elapsed:.2f}/s) ===")
    print(f"ok={ok} bad={bad_count}")
    if bad_count:
        rc = {}
        for r in results:
            if not r.get("ok"):
                rc[r["err"]] = rc.get(r["err"], 0) + 1
        print("Failure breakdown:")
        for reason, c in sorted(rc.items(), key=lambda x: -x[1]):
            print(f"  {reason}: {c}")


if __name__ == "__main__":
    asyncio.run(main())
