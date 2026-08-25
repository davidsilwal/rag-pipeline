#!/usr/bin/env python3
"""One-shot compile processor.

Reads all queued 'compile' tasks, fetches source + units from the control API,
calls the new compile_page() (with the multi-strategy parser), POSTs the result
to /wiki/pages, and marks the task complete. Runs N tasks in parallel.

Usage: python scripts/recompile_all.py [--limit N] [--concurrency C]
"""
import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

import httpx

# Make the project importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workers.gpu_worker.markdown_compiler import compile_page  # noqa: E402

CONTROL_API = os.getenv("CONTROL_API_URL", "http://localhost:8000/api/v1")
API_TOKEN = os.getenv("API_TOKEN") or "101b6aefc27961c62d5dd42acbac07923e1c340d45d9604e3766f52622a614aa"


def auth_headers() -> dict:
    return {"Authorization": f"Bearer {API_TOKEN}"}


async def fetch_queued_compile(client: httpx.AsyncClient, limit: int) -> list[dict]:
    """Read directly from task_queue via a one-shot SQL via the control API if it exposes it.
    Otherwise we'll use a workaround: ask the API for all extracted sources and queue them ourselves.
    """
    # No direct queue listing endpoint. Use a direct DB query through the control API.
    # We'll use a custom SQL endpoint if available.
    r = await client.get(f"{CONTROL_API}/tasks/queued?stage=compile&limit={limit}", headers=auth_headers())
    if r.status_code == 200:
        return r.json()
    # Fallback: fetch sources with status='extracted' and synthesize tasks
    r = await client.get(f"{CONTROL_API}/sources?status=extracted&limit={limit}", headers=auth_headers())
    if r.status_code != 200:
        raise RuntimeError(f"failed to list sources: {r.status_code} {r.text[:200]}")
    sources = r.json()
    return [{"task_id": None, "scope_type": "source", "scope_id": s["source_id"]} for s in sources]


async def process_one(client: httpx.AsyncClient, task: dict) -> dict:
    try:
        return await _process_one_inner(client, task)
    except Exception as e:
        import traceback
        return {"scope_id": task.get("scope_id"), "ok": False, "err": f"uncaught: {e}", "trace": traceback.format_exc()[:500]}


async def _process_one_inner(client: httpx.AsyncClient, task: dict) -> dict:
    scope_id = task["scope_id"]
    try:
        src = (await client.get(f"{CONTROL_API}/sources/by-id/{scope_id}", headers=auth_headers())).json()
    except Exception as e:
        return {"scope_id": scope_id, "ok": False, "err": f"fetch source: {e}"}
    if not src:
        return {"scope_id": scope_id, "ok": False, "err": "no source"}
    file_path = src.get("file_path")
    title_seed = (file_path or scope_id).split("/")[-1].removesuffix(".md") or scope_id

    # Fetch units
    units: list[dict] = []
    limit = 1000
    offset = 0
    while True:
        r = await client.get(
            f"{CONTROL_API}/units/?source_id={scope_id}&limit={limit}&offset={offset}",
            headers=auth_headers(),
        )
        if r.status_code != 200:
            return {"scope_id": scope_id, "ok": False, "err": f"fetch units: {r.status_code}"}
        batch = r.json()
        if not batch:
            break
        units.extend(batch)
        if len(batch) < limit:
            break
        offset += limit

    if not units:
        return {"scope_id": scope_id, "ok": False, "err": "no units"}

    # Compile
    page = await compile_page(title_seed, units, {"source_id": scope_id, "source_path": file_path})

    # Derive proper title from LLM output (if it parsed JSON) or fall back to file_path
    body = page.markdown or ""
    title = title_seed
    if body.startswith("# "):
        # Extract first heading
        first_line = body.split("\n", 1)[0]
        if len(first_line) > 2 and len(first_line) < 120:
            title = first_line[2:].strip()

    # Build page payload
    page_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"wiki-page/{file_path or scope_id}"))
    payload = {
        "page_id": page_id,
        "file_path": file_path,
        "title": title,
        "page_type": "source",
        "domain": "docs",
        "status": "active",
        "frontmatter": {
            "source_id": scope_id,
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

    # Skip if already up to date (check by updated_at)
    if page.markdown and len(page.markdown) > 200:
        pass  # we always re-upsert since we may have new format
    # Upsert
    for attempt in range(3):
        try:
            r = await client.post(f"{CONTROL_API}/wiki/pages", json={"pages": [payload]}, headers=auth_headers())
            r.raise_for_status()
            break
        except Exception as e:
            if attempt == 2:
                return {"scope_id": scope_id, "ok": False, "err": f"upsert: {e}", "file_path": file_path}
            await asyncio.sleep(2 + attempt * 3)
    else:
        return {"scope_id": scope_id, "ok": False, "err": "upsert: failed after retries", "file_path": file_path}

    return {"scope_id": scope_id, "ok": True, "file_path": file_path, "title": title, "body_len": len(body), "citations": body.count("[^src_")}


async def worker_loop(name: str, queue: asyncio.Queue, results: list, client: httpx.AsyncClient):
    while True:
        task = await queue.get()
        if task is None:
            queue.task_done()
            return
        t0 = time.time()
        result = await process_one(client, task)
        result["elapsed"] = round(time.time() - t0, 2)
        results.append(result)
        if len(results) % 10 == 0:
            ok = sum(1 for r in results if r.get("ok"))
            print(f"[{name}] processed={len(results)} ok={ok} last={result}", flush=True)
        queue.task_done()


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20000)
    ap.add_argument("--concurrency", type=int, default=8)
    args = ap.parse_args()

    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        print(f"Fetching up to {args.limit} tasks...")
        tasks = await fetch_queued_compile(client, args.limit)
        print(f"Got {len(tasks)} tasks")
        if not tasks:
            return

        queue: asyncio.Queue = asyncio.Queue()
        for t in tasks:
            queue.put_nowait(t)
        # Add sentinel for each worker
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
        bad = len(results) - ok
        elapsed = time.time() - t0
        print(f"\n=== done: {len(results)} processed in {elapsed:.1f}s ({len(results)/elapsed:.2f}/s) ===")
        print(f"ok={ok} bad={bad}")
        if bad:
            for r in results:
                if not r.get("ok"):
                    print(" bad:", r)


if __name__ == "__main__":
    asyncio.run(main())
