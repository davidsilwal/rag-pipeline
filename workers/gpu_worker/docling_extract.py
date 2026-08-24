#!/usr/bin/env python3
"""workers/gpu_worker/docling_extract.py — Docling-backed extraction & chunking.

plan.md §6.3 (MIME Ingestion Matrix) routes rich documents through Docling:

  * Rich documents (``.pdf`` / ``.docx`` / ``.pptx`` / ``.epub`` / images)
    → `Docling <https://docling.ai>`_ → structured Markdown with reading order,
    tables, formulas and (for scans) OCR bounding boxes.
  * Plain text / code (``.md`` / ``.txt`` / ``.py`` / ``.json`` / ``.yaml`` …)
    → native UTF-8 decode.

Docling is imported lazily so a thin/CPU worker that never installed it still
extracts plain text and degrades to a UTF-8 decode for anything else instead of
failing the ``extract`` stage. The heavy models (OCR / table structure) are only
downloaded/loaded on the first conversion of a rich document.

The chunker half mirrors the RAG recipe on docling.ai: ``HybridChunker`` splits a
``DoclingDocument`` into structure-aware chunks and preserves each chunk's
``page_number`` / ``bbox_coords`` provenance so retrieval can point at the exact
region on the page.
"""

from __future__ import annotations

import logging
import mimetypes
import os
import tempfile
from pathlib import Path
from typing import Any

log = logging.getLogger("gpu_worker.docling_extract")

# Substrings that select the Docling path. ``image/*`` covers scanned pages and
# photos (OCR), per plan §6.3 "Scans / Images".
DOCLING_MIME_HINTS = (
    "application/pdf",
    "application/msword",  # .doc
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
    "application/vnd.ms-powerpoint",  # .ppt
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",  # .pptx
    "application/vnd.ms-excel",  # .xls
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # .xlsx
    "application/epub+zip",
    "text/html",
    "application/xhtml+xml",
    "image/",
)

# Plain text / code is never worth the Docling cost and always round-trips
# through a direct UTF-8 decode.
PLAIN_TEXT_MIME_HINTS = (
    "text/plain",
    "text/markdown",
    "text/x-",
    "application/json",
    "application/xml",
    "application/x-yaml",
    "application/yaml",
    "application/javascript",
    "application/x-python",
    "application/x-httpd-php",
)


def uses_docling(mime_type: str | None) -> bool:
    """Return True when this MIME type should be parsed by Docling."""
    mime = (mime_type or "").lower()
    if any(mime.startswith(h) for h in PLAIN_TEXT_MIME_HINTS):
        return False
    return any(mime.startswith(h) or h in mime for h in DOCLING_MIME_HINTS)


_DOCLING_AVAIL: int | None = None  # tri-state: None=unchecked, 1=yes, 0=no


def docling_available() -> bool:
    """True when the ``docling`` package can be imported. Cached after first call."""
    global _DOCLING_AVAIL
    if _DOCLING_AVAIL is not None:
        return _DOCLING_AVAIL == 1
    try:
        from docling.document_converter import DocumentConverter  # noqa: F401
        _DOCLING_AVAIL = 1
    except Exception:
        _DOCLING_AVAIL = 0
    return _DOCLING_AVAIL == 1


def _utf8_text(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Docling conversion plumbing (lazy import, temp-file staging)
# ---------------------------------------------------------------------------

# MIME-to-suffix override for mimetypes.guess_extension which often returns
# wrong extensions for vendor types (e.g. .ksh for application/javascript).
_MIME_SUFFIX_OVERRIDE: dict[str, str] = {
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/epub+zip": ".epub",
    "application/xhtml+xml": ".html",
    "application/javascript": ".js",
    "application/x-python": ".py",
    "application/json": ".json",
    "application/xml": ".xml",
    "application/x-yaml": ".yaml",
    "application/yaml": ".yaml",
    "text/html": ".html",
    "text/markdown": ".md",
    "text/plain": ".txt",
    "text/csv": ".csv",
}


def _stage_suffix(file_name: str, mime_type: str) -> str:
    """Pick a file extension so Docling can detect the input format."""
    suffix = ""
    if file_name:
        suffix = Path(file_name).suffix
    if not suffix and mime_type:
        mime_lower = mime_type.lower()
        # Use the override table first (more reliable than mimetypes.guess_extension
        # for vendor MIME types).
        suffix = _MIME_SUFFIX_OVERRIDE.get(mime_lower, "")
        if not suffix:
            suffix = mimetypes.guess_extension(mime_lower) or ""
    return suffix or ".bin"


def _stage_bytes(raw: bytes, suffix: str) -> str:
    fd, tmp_path = tempfile.mkstemp(prefix="docling-", suffix=suffix)
    with os.fdopen(fd, "wb") as fh:
        fh.write(raw)
    return tmp_path


def _unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _convert_to_docling_document(raw: bytes, file_name: str, mime_type: str):
    """Convert in-memory bytes to a ``DoclingDocument`` (temp file staged)."""
    from docling.document_converter import DocumentConverter

    tmp_path = _stage_bytes(raw, _stage_suffix(file_name, mime_type))
    try:
        converter = DocumentConverter()
        result = converter.convert(tmp_path)
        return result.document
    finally:
        _unlink(tmp_path)


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def extract_document(raw: bytes, mime_type: str = "", file_name: str = "") -> dict:
    """Extract text for one source, routing by MIME type (plan §6.3).

    Returns ``{"text", "engine", "chars"}`` where ``engine`` is:

    * ``"docling"``          — Docling parsed the document to structured Markdown
    * ``"utf-8"``            — plain-text fast path (no Docling involved)
    * ``"utf-8-fallback"``   — Docling unavailable/failed; raw UTF-8 decode so
                               the ``extract`` stage never fails spuriously
    """
    mime = (mime_type or "").lower()

    if not uses_docling(mime):
        text = _utf8_text(raw)
        return {"text": text, "engine": "utf-8", "chars": len(text)}

    if docling_available():
        try:
            doc = _convert_to_docling_document(raw, file_name, mime)
            markdown = doc.export_to_markdown()
            if markdown and markdown.strip():
                return {"text": markdown, "engine": "docling", "chars": len(markdown)}
            log.warning(
                "docling produced empty output for %s; falling back to UTF-8",
                file_name or mime,
            )
        except Exception as e:  # pragma: no cover - depends on model downloads
            log.warning(
                "docling extraction failed for %s (%s); falling back to UTF-8",
                file_name or mime,
                e,
            )

    text = _utf8_text(raw)
    return {"text": text, "engine": "utf-8-fallback", "chars": len(text)}


# ---------------------------------------------------------------------------
# Structure-aware chunking (page/bbox provenance)
# ---------------------------------------------------------------------------

def _bbox_to_dict(bbox: Any) -> dict:
    """Normalize a Docling ``BoundingBox`` to a JSON-friendly dict."""
    if hasattr(bbox, "as_tuple"):
        try:
            l, t, r, b = bbox.as_tuple()
            return {"l": float(l), "t": float(t), "r": float(r), "b": float(b)}
        except Exception:
            pass
    out: dict = {}
    for k in ("l", "t", "r", "b"):
        if hasattr(bbox, k):
            try:
                out[k] = float(getattr(bbox, k))
            except (TypeError, ValueError):
                pass
    return out


def _provenance(meta: Any) -> tuple[int | None, list | None]:
    """Extract ``(page_number, bboxes)`` from a chunk's ``DocMeta``.

    Docling stores provenance as ``doc_item.prov`` — a list of
    ``ProvenanceItem`` objects, each with ``.page_no`` and ``.bbox``
    attributes (or occasionally plain tuples). We surface the first
    page and all bounding boxes so a retrieved unit can highlight
    the exact page region.
    """
    page: int | None = None
    bboxes: list = []
    for item in getattr(meta, "doc_items", None) or []:
        for prov in getattr(item, "prov", None) or []:
            # Try attribute access first (ProvenanceItem), then tuple unpack.
            p_no = getattr(prov, "page_no", None)
            bx = getattr(prov, "bbox", None)
            if p_no is None and bx is None:
                try:
                    p_no, bx = prov  # type: ignore[misc]
                except (TypeError, ValueError):
                    continue
            if page is None and isinstance(p_no, int):
                page = p_no
            if bx is not None:
                bboxes.append(_bbox_to_dict(bx))
    return page, (bboxes or None)


def chunk_document(raw: bytes, mime_type: str = "", file_name: str = "") -> list[dict]:
    """Chunk a rich document with Docling's ``HybridChunker``.

    Returns one dict per chunk:

        {"heading_path", "raw_text", "clean_text", "page_number", "bbox_coords"}

    Raises when Docling (or its chunking extras — ``semchunk`` / a tokenizer)
    is unavailable or conversion fails; callers fall back to the heading-aware
    chunker on the already-extracted Markdown.
    """
    from docling.chunking import HybridChunker

    doc = _convert_to_docling_document(raw, file_name, mime_type)
    chunker = HybridChunker()

    out: list[dict] = []
    for chunk in chunker.chunk(doc):
        text = chunker.contextualize(chunk)
        meta = chunk.meta
        headings = list(getattr(meta, "headings", None) or [])
        page, bboxes = _provenance(meta)
        out.append(
            {
                "heading_path": headings,
                "raw_text": text,
                "clean_text": text,
                "page_number": page,
                "bbox_coords": bboxes,
            }
        )
    return out
