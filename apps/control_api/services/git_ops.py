#!/usr/bin/env python3
"""apps/control_api/services/git_ops.py — Git commit, push, branch, and re-indexing hooks."""

import json
import os
from pathlib import Path
from typing import Optional

from database import get_engine
from models import WikiPage, WikiChunk


class GitPublisher:
    def __init__(self, repo_path: str = "/var/data/wiki"):
        self.repo_path = Path(repo_path)
        self.git_dir = self.repo_path / ".git"

    def _run(self, *args, cwd: Optional[str] = None) -> str:
        cwd = cwd or str(self.repo_path)
        import subprocess
        return subprocess.check_output(
            ["git"] + list(args),
            cwd=cwd,
            stderr=subprocess.STDOUT,
        ).decode("utf-8", errors="replace")

    def commit_page(self, page: dict, upsert_db: bool = False) -> str:
        """Compile a Markdown page, commit to the Git repo, return page_id for pgvector upsert."""
        title = page["title"]
        file_path = page["file_path"]
        frontmatter = page.get("frontmatter", {})
        md_body = page["markdown_body"]

        # Write frontmatter + markdown
        page_file = self.repo_path / file_path
        page_file.parent.mkdir(parents=True, exist_ok=True)
        page_file.write_text(f"---\n{json.dumps(frontmatter, indent=2)}\n---\n{md_body}", encoding="utf-8")

        # Git add + commit + push
        self._run("add", str(page_file.relative_to(self.repo_path)))
        commit_msg = f"docs: update {file_path} [{title}]"
        self._run("commit", "-m", commit_msg)

        # Push (assume remote configured; in Colab/Deepnote runner may handle via Control API)
        try:
            self._run("push")
        except Exception:
            pass  # Non-fatal if push not configured in this env

        page_id = page.get("page_id", "")
        if upsert_db:
            # Return page_id for the DB; the caller can upsert wiki_chunks
            pass

        return page_id

    def get_repo_status(self) -> dict:
        try:
            out = self._run("status", "--porcelain")
            return {"has_changes": bool(out.strip()), "summary": out}
        except Exception:
            return {"has_changes": False, "summary": ""}