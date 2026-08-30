#!/usr/bin/env python3
"""apps/control_api/routers/ingest.py — Add sources from the server filesystem,
browser uploads, or external URLs (GitHub / public repos).

Endpoints
---------
  GET  /ingest/node         list the ingest-root filesystem tree for the picker
  POST /ingest/server       register an existing server file or folder as source(s)
  POST /ingest/upload       multipart upload of one or more files/folders
  POST /ingest/url          register an external URL (e.g. a public git repo)

The three ingestion channels all funnel into the same durable path:
register source → store blob → enqueue ``extract`` (→ chunk → embed → …).
GitHub repos additionally enqueue a ``clone`` task so the worker pulls a
shallow copy onto the ingest root before registering its files.
"""

from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import os
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import text

from database import get_engine
from deps import require_any_token
from services.queue import enqueue_stage

log = logging.getLogger("ingest")

router = APIRouter(prefix="/ingest", tags=["ingest"])


# Files/dirs never offered in the picker or walked on registration.
_IGNORED_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", ".venv", "venv", "node_modules",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".next", ".turbo", "dist",
    "build", ".tox",
}
_IGNORED_FILES = {
    ".ds_store", "thumbs.db", ".gitignore", ".gitattributes", "desktop.ini",
}
_MAX_TEXT_SIZE = 100 * 1024 * 1024  # refuse to register single blobs > 100 MiB
_MAX_FILES_PER_DIR = 5000          # guard against pathological folder walks


def get_ingest_root() -> Path:
    """The filesytem root the server picker is confined to.

    Resolution order: INGEST_ROOT env → LOCAL_SOURCE_DIR → /var/data/ingest.
    """
    for var in ("INGEST_ROOT", "LOCAL_SOURCE_DIR"):
        val = os.getenv(var, "")
        if val:
            return Path(val).expanduser().resolve()
    return Path("/var/data/ingest")


def _resolve_in_root(raw: str) -> Path:
    """Resolve a user-supplied path against the ingest root, blocking escapes.

    Accepts an absolute path under the root or a path relative to it. Raises
    HTTPException 400 if the resolved path escapes the root.
    """
    root = get_ingest_root()
    p = Path(raw)
    if p.is_absolute():
        cand = p.resolve()
    else:
        cand = (root / p).resolve()
    try:
        cand.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=400, detail="Path escapes the ingest root")
    return cand


def _entry(p: Path, *, is_dir: bool) -> dict:
    try:
        size = 0 if is_dir else p.stat().st_size
    except OSError:
        size = 0
    return {
        "name": p.name,
        "path": str(p.relative_to(get_ingest_root())),
        "is_dir": is_dir,
        "mime_type": None if is_dir else (mimetypes.guess_type(str(p))[0] or "application/octet-stream"),
        "size_bytes": size,
    }


def _repo_slug(url: str) -> str:
    """Derive a filesystem-safe name for the repo from its URL."""
    import re as _re
    stem = url.rstrip("/").rstrip(".git")
    slug = stem.split("/")[-1] if "/" in stem else stem
    return _re.sub(r"[^A-Za-z0-9._-]+", "-", slug).strip("-")[:80] or hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


async def _register_bytes(
    conn,
    *,
    file_path: str,
    file_name: str,
    data: bytes,
    source_type: str,
    source_url: str | None,
) -> str:
    """Upsert a source row, store its blob, and enqueue extract. Returns source_id."""
    if len(data) > _MAX_TEXT_SIZE:
        raise HTTPException(status_code=413, detail=f"File too large: {file_name}")
    sha = hashlib.sha256(data).hexdigest()
    mime = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
    drive_item_id = f"{source_type}:{sha}"

    result = await conn.execute(
        text("""
            INSERT INTO sources
                (drive_item_id, drive_id, source_type, source_url, file_path, file_name,
                 mime_type, size_bytes, sha256_hash, status, source_metadata)
            VALUES (:did, :drive, :stype, :surl, :fp, :fn, :mime, :sz, :sha, 'discovered', '{}')
            ON CONFLICT (drive_item_id) DO UPDATE SET
                source_type = EXCLUDED.source_type,
                source_url = EXCLUDED.source_url,
                file_path = EXCLUDED.file_path,
                file_name = EXCLUDED.file_name,
                mime_type = EXCLUDED.mime_type,
                size_bytes = EXCLUDED.size_bytes,
                sha256_hash = EXCLUDED.sha256_hash,
                updated_at = now()
            RETURNING source_id
        """),
        {
            "did": drive_item_id,
            "drive": source_type,
            "stype": source_type,
            "surl": source_url,
            "fp": file_path,
            "fn": file_name,
            "mime": mime,
            "sz": len(data),
            "sha": sha,
        },
    )
    row = result.first()
    source_id = str(row[0])

    await conn.execute(
        text("""
            INSERT INTO source_blobs (source_id, sha256_hash, content_type, data, size_bytes)
            VALUES (:id, :sha, :ct, :data, :size)
            ON CONFLICT (source_id) DO UPDATE SET
                data = EXCLUDED.data, size_bytes = EXCLUDED.size_bytes,
                content_type = EXCLUDED.content_type
        """),
        {"id": source_id, "sha": sha, "ct": mime, "data": data, "size": len(data)},
    )
    await enqueue_stage(conn, "extract", "source", source_id, priority=0)
    return source_id


def _iter_files(rel_root: Path, p: Path, prefix: str) -> list[tuple[str, Path]]:
    """Recursively enumerate files under ``p`` with their ingest-relative paths."""
    out: list[tuple[str, Path]] = []
    stack: list[tuple[str, Path]] = [(prefix, p)]
    while stack:
        rel, cur = stack.pop()
        try:
            entries = sorted(cur.iterdir(), key=lambda e: e.name.lower())
        except OSError:
            continue
        for e in entries:
            try:
                if e.is_dir():
                    if e.name in _IGNORED_DIRS:
                        continue
                    stack.append((f"{rel}/{e.name}" if rel else e.name, e))
                elif e.name.lower() in _IGNORED_FILES:
                    continue
                else:
                    out.append(((f"{rel}/{e.name}" if rel else e.name), e))
                    if len(out) > _MAX_FILES_PER_DIR:
                        raise HTTPException(
                            status_code=413,
                            detail=f"Directory has more than {_MAX_FILES_PER_DIR} files",
                        )
            except HTTPException:
                raise
            except OSError:
                continue
    return out


@router.get("/node", summary="List a directory under the ingest root (server picker)")
async def browse(path: str = ""):
    root = get_ingest_root()
    if path in (".", "/", "~"):
        path = ""
    node = _resolve_in_root(path)
    if not node.is_dir():
        raise HTTPException(status_code=400, detail="Not a directory")

    dirs: list[dict] = []
    files: list[dict] = []
    try:
        for e in sorted(node.iterdir(), key=lambda x: x.name.lower()):
            try:
                if e.is_dir():
                    if e.name in _IGNORED_DIRS:
                        continue
                    dirs.append(_entry(e, is_dir=True))
                elif e.name.lower() in _IGNORED_FILES:
                    continue
                else:
                    files.append(_entry(e, is_dir=False))
            except OSError:
                continue
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Cannot read directory: {exc}")

    return {
        "path": str(node.relative_to(root)) if node != root else "",
        "absolute": str(node),
        "is_root": node == root,
        "dirs": dirs,
        "files": files,
    }


class ServerPathRequest(BaseModel):
    path: str = Field(..., description="Absolute or root-relative path to a file or folder")
    source_type: str = Field("local", description="local|github|onedrive")


@router.post("/server", summary="Register an existing file/folder on the server as source(s)")
async def register_server_path(payload: ServerPathRequest, _tok: str = Depends(require_any_token)):
    root = get_ingest_root()
    target = _resolve_in_root(payload.path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="Path does not exist")

    engine = get_engine()
    registered: list[str] = []
    under_root = root == target or root in target.parents
    async with engine.begin() as conn:
        if target.is_dir():
            rel_prefix = str(target.relative_to(root))
            for rel, f in _iter_files(root, target, rel_prefix):
                try:
                    data = f.read_bytes()
                except OSError:
                    continue
                sid = await _register_bytes(
                    conn, file_path=rel_prefix, file_name=rel,
                    data=data, source_type=payload.source_type, source_url=None,
                )
                registered.append(sid)
        else:
            if under_root:
                file_path = str(target.parent.relative_to(root)) if target.parent != root else ""
                file_name = str(target.relative_to(root))
            else:
                file_path = str(target.parent)
                file_name = target.name
            sid = await _register_bytes(
                conn, file_path=file_path, file_name=file_name,
                data=target.read_bytes(), source_type=payload.source_type, source_url=None,
            )
            registered.append(sid)
    return {"registered": len(registered), "source_ids": registered}


@router.post("/upload", summary="Multipart upload of files/folders to ingest")
async def upload_sources(
    files: list[UploadFile] = File(...),
    source_type: str = Form("local"),
    _tok: str = Depends(require_any_token),
):
    engine = get_engine()
    source_ids: list[str] = []
    async with engine.begin() as conn:
        for f in files:
            if not f.filename:
                continue
            data = await f.read()
            if not data:
                continue
            name = f.filename.replace("\\", "/").lstrip("/")
            sid = await _register_bytes(
                conn, file_path=name, file_name=name,
                data=data, source_type=source_type, source_url=None,
            )
            source_ids.append(sid)
    return {"registered": len(source_ids), "source_ids": source_ids}


class UrlRequest(BaseModel):
    url: str = Field(..., min_length=6, description="Public git/https source URL")


_REPO_URL = re.compile(
    r"^(https?://|git@).+\.git(\?.*)?$|^https?://(github\.com|gitlab\.com|bitbucket\.org)/",
    re.I,
)


@router.post("/url", summary="Register an external URL (public git repo) for ingestion")
async def add_url_source(payload: UrlRequest, _tok: str = Depends(require_any_token)):
    url = payload.url.strip()
    if not _REPO_URL.match(url):
        raise HTTPException(status_code=400, detail="Not a recognized public git/repo URL")
    # Stable scope id per URL so re-registration is idempotent.
    scope_id = str(uuid.uuid5(uuid.NAMESPACE_URL, url))
    repo_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()
    engine = get_engine()
    async with engine.begin() as conn:
        await enqueue_stage(conn, "clone", "github", scope_id, priority=0, payload={"url": url})
        # Create a live "repo marker" source row immediately so the repo shows
        # up in the Sources list while its clone task is queued/running. Its
        # status is synced to the clone task by `list_sources`.
        await conn.execute(
            text("""
                INSERT INTO sources
                    (drive_item_id, drive_id, source_type, source_url, file_path, file_name,
                     mime_type, size_bytes, sha256_hash, status, source_metadata)
                VALUES (:did, 'github', 'github', :url, :path, :name,
                        'application/vnd.git-repo', 0, :hash, 'queued',
                        CAST(:meta AS jsonb))
                ON CONFLICT (drive_item_id) DO UPDATE SET
                    source_url = EXCLUDED.source_url,
                    source_metadata = EXCLUDED.source_metadata,
                    updated_at = now()
            """),
            {
                "did": f"github-repo:{scope_id}",
                "url": url,
                "path": f"repos/{_repo_slug(url)}",
                "name": _repo_slug(url),
                "hash": repo_hash,
                "meta": json.dumps({
                    "github_url": url,
                    "github_scope_id": scope_id,
                    "clone_status": "queued",
                    "is_repo_marker": True,
                }),
            },
        )
    return {"status": "queued", "url": url, "clone_scope_id": scope_id}