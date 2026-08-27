#!/usr/bin/env python3
"""Reprocess only the bad wiki pages by reading source_ids from the DB.

The recompile_all.py script iterates all sources, which is slow when most
of them are already in good format. This script queries the DB directly
to find the specific source_ids whose pages are bad, and only recompiles
those.

Bad criteria (any of):
- markdown_body starts with ```json (old LLM response format)
- markdown_body starts with "> [!WARNING] Inferred" (LLM unavailable)
- markdown_body has no markdown heading (reasoning leaks / raw LLM text)
- markdown_body doesn't contain [^src_N] (no citations)
- title is a UUID
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workers.gpu_worker.markdown_compiler import compile_page  # noqa: E402

CONTROL_API = os.getenv("CONTROL_API_URL", "http://localhost:8000/api/v1")
API_TOKEN = os.getenv("API_TOKEN") or "101b6aefc27961c62d5dd42acbac07923e1c340d45d9604e3766f52622a614aa"
DB_DSN = os.getenv("DATABASE_URL") or "postgresql://postgres:d93f1a7b2c4e5f608192a3b4c5d6e7f8@localhost:5432/knowledge_base"

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
CITATION_RE = re.compile(r"\[\^src_\d+\]")


def auth_headers() -> dict:
    return {"Authorization": f"Bearer {API_TOKEN}"}


# "broken": clearly-broken compiler output (raw fenced JSON/YAML, the
# LLM-unavailable fallback, or a UUID title). "all": also pages with no
# [^src_N] citations (mostly chat transcripts — usually left untouched).
BAD_SQL = """
SELECT
    (frontmatter->>'source_id')::uuid AS source_id,
    file_path,
    title
FROM wiki_pages
WHERE
    (
        markdown_body LIKE '```%'
        OR markdown_body LIKE '> [!WARNING] Inferred%'
        OR title ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
        -- Reasoning leaks: rambling LLM chain-of-thought with no markdown
        -- heading and no code fence (skips the intentional cross-KB stubs).
        OR (
            markdown_body NOT LIKE '%# %'
            AND markdown_body NOT LIKE '> [!WARNING]%'
            AND markdown_body NOT LIKE '```%'
            AND markdown_body NOT LIKE '<!-- cross-kb%'
            AND length(markdown_body) > 20
        )
        OR ({include_no_citation}
            AND markdown_body NOT LIKE '%```%'
            AND markdown_body NOT LIKE '%Inferred%'
            AND markdown_body !~ '\\[\\^src_\\d+\\]'
            AND markdown_body NOT LIKE '%cross-KB consolidation%'
            AND markdown_body NOT LIKE '%cross-KB%'
            AND markdown_body NOT LIKE '%Originals remain canonical%')
    )
LIMIT $1
"""


async def find_bad_source_ids(pool, limit: int, include_no_citation: bool = False) -> list[dict]:
    sql = BAD_SQL.replace(
        "{include_no_citation}", "TRUE" if include_no_citation else "FALSE"
    )
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, limit)
    return [dict(r) for r in rows if r["source_id"]]


async def process_one(client, pool, source_id: str, file_path: str, page_title: str) -> dict:
    # Fetch units
    units: list[dict] = []
    limit = 1000
    offset = 0
    while True:
        r = await client.get(
            f"{CONTROL_API}/units/?source_id={source_id}&limit={limit}&offset={offset}",
            headers=auth_headers(),
        )
        if r.status_code != 200:
            return {"source_id": source_id, "ok": False, "err": f"fetch units: {r.status_code}"}
        batch = r.json()
        if not batch:
            break
        units.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
    if not units:
        return {"source_id": source_id, "ok": False, "err": "no units"}

    title_seed = (file_path or str(source_id)).split("/")[-1].removesuffix(".md") or str(source_id)

    # Compile with retry — LLM is occasionally inconsistent
    body = ""
    last_err = ""
    for attempt in range(3):
        page = await compile_page(title_seed, units, {"source_id": str(source_id), "source_path": file_path})
        body = page.markdown or ""
        last_err = ""
        if body.startswith("```json") or body.startswith("> [!WARNING]") or body.count("[^src_") == 0:
            last_err = "bad body format" if body.startswith("```json") or body.startswith("> [!WARNING]") else "no citations in body"
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

    # Must match the worker's page_id formula (workers/runner.py) so
    # concurrent writes to the same file_path upsert the same row instead
    # of violating the wiki_pages_file_path_key unique constraint.
    page_id = str(uuid.uuid5(uuid.NAMESPACE_URL, file_path or str(source_id)))
    payload = {
        "page_id": page_id,
        "file_path": file_path,
        "title": title,
        "page_type": "source",
        "domain": "docs",
        "status": "active",
        "frontmatter": {
            "source_id": str(source_id),
            "source_path": file_path,
            "title": title,
        },
        "markdown_body": body,
        "source_unit_ids": [u.get("unit_id") for u in units if u.get("unit_id")],
        "chunks": [
            {
                "page_id": page_id,
                "file_path": file_path,
                "chunk_index": idx,
                "content": (u.get("clean_text") or "")[:8000],
                "heading_path": u.get("heading_path") or [],
                "content_hash": u.get("content_hash") or "",
                "chunk_metadata": {"unit_id": str(u.get("unit_id") or ""), "unit_type": u.get("unit_type")},
            }
            for idx, u in enumerate(units) if (u.get("clean_text") or "").strip()
        ],
    }

    for attempt in range(3):
        try:
            r = await client.post(f"{CONTROL_API}/wiki/pages", json={"pages": [payload]}, headers=auth_headers())
            r.raise_for_status()
            break
        except Exception as e:
            if attempt == 2:
                return {"source_id": str(source_id), "ok": False, "err": f"upsert: {e}", "file_path": file_path}
            await asyncio.sleep(2 + attempt * 3)
    else:
        return {"source_id": str(source_id), "ok": False, "err": "upsert: failed after retries", "file_path": file_path}

    return {"source_id": str(source_id), "ok": True, "file_path": file_path, "title": title, "body_len": len(body), "citations": body.count("[^src_")}


async def worker_loop(name, queue, results, client):
    while True:
        task = await queue.get()
        if task is None:
            queue.task_done()
            return
        t0 = time.time()
        result = await process_one(client, None, task["source_id"], task["file_path"], task["title"])
        result["elapsed"] = round(time.time() - t0, 2)
        results.append(result)
        if len(results) % 10 == 0:
            ok = sum(1 for r in results if r.get("ok"))
            print(f"[{name}] processed={len(results)} ok={ok} last={result}", flush=True)
        queue.task_done()


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20000)
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--include-no-citation",
        action="store_true",
        help="Also recompile pages with no [^src_N] citations (chat transcripts)",
    )
    args = ap.parse_args()

    pool = await asyncpg.create_pool(DB_DSN, min_size=1, max_size=4)
    try:
        bad = await find_bad_source_ids(pool, args.limit, args.include_no_citation)
        print(f"Found {len(bad)} bad pages", flush=True)
        if args.dry_run:
            for b in bad[:20]:
                print(f"  {b['file_path']:60s} title={b['title'][:30]!r}")
            return
        if not bad:
            return
    finally:
        await pool.close()

    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        queue: asyncio.Queue = asyncio.Queue()
        for t in bad:
            queue.put_nowait(t)
        for _ in range(args.concurrency):
            queue.put_nowait(None)
        results: list = []
        t0 = time.time()
        workers = [
            asyncio.create_task(worker_loop(f"w{i}", queue, results, client))
            for i in range(args.concurrency)
        ]
        await queue.join()
        for w in workers:
            w.cancel()
        for w in workers:
            try:
                await w
            except (asyncio.CancelledError, Exception):
                pass
        ok = sum(1 for r in results if r.get("ok"))
        bad2 = len(results) - ok
        elapsed = time.time() - t0
        print(f"\n=== done: {len(results)} processed in {elapsed:.1f}s ({len(results)/elapsed:.2f}/s) ===")
        print(f"ok={ok} bad={bad2}")
        if bad2:
            rc: dict[str, int] = {}
            for r in results:
                if not r.get("ok"):
                    rc[r["err"]] = rc.get(r["err"], 0) + 1
            print("Failure breakdown:")
            for reason, c in sorted(rc.items(), key=lambda x: -x[1]):
                print(f"  {reason}: {c}")


if __name__ == "__main__":
    import httpx
    asyncio.run(main())
