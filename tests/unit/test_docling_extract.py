#!/usr/bin/env python3
"""Unit tests for Docling-backed extraction (plan §6.3).

Pure routing + fallback logic only — no live Docling/Postgres required. The
Docling import is lazy, so these pass on hosts without ``docling`` installed.
"""

import pytest

from workers.gpu_worker.docling_extract import (
    _bbox_to_dict,
    _provenance,
    chunk_document,
    docling_available,
    extract_document,
    uses_docling,
)


# ---------------------------------------------------------------------------
# MIME routing (plan §6.3)
# ---------------------------------------------------------------------------

def test_plain_text_routes_to_utf8():
    for mime in ("text/plain", "text/markdown", "application/json",
                 "application/xml", "application/x-yaml"):
        assert uses_docling(mime) is False, mime


def test_source_code_routes_to_utf8():
    assert uses_docling("text/x-python") is False
    assert uses_docling("application/javascript") is False


def test_rich_documents_route_to_docling():
    for mime in (
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/epub+zip",
        "text/html",
    ):
        assert uses_docling(mime) is True, mime


def test_images_route_to_docling_ocr():
    assert uses_docling("image/png") is True
    assert uses_docling("image/tiff") is True


def test_unknown_mime_is_case_insensitive():
    assert uses_docling("Application/PDF") is True
    assert uses_docling(None) is False


# ---------------------------------------------------------------------------
# Extraction engines
# ---------------------------------------------------------------------------

def test_plain_text_extract_uses_utf8_engine():
    out = extract_document(b"# Hello\n\nworld", "text/markdown")
    assert out["engine"] == "utf-8"
    assert "# Hello" in out["text"]
    assert out["chars"] == len(out["text"])


def test_rich_doc_degrades_to_fallback_without_docling():
    # Without docling installed, rich docs degrade to utf-8-fallback rather than
    # raising — the extract stage must never fail spuriously.
    out = extract_document(b"%PDF-1.4 garbage bytes", "application/pdf", "report.pdf")
    assert out["engine"] in ("docling", "utf-8-fallback")
    assert isinstance(out["text"], str)


def test_empty_bytes_yield_empty_text():
    out = extract_document(b"", "application/pdf", "empty.pdf")
    assert out["chars"] == 0
    assert out["text"] == ""


# ---------------------------------------------------------------------------
# Chunk provenance helpers
# ---------------------------------------------------------------------------

class _BBox:
    def __init__(self, l, t, r, b):
        self.l, self.t, self.r, self.b = l, t, r, b

    def as_tuple(self):
        return (self.l, self.t, self.r, self.b)


class _DocItem:
    def __init__(self, prov):
        self.prov = prov


class _Meta:
    def __init__(self, doc_items):
        self.doc_items = doc_items


def test_bbox_to_dict():
    assert _bbox_to_dict(_BBox(0.1, 0.2, 0.9, 0.8)) == {
        "l": 0.1, "t": 0.2, "r": 0.9, "b": 0.8,
    }


def test_provenance_extracts_page_and_bboxes():
    meta = _Meta([_DocItem([(2, _BBox(0.0, 0.0, 1.0, 0.5))]),
                  _DocItem([(2, _BBox(0.0, 0.5, 1.0, 1.0))])])
    page, bboxes = _provenance(meta)
    assert page == 2
    assert len(bboxes) == 2
    assert bboxes[0]["b"] == 0.5


def test_provenance_tolerates_malformed_items():
    meta = _Meta([_DocItem(["not-a-tuple"]), _DocItem([(3, _BBox(0, 0, 1, 1))])])
    page, bboxes = _provenance(meta)
    assert page == 3
    assert len(bboxes) == 1


def test_chunk_document_raises_without_docling():
    if docling_available():
        pytest.skip("docling installed")
    with pytest.raises(Exception):
        chunk_document(b"%PDF-1.4", "application/pdf", "report.pdf")


def test_unit_schema_accepts_list_bboxes():
    """The Docling chunker emits bbox_coords as a list; /units must accept it."""
    from routers.units import UnitIn
    unit = UnitIn(
        doc_id="d", unit_index=0, unit_type="docling_chunk",
        raw_text="r", clean_text="c", content_hash="a" * 64,
        page_number=2,
        bbox_coords=[{"l": 0, "t": 0, "r": 1, "b": 0.5}],
    )
    assert isinstance(unit.bbox_coords, list)
    assert unit.page_number == 2
