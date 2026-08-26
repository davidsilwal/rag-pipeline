#!/usr/bin/env python3
"""Reprocess wiki pages that don't follow best practices.

Targets:
- Pages whose title is a UUID
- Pages whose body has no `[^src_` citations
- Pages whose body starts with ```json (old LLM response format)
- Pages whose body starts with "> [!WARNING] Inferred" (LLM unavailable fallback)
- Pages whose body has fewer than 3 citations
"""
import argparse
import asyncio
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workers.gpu_worker.markdown_compiler import compile_page  # noqa: E402

CONTROL_API = os.getenv("CONTROL_API_URL", "http://localhost:8000/api/v1")
API_TOKEN = os.getenv("API_TOKEN") or "101b6aefc27961c62d5dd42acbac07923e1c340d45d9604e3766f52622a614aa"


def auth_headers() -> dict:
    return {"Authorization": f"Bearer {API_TOKEN}"}


UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
CITATION_RE = re.compile(r"\[\^src_\d+\]")


def is_bad(title: str, body: str) -> str | None:
    """Return reason string if the page is bad, else None."""
    if not body:
        return "empty body"
    if body.startswith("```json"):
        return "old json wrapper"
    if body.startswith("> [!WARNING] Inferred"):
        return "inferred fallback"
    if title and UUID_RE.match(title):
        return "uuid title"
    citations = body.count("[^src_")
    if citations < 2:
        return f"too few citations ({citations})"
    return None


async def fetch_bad_pages(client: httpx.AsyncClient, limit: int) -> list[dict]:
    """Use the control API to list all pages and filter to bad ones."""
    out: list[dict] = []
    offset = 0
    page_size = 500
    while len(out) < limit:
        r = await client.get(
            f"{CONTROL_API}/wiki/pages?limit={page_size}",
            headers=auth_headers(),
        )
        r.raise_for_status()
        pages = r.json()
        if not pages:
            break
        # We need the body too, fetch each by id
        for p in pages:
            if len(out) >= limit:
                break
            try:
                pr = await client.get(f"{CONTROL_API}/wiki/pages/{p['page_id']}", headers=auth_headers())
                pr.raise_for_status()
                full = pr.json()
            except Exception:
                continue
            body = full.get("markdown_body") or ""
            title = full.get("title") or ""
            reason = is_bad(title, body)
            if reason:
                out.append({"page_id": full["page_id"], "file_path": full["file_path"], "title": title, "reason": reason})
        # advance - the API doesn't support offset/limit for wiki pages, so we get the same list
        # We need a different approach: just process all pages in the result
        break
    return out


async def fetch_all_pages_with_body(client: httpx.AsyncClient) -> list[dict]:
    """Use the source_ids endpoint to find sources whose wiki pages are bad."""
    out: list[dict] = []
    # Page through sources
    offset = 0
    page_size = 500
    while True:
        r = await client.get(
            f"{CONTROL_API}/sources/?status=extracted&limit={page_size}&offset={offset}",
            headers=auth_headers(),
        )
        r.raise_for_status()
        sources = r.json()
        if not sources:
            break
        for s in sources:
            # Check the wiki page for this source
            try:
                pr = await client.get(f"{CONTROL_API}/wiki/by-source-id/{s['source_id']}", headers=auth_headers())
                if pr.status_code != 200:
                    continue
                full = pr.json()
            except Exception:
                continue
            body = full.get("markdown_body") or ""
            title = full.get("title") or ""
            reason = is_bad(title, body)
            if reason:
                out.append({"source_id": s["source_id"], "file_path": s.get("file_path"), "reason": reason, "title": title})
        if len(sources) < page_size:
            break
        offset += page_size
    return out


async def process_one(client: httpx.AsyncClient, source_id: str) -> dict:
    try:
        src = (await client.get(f"{CONTROL_API}/sources/by-id/{source_id}", headers=auth_headers())).json()
    except Exception as e:
        return {"source_id": source_id, "ok": False, "err": f"fetch source: {e}"}
    if not src:
        return {"source_id": source_id, "ok": False, "err": "no source"}
    file_path = src.get("file_path")
    title_seed = (file_path or source_id).split("/")[-1].removesuffix(".md") or source_id

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

    # Compile
    page = await compile_page(title_seed, units, {"source_id": source_id, "source_path": file_path})
    body = page.markdown or ""

    # Quality gate
    if body.count("[^src_") == 0:
        return {"source_id": source_id, "ok": False, "err": "no citations in body", "file_path": file_path}
    if body.startswith("```json") or body.startswith("> [!WARNING]"):
        return {"source_id": source_id, "ok": False, "err": "bad body format", "file_path": file_path}

    # Derive title
    title = title_seed
    if body.startswith("# "):
        first_line = body.split("\n", 1)[0]
        if 2 < len(first_line) < 120:
            title = first_line[2:].strip()
    if UUID_RE.match(title):
        title = title_seed

    page_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"wiki-page/{file_path or source_id}"))
    payload = {
        "page_id": page_id,
        "file_path": file_path,
        "title": title,
        "page_type": "source",
        "domain": "docs",
        "status": "active",
        "frontmatter": {
            "source_id": source_id,
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
                return {"source_id": source_id, "ok": False, "err": f"upsert: {e}", "file_path": file_path}
            await asyncio.sleep(2 + attempt * 3)
    else:
        return {"source_id": source_id, "ok": False, "err": "upsert: failed after retries", "file_path": file_path}

    return {"source_id": source_id, "ok": True, "file_path": file_path, "title": title, "body_len": len(body), "citations": body.count("[^src_")}


async def worker_loop(name: str, queue: asyncio.Queue, results: list, client: httpx.AsyncClient):
    while True:
        task = await queue.get()
        if task is None:
            queue.task_done()
            return
        t0 = time.time()
        result = await process_one(client, task["source_id"])
        result["elapsed"] = round(time.time() - t0, 2)
        result["reason"] = task.get("reason", "")
        results.append(result)
        if len(results) % 10 == 0:
            ok = sum(1 for r in results if r.get("ok"))
            print(f"[{name}] processed={len(results)} ok={ok} last={result}", flush=True)
        queue.task_done()


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=2000)
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--dry-run", action="store_true", help="List bad pages but don't recompile")
    args = ap.parse_args()

    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        print(f"Fetching bad pages...")
        bad = await fetch_all_pages_with_body(client)
        print(f"Found {len(bad)} bad pages")
        if args.dry_run:
            reasons: dict[str, int] = {}
            for b in bad:
                reasons[b["reason"]] = reasons.get(b["reason"], 0) + 1
            print("Reason breakdown:")
            for r, c in sorted(reasons.items(), key=lambda x: -x[1]):
                print(f"  {r}: {c}")
            print(f"Total: {len(bad)}")
            return

        bad = bad[: args.limit]
        if not bad:
            return
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
            reason_counts: dict[str, int] = {}
            for r in results:
                if not r.get("ok"):
                    reason_counts[r["err"]] = reason_counts.get(r["err"], 0) + 1
            print("Failure breakdown:")
            for reason, c in sorted(reason_counts.items(), key=lambda x: -x[1]):
                print(f"  {reason}: {c}")


if __name__ == "__main__":
    asyncio.run(main())
