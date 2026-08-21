#!/usr/bin/env python3
"""workers/gpu_worker/source_ingest.py — Ingest GitHub repos and local folders."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Iterable

from workers.gpu_worker.discovery import discover


def _iter_local_files(root: Path) -> Iterable[Path]:
    for dirpath, _, filenames in os.walk(root, topdown=True):
        for fn in filenames:
            yield Path(dirpath) / fn


def _ensure_git() -> None:
    if shutil.which("git") is None:
        raise RuntimeError("git is required for GitHub ingestion")


def _clone_github_repo(repo_url: str, dest: Path) -> Path:
    _ensure_git()
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    os.system(f"git clone --depth 1 {repo_url} {dest} >/tmp/git_clone.log 2>&1")
    return dest


def _safe_repo_name(url: str) -> str:
    return url.rstrip("/").split("/")[-1] or "repo"


def ingest(source_type: str, source_url: str | None, workspace_root: Path) -> list[dict[str, object]]:
    if source_type == "github":
        if not source_url or not str(source_url).startswith("https://github.com/"):
            raise ValueError("source_type=github requires a https://github.com/... URL")
        repo_dir = workspace_root / "_ingest" / _safe_repo_name(str(source_url))
        _clone_github_repo(str(source_url), repo_dir)
        return discover(repo_dir)
    if source_type == "local":
        local_path = Path(str(source_url or "/workspace"))
        return discover(local_path)
    raise ValueError(f"Unsupported source_type: {source_type}")
