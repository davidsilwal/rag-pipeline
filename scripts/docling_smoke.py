#!/usr/bin/env python3
"""Smoke test: create a small PDF, run it through Docling extract + chunk, verify output."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "workers"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "workers", "gpu_worker"))

# --- Step 1: Generate a tiny PDF with pypdfium2's writer (already installed via docling) ---
from pypdfium2 import PdfDocument
import pypdfium2.raw as pdfium_c

# Use a simpler approach: create a PDF manually
pdf_bytes = None

# Try creating with reportlab if available, else write minimal PDF by hand
try:
    from reportlab.pdfgen import canvas
    import io
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    # Use bold 16pt for the title so HybridChunker detects it as a heading
    c.setFont("Helvetica-Bold", 16)
    c.drawString(72, 750, "Introduction to Knowledge Graphs")
    c.setFont("Helvetica", 12)
    c.drawString(72, 730, "Section 1: What is a Knowledge Graph?")
    c.drawString(72, 710, "A knowledge graph is a structured representation of information.")
    c.drawString(72, 690, "It encodes entities and their relationships in a graph format.")
    c.setFont("Helvetica-Bold", 14)
    c.drawString(72, 670, "Section 2: Building a Knowledge Graph")
    c.setFont("Helvetica", 12)
    c.drawString(72, 650, "The first step is entity extraction from source documents.")
    c.drawString(72, 630, "Next, we resolve coreferences and link entities across sources.")
    c.drawString(72, 610, "Finally, the graph is stored and made queryable via an API.")
    c.save()
    pdf_bytes = buf.getvalue()
    print("✅ PDF created with reportlab")
except ImportError:
    print("⚠️  reportlab not available, trying minimal PDF...")
    # Minimal valid PDF
    pdf_bytes = b"""%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj
4 0 obj
<</Length 260>>
stream
BT
/F1 12 Tf
72 750 Td
(Introduction to Knowledge Graphs) Tj
0 -20 Td
(Section 1: What is a Knowledge Graph?) Tj
0 -20 Td
(A knowledge graph is a structured representation of information.) Tj
0 -20 Td
(It encodes entities and their relationships in a graph format.) Tj
0 -20 Td
(Section 2: Building a Knowledge Graph) Tj
0 -20 Td
(The first step is entity extraction from source documents.) Tj
ET
endstream
endobj
5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj
xref
0 6
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000115 00000 n 
0000000266 00000 n 
0000000578 00000 n 
trailer<</Size 6/Root 1 0 R>>
startxref
655
%%EOF"""
    print("✅ PDF created (minimal)")

assert pdf_bytes and len(pdf_bytes) > 100, f"PDF creation failed: {len(pdf_bytes or b'')} bytes"

# --- Step 2: Test extract_document ---
from docling_extract import extract_document
result = extract_document(pdf_bytes, "application/pdf", "test_kg.pdf")
print(f"\n--- Extract ---")
print(f"engine:    {result['engine']}")
print(f"chars:     {result['chars']}")
print(f"text[:300]: {result['text'][:300]}")

# Verify it's not garbled
assert "Knowledge" in result["text"] or "knowledge" in result["text"], f"Expected 'Knowledge' in extracted text, got: {result['text'][:100]}"
assert result["engine"] == "docling", f"Expected docling engine, got: {result['engine']}"
assert result["chars"] > 50, f"Too few chars: {result['chars']}"
print("✅ extract_document works with real PDF")

# --- Step 3: Test chunk_document ---
from docling_extract import chunk_document
chunks = chunk_document(pdf_bytes, "application/pdf", "test_kg.pdf")
print(f"\n--- Chunk ---")
print(f"num chunks: {len(chunks)}")
for i, ch in enumerate(chunks):
    print(f"  [{i}] heading={ch['heading_path']} len={len(ch['raw_text'])}")
    if ch.get("page_number"):
        print(f"       page={ch['page_number']} bbox={ch.get('bbox_coords')}")

assert len(chunks) >= 1, f"Expected at least 1 chunk, got {len(chunks)}"
assert any(ch["heading_path"] for ch in chunks), "No heading_path in any chunk"
assert any(ch.get("page_number") for ch in chunks), "No page_number in any chunk"
# Verify all required keys the runner expects
for ch in chunks:
    for key in ("heading_path", "raw_text", "clean_text"):
        assert key in ch, f"Missing '{key}' in chunk"
print("✅ chunk_document produces chunks with provenance")

# --- Step 4: Verify chunks are compatible with UnitIn schema ---
from pydantic import BaseModel
from typing import Optional
class UnitIn(BaseModel):
    source_id: Optional[str] = None
    doc_id: str
    unit_index: int
    heading_path: list[str] = []
    unit_type: str = "text"
    raw_text: str
    clean_text: str
    content_hash: str
    bbox_coords: dict | list | None = None
    page_number: Optional[int] = None

# Simulate what runner.py handle_chunk does: add doc_id, unit_index, etc.
for i, ch in enumerate(chunks):
    u = UnitIn(
        source_id="00000000-0000-0000-0000-000000000000",
        doc_id="00000000-0000-0000-0000-000000000000",
        unit_index=i,
        heading_path=ch["heading_path"],
        unit_type="docling_chunk",
        raw_text=ch["raw_text"],
        clean_text=ch["clean_text"],
        content_hash="a" * 64,
        bbox_coords=ch.get("bbox_coords"),
        page_number=ch.get("page_number"),
    )
print(f"\n✅ All {len(chunks)} chunks are valid UnitIn instances")

# --- Step 5: Test fallback (simulate missing docling) ---
import docling_extract as de
original_fn = de.docling_available
original_cache = de._DOCLING_AVAIL
de.docling_available = lambda: False
de._DOCLING_AVAIL = 0  # reset cache so the lambda is consulted
fallback = extract_document(pdf_bytes, "application/pdf", "test.pdf")
assert fallback["engine"] == "utf-8-fallback", f"Fallback engine: {fallback['engine']}"
print("✅ Fallback mode works when docling unavailable")
de.docling_available = original_fn
de._DOCLING_AVAIL = original_cache

# --- Step 6: Test non-PDF MIME type goes native ---
plain = extract_document(b"hello world", "text/plain", "readme.txt")
assert plain["engine"] == "utf-8"
assert plain["text"] == "hello world"
print("✅ Plain text uses native engine")

print("\n🎉 ALL SMOKE TESTS PASSED")
