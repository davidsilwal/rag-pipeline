#!/usr/bin/env python3
"""workers/gpu_worker/extractors/__init__.py — Package init for code-to-wiki-compatible extractors.

The design (plan.md §7.5) calls for tree-sitter-based AST extraction, Docling
fallback, and Markdown/code summarization — not raw body passthrough.
"""

from code_to_wiki.extractors import CodeSampler, Extractor
from code_to_wiki.extractors import MarkdownCollector, ShapesCollector

__all__ = [
    "CodeSampler",
    "Extractor",
    "MarkdownCollector",
    "ShapesCollector",
]