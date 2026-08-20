#!/usr/bin/env python3
"""workers/gpu_worker/discovery.py — Recursive discovery & content classifier (§7)."""

from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

# Package logger stub; replace with stdlib logging in production.
def logger():
    import logging
    return logging.getLogger("gpu_worker")

DEFAULT_RULES_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "policies", "discovery_rules.yaml")


def _load_rules(path: str | None = None) -> dict:
    p = path or os.getenv("DISCOVERY_RULES", DEFAULT_RULES_PATH)
    with open(p, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _mime(path: Path) -> str:
    import mimetypes
    m, _ = mimetypes.guess_type(str(path))
    return m or "application/octet-stream"


_IGNORE_DIRS: set[str] = set()
_IGNORE_FILES: list[str] = []
_WHITELIST: list[str] = []
_PROJECT_MARKERS: list[dict] = []


def _compile_rules(rules: dict) -> None:
    global _IGNORE_DIRS, _IGNORE_FILES, _WHITELIST, _PROJECT_MARKERS
    _IGNORE_DIRS = set(rules.get("ignore_dirs", []))
    _IGNORE_FILES = [p for p in rules.get("ignore_files", [])]
    _WHITELIST = rules.get("whitelist", [])
    _PROJECT_MARKERS = rules.get("custom_project_markers", [])


def _is_ignored_dir(name: str, rel: str) -> bool:
    if any(rel == w.rstrip("/*").replace("**/", "") or rel.startswith(w.replace("**/", "").rstrip("/")) for w in _WHITELIST):
        return False
    return name in _IGNORE_DIRS


def _is_ignored_file(name: str) -> bool:
    import fnmatch
    for pat in _IGNORE_FILES:
        if fnmatch.fnmatch(name, pat):
            return True
    return False


_MARKER_MAP = {
    ".git": ("git_repository", None),
    "package.json": ("source_code", "javascript/typescript"),
    "pyproject.toml": ("source_code", "python"),
    "requirements.txt": ("source_code", "python"),
    "go.mod": ("source_code", "go"),
    "Cargo.toml": ("source_code", "rust"),
    "pom.xml": ("source_code", "java/kotlin"),
    "build.gradle": ("source_code", "java/kotlin"),
    "build.gradle.kts": ("source_code", "java/kotlin"),
    "Makefile": ("source_code", "c/cpp"),
    "CMakeLists.txt": ("source_code", "c/cpp"),
    "Dockerfile": ("infrastructure", "docker"),
    "docker-compose.yml": ("infrastructure", "docker"),
}


def _fingerprint(directory: Path) -> tuple[str, str | None, str | None]:
    project_type = "unclassified"
    language = "unknown"
    for marker, (pt, lang) in _MARKER_MAP.items():
        if (directory / marker).exists():
            project_type = pt
            language = lang or language
    for cm in _PROJECT_MARKERS:
        if (directory / cm.get("marker", "")).exists():
            project_type = cm.get("project_type", project_type)
    return project_type, language, str(directory)


_CONTENT_CLASSIFIERS = [
    (re.compile(r"^(ADR-|RFC-|ARCHITECTURE|DESIGN)", re.I), "architecture_doc", 0),
    (re.compile(r"^(README|CONTRIBUTING|CHANGELOG)", re.I), "readme_overview", 1),
    (re.compile(r"^(openapi|swagger|graphql)\.(ya?ml|json)$", re.I), "api_specification", 0),
    (re.compile(r"^(FRD|SRS|PRD)-", re.I), "requirements_spec", 0),
    (re.compile(r"^(runbook|playbook|troubleshooting)", re.I), "runbook_ops", 1),
    (re.compile(r"\.(py|ts|cs|java|go|rs)$"), "source_code", 2),
    (re.compile(r"\.(tf|tfvars)$"), "config_iac", 1),
    (re.compile(r"^schema\.(sql|prisma)$|migrations/", re.I), "data_schema", 2),
    (re.compile(r"\.(eml|msg|html)$"), "communication", 2),
    (re.compile(r"\.(png|jpg|tiff|svg|drawio|mermaid)$"), "asset_media", 3),
]


def _classify(path: Path, project_type: str, parent: str) -> tuple[str, str]:
    name = path.name
    parts = [p.lower() for p in path.parts]
    if any(t in parts for t in ["test", "tests", "__tests__", "spec"]) or re.search(r"(_test|test_|\.spec\.|\.test\.)", name):
        return "test_code", "P3"
    if any(t in parts for t in ["docs", "manual", "wiki", "guides", "knowledge"]):
        if re.search(r"\.(png|jpg|svg|tiff|drawio|mermaid)$", name):
            return "asset_media", "P3"
        if re.search(r"\.(md|rst|txt)$", name):
            return "wiki_knowledge", "P0"
        if re.search(r"\.(pdf|docx)$", name) and re.search(r"^(FRD|SRS|PRD)", name, re.I):
            return "requirements_spec", "P0"
    for pat, cls, prio in _CONTENT_CLASSIFIERS:
        if pat.search(name) or pat.search(str(path)):
            return cls, f"P{prio}"
    if path.suffix.lower() in {".md", ".txt", ".rst"}:
        return "wiki_knowledge", "P0"
    return "unclassified", "P3"


def discover(root: str | Path, rules_path: str | None = None) -> list[dict[str, Any]]:
    root = Path(root)
    rules = _load_rules(rules_path)
    _compile_rules(rules)
    manifest: list[dict[str, Any]] = []
    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        rel = os.path.relpath(dirpath, root)
        if rel == ".":
            rel = ""
        dirnames[:] = [d for d in dirnames if not _is_ignored_dir(d, os.path.join(rel, d) if rel else d)]
        project_type, language, repo_root = _fingerprint(Path(dirpath))
        for fn in filenames:
            if _is_ignored_file(fn):
                continue
            p = Path(dirpath) / fn
            relp = p.relative_to(root)
            content_class, priority = _classify(p, project_type, rel)
            manifest.append(
                {
                    "file_path": str(relp),
                    "file_name": fn,
                    "mime_type": _mime(p),
                    "size_bytes": p.stat().st_size,
                    "sha256_hash": _sha256_file(p),
                    "source_metadata": {
                        "discovery": {
                            "input_root": str(root),
                            "project_type": project_type,
                            "language_ecosystem": language,
                            "repo_root": repo_root,
                            "content_class": content_class,
                            "extraction_priority": priority,
                            "discovery_timestamp": __import__("datetime").datetime.now(timezone.utc).isoformat(),
                        }
                    },
                }
            )
    return manifest
