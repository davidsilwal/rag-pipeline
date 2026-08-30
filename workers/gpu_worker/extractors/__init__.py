#!/usr/bin/env python3
"""workers/gpu_worker/extractors/__init__.py — Extractors for code-to-wiki (§7.5).

The plan calls for tree-sitter-based AST extraction, Docling fallback, and
Markdown/code summarization rather than raw body passthrough.

The `code_to_wiki` package is not a hard dependency of this pipeline, so the
imports below are guarded: when the package happens to be installed its
extractors are exposed, otherwise the module still imports cleanly and the
docling-based extractor handles rich documents. This keeps the stage import
from hard-failing on a thin worker.
"""

from __future__ import annotations

try:
    from code_to_wiki.extractors import (  # noqa: F401  (optional)
        CodeSampler,
        Extractor,
        MarkdownCollector,
        ShapesCollector,
    )
except Exception:  # noqa: BLE001  # optional dependency not installed
    pass  # code-to-wiki extraction is optional; docling covers rich docs

__all__ = [
    "CodeSampler",
    "Extractor",
    "MarkdownCollector",
    "ShapesCollector",
]