#!/usr/bin/env python3
"""Export endpoint — generates an organized ZIP with wiki pages and knowledge graph.

Features:
- Filtered export: by prefix, page_type, entity_types, source, domain
- Content-Length header for download progress tracking
- Scheduled export: background task stores weekly exports on disk
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse, Response
from sqlalchemy import text

from database import get_engine
from deps import require_any_token

log = logging.getLogger("export")

router = APIRouter(prefix="/export", tags=["export"])

MAX_WIKI_CHARS_PER_PAGE = 50000

# Scheduled export directory
EXPORT_DIR = Path("/data/exports")
EXPORT_KEEP = 4  # keep last N weekly exports

VALID_ENTITY_TYPES = {"concept", "technology", "event", "org", "person", "location", "other"}


def _slugify(name: str) -> str:
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug[:80] or "unnamed"


def _safe_filename(title: str) -> str:
    return _slugify(title)[:80] or "untitled"


async def _build_export_zip(
    conn,
    *,
    prefix: str | None = None,
    page_type: str | None = None,
    entity_types: list[str] | None = None,
    source_id: str | None = None,
    domain: str | None = None,
    max_pages: int = 5000,
    max_entities: int = 2000,
    max_relationships: int = 1000,
    max_communities: int = 200,
) -> bytes:
    """Build the ZIP in memory and return bytes."""
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        wiki_count = 0
        projects: dict[str, int] = {}

        # --- Wiki Pages (streamed in batches) ---
        where_clauses = []
        params: dict = {"lim": max_pages}

        if prefix:
            folder = prefix.rstrip("/")
            where_clauses.append("(file_path = :pfx OR file_path LIKE :pfx_slash)")
            params["pfx"] = folder
            params["pfx_slash"] = f"{folder}/%"
        if page_type:
            where_clauses.append("page_type = :pt")
            params["pt"] = page_type
        if source_id:
            where_clauses.append("source_id = :sid")
            params["sid"] = source_id
        if domain:
            where_clauses.append("domain = :dom")
            params["dom"] = domain

        where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        rows = await conn.execute(text(f"""
            SELECT page_id, file_path, title, page_type, domain, status,
                   left(markdown_body, :max_chars) AS markdown_body, updated_at
            FROM wiki_pages
            {where_sql}
            ORDER BY file_path
            LIMIT :lim
        """), {**params, "max_chars": MAX_WIKI_CHARS_PER_PAGE})

        for page in rows.mappings():
            title = page["title"] or "Untitled"
            file_path = page["file_path"] or ""
            md_body = page["markdown_body"] or ""
            page_type_val = page["page_type"] or "page"
            page_domain = page["domain"] or ""

            parts = file_path.replace("\\", "/").split("/")
            project = _slugify(parts[-2]) if len(parts) > 1 else _slugify(page_type_val) or "pages"

            filename = _safe_filename(title) + ".md"
            rel_path = f"wiki/{project}/{filename}"

            fm = ["---", f'title: "{title}"', f"page_type: {page_type_val}"]
            if page_domain:
                fm.append(f"domain: {page_domain}")
            if page["updated_at"]:
                fm.append(f"updated: {page['updated_at'].isoformat()}")
            if file_path:
                fm.append(f'source: "{file_path}"')
            fm.append("---")

            content = "\n".join(fm) + "\n\n"
            content += md_body if md_body.strip() else "*No content available.*\n"
            zf.writestr(rel_path, content)

            projects[project] = projects.get(project, 0) + 1
            wiki_count += 1

        # --- Knowledge Graph: Entities by type ---
        et_where = ""
        et_params: dict = {"lim": max_entities}
        if entity_types:
            placeholders = ", ".join(f":et{i}" for i in range(len(entity_types)))
            et_where = f" WHERE entity_type IN ({placeholders})"
            for i, et in enumerate(entity_types):
                et_params[f"et{i}"] = et

        entity_rows = await conn.execute(text(f"""
            SELECT name, entity_type, description, frequency
            FROM graphrag_entities
            {et_where}
            ORDER BY frequency DESC, name
            LIMIT :lim
        """), et_params)
        entities = entity_rows.mappings().all()

        entities_by_type: dict[str, list] = {}
        for e in entities:
            etype = e["entity_type"] or "other"
            entities_by_type.setdefault(etype, []).append(e)

        for etype, elist in entities_by_type.items():
            type_dir = _slugify(etype)
            for e in elist:
                name = e["name"] or "unnamed"
                desc = e["description"] or "*No description.*"
                freq = e["frequency"] or 0
                content = f'---\ntitle: "{name}"\ntype: {etype}\nfrequency: {freq}\n---\n\n# {name}\n\n**Type:** {etype} | **Mentions:** {freq}\n\n{desc}\n'
                zf.writestr(f"knowledge-graph/entities/{type_dir}/{_safe_filename(name)}.md", content)

            # Type index
            lines = [f"# {etype.title()} Entities", "", f"Total: {len(elist)}", "", "| Name | Freq | Description |", "|------|------|-------------|"]
            for e in elist[:100]:
                lines.append(f"| [{e['name']}]({_safe_filename(e['name'])}.md) | {e['frequency'] or 0} | {(e['description'] or '')[:60]} |")
            zf.writestr(f"knowledge-graph/entities/{type_dir}/README.md", "\n".join(lines) + "\n")

        # --- Relationships ---
        rel_rows = await conn.execute(text("""
            SELECT e1.name as source_name, e2.name as target_name,
                   r.relationship_type, r.description, r.weight
            FROM graphrag_relationships r
            JOIN graphrag_entities e1 ON r.source_entity_id = e1.entity_id
            JOIN graphrag_entities e2 ON r.target_entity_id = e2.entity_id
            ORDER BY r.weight DESC
            LIMIT :lim
        """), {"lim": max_relationships})
        relationships = rel_rows.mappings().all()

        rel_lines = ["# Relationships", "", f"Total: {len(relationships)}", "", "| Source | Target | Type | Weight | Description |", "|--------|--------|------|--------|-------------|"]
        for r in relationships:
            rel_lines.append(f"| {r['source_name']} | {r['target_name']} | {r['relationship_type']} | {round(float(r['weight']) if r['weight'] else 1.0, 2)} | {(r['description'] or '')[:50]} |")
        zf.writestr("knowledge-graph/relationships.md", "\n".join(rel_lines) + "\n")

        # --- Communities ---
        comm_rows = await conn.execute(text("""
            SELECT title, summary, member_entities, level
            FROM graphrag_communities
            ORDER BY array_length(member_entities, 1) DESC NULLS LAST
            LIMIT :lim
        """), {"lim": max_communities})
        communities = comm_rows.mappings().all()

        cl = ["# Communities", "", f"Total: {len(communities)}", ""]
        for c in communities:
            title = c["title"] or "Untitled"
            summary = c["summary"] or ""
            members = c["member_entities"] or []
            cl.append(f"## {title}\n")
            cl.append(f"**Level:** {c['level'] or 0} | **Members:** {len(members)}\n")
            if summary:
                cl.append(f"{summary}\n")
            if members:
                cl.append("**Members:**\n")
                for m in members:
                    if isinstance(m, str):
                        cl.append(f"- {m}")
            cl.append("\n---\n")
        zf.writestr("knowledge-graph/communities.md", "\n".join(cl) + "\n")

        # --- Graph JSON ---
        graph_data = {
            "entities": [{"name": e["name"], "type": e["entity_type"], "description": e["description"], "frequency": e["frequency"]} for e in entities],
            "relationships": [{"source": r["source_name"], "target": r["target_name"], "type": r["relationship_type"], "weight": float(r["weight"]) if r["weight"] else 1.0} for r in relationships],
            "communities": [{"title": c["title"], "summary": c["summary"], "members": c["member_entities"] or []} for c in communities],
        }
        zf.writestr("knowledge-graph/graph.json", json.dumps(graph_data, indent=2, default=str))

        # --- GraphML ---
        gm = ['<?xml version="1.0" encoding="UTF-8"?>', '<graphml xmlns="http://graphml.graphstruct.org/graphml">', '<graph id="kg" edgedefault="undirected">']
        for e in entities:
            ne = (e["name"] or "").replace("&", "&amp;").replace("<", "&lt;")
            gm.append(f'  <node id="{_slugify(ne)}" label="{ne}"><data key="type">{e["entity_type"]}</data></node>')
        for r in relationships:
            gm.append(f'  <edge source="{_slugify(r["source_name"] or "")}" target="{_slugify(r["target_name"] or "")}" label="{r["relationship_type"]}"/>')
        gm.append("</graph></graphml>")
        zf.writestr("knowledge-graph/graph.graphml", "\n".join(gm))

        # --- Knowledge Graph README ---
        kg_lines = ["# Knowledge Graph", "", f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}", "", "## Stats", "",
            f"- **Entities:** {len(entities)}", f"- **Relationships:** {len(relationships)}", f"- **Communities:** {len(communities)}", "", "## Entity Types", "", "| Type | Count |", "|------|-------|"]
        for etype, elist in sorted(entities_by_type.items()):
            kg_lines.append(f"| {etype} | {len(elist)} |")
        zf.writestr("knowledge-graph/README.md", "\n".join(kg_lines) + "\n")

        # --- Main README ---
        readme = ["# Wiki & Knowledge Graph Export", "", f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}", "",
            "## Wiki Pages", "", f"Total: {wiki_count} pages across {len(projects)} projects", "", "| Project | Pages |", "|---------|-------|"]
        for p, c in sorted(projects.items()):
            readme.append(f"| [{p}](wiki/{p}/) | {c} |")
        readme.extend(["", "## Knowledge Graph", "", f"- **Entities:** {len(entities)}", f"- **Relationships:** {len(relationships)}", f"- **Communities:** {len(communities)}", "",
            "## Structure", "", "```", "wiki-export/", "├── README.md", "├── wiki/{project}/{page}.md", "└── knowledge-graph/",
            "    ├── README.md", "    ├── entities/{type}/{name}.md", "    ├── relationships.md", "    ├── communities.md",
            "    ├── graph.json", "    └── graph.graphml", "```\n"])
        zf.writestr("README.md", "\n".join(readme) + "\n")

    return zip_buffer.getvalue()


@router.get("/zip", summary="Export wiki + knowledge graph as organized ZIP")
async def export_zip(
    prefix: str | None = Query(None, description="Filter wiki pages by file path prefix (e.g. 'kubernetes')"),
    page_type: str | None = Query(None, description="Filter wiki pages by type (e.g. 'page', 'redirect')"),
    entity_types: str | None = Query(None, description="Comma-separated entity types to include (e.g. 'technology,org')"),
    source_id: str | None = Query(None, description="Filter wiki pages by source ID"),
    domain: str | None = Query(None, description="Filter wiki pages by domain"),
    _tok: str = Depends(require_any_token),
):
    engine = get_engine()
    async with engine.connect() as conn:
        et_list = [t.strip() for t in entity_types.split(",")] if entity_types else None
        zip_bytes = await _build_export_zip(
            conn,
            prefix=prefix,
            page_type=page_type,
            entity_types=et_list,
            source_id=source_id,
            domain=domain,
        )

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="wiki-kg-export-{ts}.zip"',
            "Content-Length": str(len(zip_bytes)),
        },
    )


# ---------------------------------------------------------------------------
# Scheduled exports — background task stores weekly exports on disk
# ---------------------------------------------------------------------------

async def run_scheduled_export() -> str | None:
    """Build and store a full export ZIP. Returns the file path or None on error."""
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            zip_bytes = await _build_export_zip(conn)

        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        filename = f"wiki-kg-export-{ts}.zip"
        filepath = EXPORT_DIR / filename
        filepath.write_bytes(zip_bytes)

        # Prune old exports (keep last N)
        exports = sorted(EXPORT_DIR.glob("wiki-kg-export-*.zip"))
        while len(exports) > EXPORT_KEEP:
            old = exports.pop(0)
            old.unlink(missing_ok=True)
            log.info("pruned old export: %s", old.name)

        log.info("scheduled export saved: %s (%d bytes)", filepath, len(zip_bytes))
        return str(filepath)
    except Exception as e:
        log.warning("scheduled export failed: %s", e)
        return None


@router.get("/scheduled", summary="List stored scheduled exports")
async def list_scheduled_exports(
    _tok: str = Depends(require_any_token),
):
    """List all stored scheduled export ZIPs."""
    if not EXPORT_DIR.exists():
        return {"exports": []}

    exports = []
    for f in sorted(EXPORT_DIR.glob("wiki-kg-export-*.zip"), reverse=True):
        stat = f.stat()
        exports.append({
            "filename": f.name,
            "size_bytes": stat.st_size,
            "created_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        })
    return {"exports": exports}


@router.get("/scheduled/{filename}", summary="Download a stored scheduled export")
async def download_scheduled_export(
    filename: str,
    _tok: str = Depends(require_any_token),
):
    """Download a previously generated scheduled export ZIP."""
    filepath = EXPORT_DIR / filename
    if not filepath.exists() or not filepath.name.startswith("wiki-kg-export-"):
        return Response(status_code=404, content='{"error": "Export not found"}', media_type="application/json")

    zip_bytes = filepath.read_bytes()
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(zip_bytes)),
        },
    )


@router.post("/trigger", summary="Manually trigger a scheduled export now")
async def trigger_export(
    _tok: str = Depends(require_any_token),
):
    """Trigger an immediate export and save to disk."""
    filepath = await run_scheduled_export()
    if filepath:
        return {"status": "ok", "file": filepath}
    return Response(status_code=500, content='{"error": "Export failed"}', media_type="application/json")
