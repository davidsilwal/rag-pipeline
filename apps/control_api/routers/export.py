#!/usr/bin/env python3
"""Export endpoint — organized exports of wiki pages, knowledge graph, and per-cluster bundles.

Full export (all data) and per-cluster exports (markdown, JSON, GraphML, context pack).
"""
from __future__ import annotations

import io
import json
import logging
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path as FastAPIPath, Query
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import text

from database import get_engine
from deps import require_any_token
from services.cluster_export import (
    build_context_pack,
    export_cluster_json,
    export_cluster_graphml,
    export_cluster_markdown,
    get_cluster_graph,
    get_cluster_sources,
    get_cluster_zip,
    list_clusters,
)

log = logging.getLogger("export")

router = APIRouter(prefix="/export", tags=["export"])

MAX_WIKI_CHARS_PER_PAGE = 50000
WIKI_BATCH_SIZE = 2000  # fetch pages in batches to avoid memory spikes

# Scheduled export directory
EXPORT_DIR = Path("/data/exports")
EXPORT_KEEP = 4  # keep last N weekly exports


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
) -> bytes:
    """Build the ZIP in memory with ALL data — no limits."""
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        wiki_count = 0
        projects: dict[str, int] = {}

        # --- Wiki Pages (ALL, batched) ---
        where_clauses = []
        params: dict = {}

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

        # Count total pages first
        count_row = await conn.execute(
            text(f"SELECT count(*) FROM wiki_pages {where_sql}"), params
        )
        total_pages = count_row.scalar() or 0
        log.info("export: %d total wiki pages to export", total_pages)

        # Fetch in batches using OFFSET/LIMIT
        offset = 0
        while offset < total_pages:
            batch_params = {**params, "offset": offset, "batch": WIKI_BATCH_SIZE, "max_chars": MAX_WIKI_CHARS_PER_PAGE}
            rows = await conn.execute(text(f"""
                SELECT page_id, file_path, title, page_type, domain, status,
                       left(markdown_body, :max_chars) AS markdown_body, updated_at
                FROM wiki_pages
                {where_sql}
                ORDER BY file_path
                OFFSET :offset LIMIT :batch
            """), batch_params)

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

            offset += WIKI_BATCH_SIZE

        log.info("export: wrote %d wiki pages across %d projects", wiki_count, len(projects))

        # --- Knowledge Graph: ALL Entities by type ---
        et_where = ""
        et_params: dict = {}
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
        """), et_params)
        entities = entity_rows.mappings().all()

        log.info("export: %d entities", len(entities))

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

            # Type index (truncate to 200 per index page to keep ZIP manageable)
            lines = [f"# {etype.title()} Entities", "", f"Total: {len(elist)}", "", "| Name | Freq | Description |", "|------|------|-------------|"]
            for e in elist[:200]:
                lines.append(f"| [{e['name']}]({_safe_filename(e['name'])}.md) | {e['frequency'] or 0} | {(e['description'] or '')[:60]} |")
            if len(elist) > 200:
                lines.append(f"\n*... and {len(elist) - 200} more*")
            zf.writestr(f"knowledge-graph/entities/{type_dir}/README.md", "\n".join(lines) + "\n")

        # --- ALL Relationships ---
        rel_rows = await conn.execute(text("""
            SELECT e1.name as source_name, e2.name as target_name,
                   r.relationship_type, r.description, r.weight
            FROM graphrag_relationships r
            JOIN graphrag_entities e1 ON r.source_entity_id = e1.entity_id
            JOIN graphrag_entities e2 ON r.target_entity_id = e2.entity_id
            ORDER BY r.weight DESC
        """))
        relationships = rel_rows.mappings().all()

        log.info("export: %d relationships", len(relationships))

        # Split relationships into pages of 500 to avoid giant markdown files
        REL_PAGE_SIZE = 500
        total_rel_pages = (len(relationships) + REL_PAGE_SIZE - 1) // REL_PAGE_SIZE
        for page_idx in range(total_rel_pages):
            start = page_idx * REL_PAGE_SIZE
            end = min(start + REL_PAGE_SIZE, len(relationships))
            page_rels = relationships[start:end]

            rel_lines = []
            if page_idx == 0:
                rel_lines = ["# Relationships", "", f"Total: {len(relationships)}", ""]
            
            rel_lines.append(f"## Page {page_idx + 1}/{total_rel_pages}" if total_rel_pages > 1 else "")
            rel_lines.extend(["| Source | Target | Type | Weight | Description |", "|--------|--------|------|--------|-------------|"])
            for r in page_rels:
                rel_lines.append(f"| {r['source_name']} | {r['target_name']} | {r['relationship_type']} | {round(float(r['weight']) if r['weight'] else 1.0, 2)} | {(r['description'] or '')[:50]} |")
            
            fname = "relationships.md" if total_rel_pages <= 1 else f"relationships/page-{page_idx + 1}.md"
            zf.writestr(f"knowledge-graph/{fname}", "\n".join(rel_lines) + "\n")

        # --- ALL Communities ---
        comm_rows = await conn.execute(text("""
            SELECT title, summary, member_entities, level
            FROM graphrag_communities
            ORDER BY array_length(member_entities, 1) DESC NULLS LAST
        """))
        communities = comm_rows.mappings().all()

        log.info("export: %d communities", len(communities))

        # Split communities into pages
        COMM_PAGE_SIZE = 100
        total_comm_pages = (len(communities) + COMM_PAGE_SIZE - 1) // COMM_PAGE_SIZE
        for page_idx in range(total_comm_pages):
            start = page_idx * COMM_PAGE_SIZE
            end = min(start + COMM_PAGE_SIZE, len(communities))
            page_comms = communities[start:end]

            cl = []
            if page_idx == 0:
                cl = ["# Communities", "", f"Total: {len(communities)}", ""]
            if total_comm_pages > 1:
                cl.append(f"## Page {page_idx + 1}/{total_comm_pages}\n")

            for c in page_comms:
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

            fname = "communities.md" if total_comm_pages <= 1 else f"communities/page-{page_idx + 1}.md"
            zf.writestr(f"knowledge-graph/{fname}", "\n".join(cl) + "\n")

        # --- Graph JSON (ALL data) ---
        graph_data = {
            "entities": [{"name": e["name"], "type": e["entity_type"], "description": e["description"], "frequency": e["frequency"]} for e in entities],
            "relationships": [{"source": r["source_name"], "target": r["target_name"], "type": r["relationship_type"], "weight": float(r["weight"]) if r["weight"] else 1.0} for r in relationships],
            "communities": [{"title": c["title"], "summary": c["summary"], "members": c["member_entities"] or []} for c in communities],
        }
        zf.writestr("knowledge-graph/graph.json", json.dumps(graph_data, default=str))

        # --- GraphML (ALL entities and relationships) ---
        gm = ['<?xml version="1.0" encoding="UTF-8"?>', '<graphml xmlns="http://graphml.graphstruct.org/graphml">', '<graph id="kg" edgedefault="undirected">']
        for e in entities:
            ne = (e["name"] or "").replace("&", "&amp;").replace("<", "&lt;")
            gm.append(f'  <node id="{_slugify(ne)}" label="{ne}"><data key="type">{e["entity_type"]}</data></node>')
        for r in relationships:
            gm.append(f'  <edge source="{_slugify(r["source_name"] or "")}" target="{_slugify(r["target_name"] or "")}" label="{r["relationship_type"]}"/>')
        gm.append("</graph></graphml>")
        zf.writestr("knowledge-graph/graph.graphml", "\n".join(gm))

        # --- Knowledge Graph README ---
        kg_lines = [
            "# Knowledge Graph",
            "",
            f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
            "",
            "## Stats",
            "",
            f"- **Entities:** {len(entities)}",
            f"- **Relationships:** {len(relationships)}",
            f"- **Communities:** {len(communities)}",
            "",
            "## Entity Types",
            "",
            "| Type | Count |",
            "|------|-------|",
        ]
        for etype, elist in sorted(entities_by_type.items()):
            kg_lines.append(f"| {etype} | {len(elist)} |")
        zf.writestr("knowledge-graph/README.md", "\n".join(kg_lines) + "\n")

        # --- Main README ---
        readme = [
            "# Wiki & Knowledge Graph Export (Complete)",
            "",
            f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
            "",
            "## Wiki Pages",
            "",
            f"Total: **{wiki_count}** pages across **{len(projects)}** projects",
            "",
            "| Project | Pages |",
            "|---------|-------|",
        ]
        for p, c in sorted(projects.items()):
            readme.append(f"| [{p}](wiki/{p}/) | {c} |")
        readme.extend([
            "",
            "## Knowledge Graph",
            "",
            f"- **Entities:** {len(entities)}",
            f"- **Relationships:** {len(relationships)}",
            f"- **Communities:** {len(communities)}",
            "",
            "## Structure",
            "",
            "```",
            "wiki-kg-export/",
            "├── README.md",
            "├── wiki/{project}/{page}.md",
            "└── knowledge-graph/",
            "    ├── README.md",
            "    ├── entities/{type}/{name}.md",
            "    ├── relationships.md (or page-1.md, page-2.md...)",
            "    ├── communities.md (or page-1.md, page-2.md...)",
            "    ├── graph.json",
            "    └── graph.graphml",
            "```",
            "",
            "## Usage with AI Agents",
            "",
            "See GUIDE_AI_AGENTS.md for how to expose this data via MCP server.",
            "",
        ])
        zf.writestr("README.md", "\n".join(readme) + "\n")

        log.info("export: ZIP complete — %d wiki pages, %d entities, %d relationships, %d communities",
                 wiki_count, len(entities), len(relationships), len(communities))

    return zip_buffer.getvalue()


@router.get("/zip", summary="Export ALL wiki + knowledge graph as organized ZIP")
async def export_zip(
    prefix: str | None = Query(None, description="Filter wiki pages by file path prefix"),
    page_type: str | None = Query(None, description="Filter wiki pages by type"),
    entity_types: str | None = Query(None, description="Comma-separated entity types to include"),
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

@router.get("/clusters", summary="List all topic clusters")
async def list_topic_clusters_endpoint(
    _tok: str = Depends(require_any_token),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """List topic clusters with pagination."""
    async with get_engine().connect() as conn:
        clusters = await list_clusters(conn, limit=limit, offset=offset)
        return {"clusters": clusters, "limit": limit, "offset": offset}


@router.get("/cluster-graph", summary="Cluster correlation graph: clusters as nodes, shared-source edges")
async def cluster_graph_endpoint(
    _tok: str = Depends(require_any_token),
    min_shared_sources: int = Query(1, ge=1, le=100),
):
    """Return topic clusters as graph nodes with edges between clusters that
    share source documents (weight = number of shared sources). This powers
    the knowledge-graph "Topics" view.
    """
    async with get_engine().connect() as conn:
        data = await get_cluster_graph(conn, min_shared_sources=min_shared_sources)
        return data


@router.get("/cluster/{cluster_id}/sources", summary="List source documents feeding a topic cluster")
async def cluster_sources_endpoint(
    cluster_id: UUID = FastAPIPath(..., description="Cluster UUID"),
    _tok: str = Depends(require_any_token),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """Build the topic→source catalog for a cluster.

    Returns the source documents (with per-source unit counts) whose content
    was assembled into this topic cluster. Available for line-of-retrieval
    auditing and LLM context-guided source lookup.
    """
    async with get_engine().connect() as conn:
        data = await get_cluster_sources(conn, str(cluster_id), limit, offset)
        if "error" in data:
            raise HTTPException(status_code=404, detail=data["error"])
        return data


@router.get("/cluster/{cluster_id}/markdown", summary="Export cluster as markdown")
async def export_cluster_markdown_endpoint(
    cluster_id: UUID = FastAPIPath(..., description="Cluster UUID"),
    _tok: str = Depends(require_any_token),
):
    """Export a single topic cluster as a markdown file."""
    async with get_engine().connect() as conn:
        markdown = await export_cluster_markdown(conn, str(cluster_id))
        if markdown is None:
            raise HTTPException(status_code=404, detail=f"Cluster {cluster_id} not found")
        return Response(
            content=markdown,
            media_type="text/markdown",
            headers={
                "Content-Disposition": f'attachment; filename="cluster-{cluster_id}.md"'
            },
        )


@router.get("/cluster/{cluster_id}/json", summary="Export cluster as RAG-ready JSON")
async def export_cluster_json_endpoint(
    cluster_id: UUID = FastAPIPath(..., description="Cluster UUID"),
    _tok: str = Depends(require_any_token),
):
    """Export a single topic cluster as JSON for RAG ingestion."""
    async with get_engine().connect() as conn:
        data = await export_cluster_json(conn, str(cluster_id))
        if data is None:
            raise HTTPException(status_code=404, detail=f"Cluster {cluster_id} not found")
        return Response(
            content=json.dumps(data, indent=2, default=str),
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="cluster-{cluster_id}.json"'
            },
        )


@router.get("/cluster/{cluster_id}/graphml", summary="Export cluster as GraphML")
async def export_cluster_graphml_endpoint(
    cluster_id: UUID = FastAPIPath(..., description="Cluster UUID"),
    _tok: str = Depends(require_any_token),
):
    """Export a single topic cluster as GraphML for visualization."""
    async with get_engine().connect() as conn:
        graphml = await export_cluster_graphml(conn, str(cluster_id))
        if graphml is None:
            raise HTTPException(status_code=404, detail=f"Cluster {cluster_id} not found")
        return Response(
            content=graphml,
            media_type="application/xml",
            headers={
                "Content-Disposition": f'attachment; filename="cluster-{cluster_id}.graphml"'
            },
        )


@router.get("/cluster/{cluster_id}/zip", summary="Export cluster as ZIP bundle")
async def export_cluster_zip_endpoint(
    cluster_id: UUID = FastAPIPath(..., description="Cluster UUID"),
    _tok: str = Depends(require_any_token),
):
    """Export a single topic cluster as a ZIP with markdown, JSON, and GraphML."""
    async with get_engine().connect() as conn:
        try:
            zip_bytes = await get_cluster_zip(conn, str(cluster_id))
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        return Response(
            content=zip_bytes,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="cluster-{cluster_id}-{ts}.zip"',
                "Content-Length": str(len(zip_bytes)),
            },
        )


@router.get("/cluster/{cluster_id}/context-pack", summary="Export LLM-optimized context pack")
async def export_cluster_context_pack_endpoint(
    cluster_id: UUID = FastAPIPath(..., description="Cluster UUID"),
    _tok: str = Depends(require_any_token),
):
    """Export a topic cluster as an LLM context pack (markdown format)."""
    async with get_engine().connect() as conn:
        pack = await build_context_pack(conn, str(cluster_id))
        if "error" in pack:
            raise HTTPException(status_code=404, detail=pack["error"])
        return Response(
            content=pack["markdown"],
            media_type="text/markdown",
            headers={
                "Content-Disposition": f'attachment; filename="context-pack-{cluster_id}.md"'
            },
        )


@router.get("/cluster/{cluster_id}/context-pack/stream", summary="Stream large context pack")
async def export_cluster_context_pack_stream_endpoint(
    cluster_id: UUID = FastAPIPath(..., description="Cluster UUID"),
    _tok: str = Depends(require_any_token),
):
    """Stream a topic cluster context pack for very large topics."""
    async def generate():
        async with get_engine().connect() as conn:
            pack = await build_context_pack(conn, str(cluster_id))
            if "error" in pack:
                yield f"Error: {pack['error']}"
                return
            yield pack["markdown"]

    return StreamingResponse(
        generate(),
        media_type="text/markdown",
        headers={
            "Content-Disposition": f'attachment; filename="context-pack-{cluster_id}.md"'
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


@router.post("/trigger", summary="Manually trigger a full export now")
async def trigger_export(
    _tok: str = Depends(require_any_token),
):
    """Trigger an immediate full export and save to disk."""
    filepath = await run_scheduled_export()
    if filepath:
        return {"status": "ok", "file": filepath}
    return Response(status_code=500, content='{"error": "Export failed"}', media_type="application/json")
