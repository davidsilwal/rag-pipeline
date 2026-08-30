#!/usr/bin/env python3
"""apps/control_api/services/cluster_export.py — Per-cluster export & context pack generation.

Provides:
- Per-cluster markdown/JSON/GraphML exports
- LLM-optimized context packs (topic page + 1-hop graph neighbors)
- GraphML serialization for visualization
"""

from __future__ import annotations

import io
import json
import logging
from datetime import datetime, timezone
from typing import Any
from xml.etree.ElementTree import Element, SubElement, tostring

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

log = logging.getLogger("cluster_export")

# Maximum context pack size (approx 120k tokens for context windows)
MAX_CONTEXT_PACK_CHARS = 500_000
CONTEXT_PACK_HEADER = """# Context Pack: {topic_name}

**Generated:** {generated_at}
**Cluster:** {cluster_id}
**Includes:** {includes}

---

"""


# ---------------------------------------------------------------------------
# GraphML serialization helpers
# ---------------------------------------------------------------------------

def _build_graphml(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    cluster_name: str,
) -> str:
    """Serialize nodes + edges as GraphML for visualization tools."""
    ns = "http://graphml.graphdrawing.org/xmlns"
    root = Element("graphml", xmlns=ns)
    root.set("edgedefault", "directed")

    # Node attributes
    for attr, attr_type in [
        ("id", "string"),
        ("label", "string"),
        ("entity_type", "string"),
        ("description", "string"),
        ("source_unit_count", "int"),
    ]:
        SubElement(root, "key", attrib={"id": attr, "for": "node", "attr.name": attr, "attr.type": attr_type})

    # Edge attributes
    for attr, attr_type in [
        ("id", "string"),
        ("source", "string"),
        ("target", "string"),
        ("relationship_type", "string"),
        ("weight", "double"),
        ("description", "string"),
    ]:
        SubElement(root, "key", attrib={"id": attr, "for": "edge", "attr.name": attr, "attr.type": attr_type})

    graph = SubElement(root, "graph", id=cluster_name, edgedefault="directed")

    for node in nodes:
        n = SubElement(graph, "node", id=str(node.get("entity_id", node.get("name", ""))))
        for attr in ["label", "entity_type", "description", "source_unit_count"]:
            if attr in node:
                data = SubElement(n, "data", key=attr)
                data.text = str(node[attr])

    for edge in edges:
        e = SubElement(
            graph,
            "edge",
            id=str(edge.get("relationship_id", "")),
            source=str(edge.get("source_entity", "")),
            target=str(edge.get("target_entity", "")),
        )
        for attr in ["relationship_type", "weight", "description"]:
            if attr in edge:
                data = SubElement(e, "data", key=attr)
                data.text = str(edge[attr])

    xml_bytes = tostring(root, encoding="unicode")
    return f'<?xml version="1.0" encoding="UTF-8"?>\n{xml_bytes}'


# ---------------------------------------------------------------------------
# Context pack builder
# ---------------------------------------------------------------------------

async def build_context_pack(
    conn: AsyncConnection,
    cluster_id: str,
    hop_neighbors: int = 1,
) -> dict[str, Any]:
    """Build an LLM-optimized context pack for a cluster.

    Args:
        conn: Database connection
        cluster_id: Topic cluster ID
        hop_neighbors: Graph neighborhood depth (1 = direct neighbors only)

    Returns:
        Dict with markdown, json, graphml, and metadata
    """
    # Load cluster info
    cluster_row = await conn.execute(
        text("""
            SELECT cluster_id, topic_name, top_keywords, unit_count
            FROM topic_clusters
            WHERE cluster_id = CAST(:cid AS uuid)
        """),
        {"cid": cluster_id},
    )
    cluster = cluster_row.fetchone()
    if not cluster:
        return {"error": f"Cluster {cluster_id} not found"}

    cluster_data = dict(cluster._mapping)
    topic_name = cluster_data["topic_name"]
    keywords = cluster_data.get("top_keywords", [])

    # Load wiki pages for this cluster — pages whose source_unit_ids overlap
    # the cluster's exemplar units (the unit→cluster linkage).
    page_rows = await conn.execute(
        text("""
            SELECT wp.page_id, wp.title, wp.file_path, wp.markdown_body,
                   wp.frontmatter, wp.updated_at
            FROM wiki_pages wp
            JOIN topic_clusters tc ON tc.cluster_id = CAST(:cid AS uuid)
            WHERE wp.source_unit_ids && tc.exemplar_unit_ids
            ORDER BY wp.title
        """),
        {"cid": cluster_id},
    )
    pages = [dict(r._mapping) for r in page_rows.fetchall()]

    # Load entities for this cluster — entities sourced from the cluster's units
    entity_rows = await conn.execute(
        text("""
            SELECT ge.entity_id, ge.name, ge.entity_type, ge.description,
                   ge.frequency as source_unit_count
            FROM graphrag_entities ge
            JOIN topic_clusters tc ON tc.cluster_id = CAST(:cid AS uuid)
            WHERE ge.source_unit_ids && tc.exemplar_unit_ids
            ORDER BY ge.frequency DESC
        """),
        {"cid": cluster_id},
    )
    entities = [dict(r._mapping) for r in entity_rows.fetchall()]

    # Load relationships for this cluster — relationships sourced from its units
    rel_rows = await conn.execute(
        text("""
            SELECT gr.rel_id as relationship_id,
                   gr.source_entity_id as source_entity,
                   gr.target_entity_id as target_entity,
                   gr.relationship_type, gr.weight, gr.description
            FROM graphrag_relationships gr
            JOIN topic_clusters tc ON tc.cluster_id = CAST(:cid AS uuid)
            WHERE gr.source_unit_ids && tc.exemplar_unit_ids
            ORDER BY gr.weight DESC NULLS LAST
        """),
        {"cid": cluster_id},
    )
    relationships = [dict(r._mapping) for r in rel_rows.fetchall()]

    # Load 1-hop neighbor clusters (clusters sharing exemplar units)
    neighbor_rows = await conn.execute(
        text("""
            WITH c AS (
                SELECT exemplar_unit_ids FROM topic_clusters WHERE cluster_id = CAST(:cid AS uuid)
            )
            SELECT DISTINCT tc.cluster_id, tc.topic_name
            FROM topic_clusters tc, c
            WHERE tc.cluster_id != CAST(:cid AS uuid)
              AND tc.exemplar_unit_ids && c.exemplar_unit_ids
            LIMIT 10
        """),
        {"cid": cluster_id},
    )
    neighbors = [dict(r._mapping) for r in neighbor_rows.fetchall()]

    # Build markdown context
    md_parts = [CONTEXT_PACK_HEADER.format(
        topic_name=topic_name,
        generated_at=datetime.now(timezone.utc).isoformat(),
        cluster_id=cluster_id,
        includes=f"{len(pages)} pages, {len(entities)} entities, {len(relationships)} relationships, {len(neighbors)} related topics",
    )]

    for page in pages:
        md_parts.append(f"## {page['title']}\n\n")
        body = page.get("markdown_body", "")
        # Truncate if needed
        if len(body) > 100_000:
            body = body[:100_000] + "\n\n*[truncated]*"
        md_parts.append(body)
        md_parts.append("\n\n---\n\n")

    # Add entity summary
    if entities:
        md_parts.append("## Key Entities\n\n")
        for e in entities[:20]:  # Top 20
            md_parts.append(f"- **{e['name']}** ({e['entity_type']}): {e.get('description', '')[:200]}\n")
        md_parts.append("\n\n")

    # Build JSON for RAG pipelines
    rag_json = {
        "cluster_id": cluster_id,
        "topic_name": topic_name,
        "keywords": keywords,
        "pages": [
            {
                "page_id": str(p["page_id"]),
                "title": p["title"],
                "file_path": p["file_path"],
                "content": p.get("markdown_body", ""),
                "frontmatter": p.get("frontmatter", {}),
            }
            for p in pages
        ],
        "entities": entities,
        "relationships": relationships,
        "neighbor_topics": neighbors,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    # Build GraphML
    graphml = _build_graphml(entities, relationships, topic_name)

    return {
        "cluster": cluster_data,
        "markdown": "".join(md_parts),
        "json": rag_json,
        "graphml": graphml,
        "neighbors": neighbors,
        "stats": {
            "pages": len(pages),
            "entities": len(entities),
            "relationships": len(relationships),
            "neighbor_topics": len(neighbors),
            "markdown_chars": sum(len(p.get("markdown_body", "")) for p in pages),
        },
    }


# ---------------------------------------------------------------------------
# Per-cluster exports
# ---------------------------------------------------------------------------

async def export_cluster_markdown(conn: AsyncConnection, cluster_id: str) -> str | None:
    """Export cluster as standalone markdown file."""
    pack = await build_context_pack(conn, cluster_id)
    if "error" in pack:
        return None
    return pack["markdown"]


async def export_cluster_json(conn: AsyncConnection, cluster_id: str) -> dict[str, Any] | None:
    """Export cluster as RAG-ready JSON."""
    pack = await build_context_pack(conn, cluster_id)
    if "error" in pack:
        return None
    return pack["json"]


async def export_cluster_graphml(conn: AsyncConnection, cluster_id: str) -> str | None:
    """Export cluster as GraphML for visualization."""
    pack = await build_context_pack(conn, cluster_id)
    if "error" in pack:
        return None
    return pack["graphml"]


async def list_clusters(conn: AsyncConnection, limit: int = 100, offset: int = 0) -> list[dict]:
    """List all topic clusters with stats.

    Counts (pages/entities/relationships per cluster) are computed in ONE pass
    via ``unnest`` joins instead of per-cluster correlated subqueries — the
    array-overlap (``&&``) subquery form was pathologically slow on unindexed
    ``source_unit_ids`` arrays (32k×44k rows × 84 clusters).
    """
    rows = await conn.execute(
        text("""
            WITH clusters AS (
                SELECT tc.cluster_id, tc.topic_name, tc.top_keywords, tc.unit_count,
                       tc.created_at, tc.exemplar_unit_ids,
                       COALESCE((SELECT max(cc.confidence_score)
                                 FROM cluster_consensus cc
                                 WHERE cc.hdbscan_cluster_id = tc.cluster_id), 0)
                           AS consensus_score
                FROM topic_clusters tc
            ),
            pages AS (
                SELECT c.cluster_id, count(DISTINCT wp.page_id) AS n
                FROM clusters c
                JOIN LATERAL unnest(c.exemplar_unit_ids) AS uid ON true
                JOIN wiki_pages wp ON wp.source_unit_ids @> ARRAY[uid]
                GROUP BY c.cluster_id
            ),
            entities AS (
                SELECT c.cluster_id, count(DISTINCT ge.entity_id) AS n
                FROM clusters c
                JOIN LATERAL unnest(c.exemplar_unit_ids) AS uid ON true
                JOIN graphrag_entities ge ON ge.source_unit_ids @> ARRAY[uid]
                GROUP BY c.cluster_id
            ),
            relationships AS (
                SELECT c.cluster_id, count(DISTINCT gr.rel_id) AS n
                FROM clusters c
                JOIN LATERAL unnest(c.exemplar_unit_ids) AS uid ON true
                JOIN graphrag_relationships gr ON gr.source_unit_ids @> ARRAY[uid]
                GROUP BY c.cluster_id
            )
            SELECT c.cluster_id, c.topic_name, c.top_keywords, c.unit_count,
                   c.created_at, c.consensus_score,
                   COALESCE(p.n, 0) AS page_count,
                   COALESCE(e.n, 0) AS entity_count,
                   COALESCE(r.n, 0) AS relationship_count
            FROM clusters c
            LEFT JOIN pages p USING (cluster_id)
            LEFT JOIN entities e USING (cluster_id)
            LEFT JOIN relationships r USING (cluster_id)
            ORDER BY c.unit_count DESC, c.topic_name
            LIMIT :limit OFFSET :offset
        """),
        {"limit": limit, "offset": offset},
    )
    return [dict(r._mapping) for r in rows.fetchall()]


async def get_cluster_graph(
    conn: AsyncConnection,
    min_shared_sources: int = 1,
) -> dict[str, Any]:
    """Build a cluster correlation graph: clusters as nodes, edges between
    clusters that share source documents (weight = number of shared sources).

    This is the "topics as graph nodes" view for the knowledge-graph page —
    the same correlation graph the plan describes, materialized from the
    cluster→units→sources join.
    """
    # One row per cluster with its aggregated source ids.
    rows = await conn.execute(
        text("""
            SELECT tc.cluster_id, tc.topic_name, tc.top_keywords, tc.unit_count,
                   tc.created_at,
                   COALESCE((SELECT max(cc.confidence_score) FROM cluster_consensus cc
                             WHERE cc.hdbscan_cluster_id = tc.cluster_id), 0) as consensus_score,
                   (SELECT array_agg(DISTINCT s.source_id)
                    FROM LATERAL unnest(tc.exemplar_unit_ids) AS uid
                    JOIN units u ON u.unit_id = uid
                    JOIN sources s ON s.source_id = u.source_id
                   ) AS source_ids
            FROM topic_clusters tc
            ORDER BY tc.unit_count DESC
        """),
    )
    nodes = []
    by_id: dict[str, dict] = {}
    for r in rows.mappings().all():
        d = dict(r)
        d["cluster_id"] = str(d["cluster_id"])
        d["source_ids"] = {str(s) for s in (d.get("source_ids") or [])}
        d["top_keywords"] = list(d.get("top_keywords") or [])
        by_id[d["cluster_id"]] = d
        nodes.append(d)

    # Edges: clusters sharing >= min_shared_sources source documents.
    ids = list(by_id.keys())
    edges = []
    seen: set[tuple[str, str]] = set()
    for i in range(len(ids)):
        a = ids[i]
        sa = by_id[a]["source_ids"]
        if not sa:
            continue
        for j in range(i + 1, len(ids)):
            b = ids[j]
            shared = sa & by_id[b]["source_ids"]
            if len(shared) >= min_shared_sources:
                key = (a, b)
                if key in seen:
                    continue
                seen.add(key)
                edges.append({
                    "source": a,
                    "target": b,
                    "weight": len(shared),
                    "shared_source_ids": sorted(shared),
                })
    edges.sort(key=lambda e: e["weight"], reverse=True)

    # Strip internal source_ids from the payload to keep it light.
    for n in nodes:
        n.pop("source_ids", None)
    return {"nodes": nodes, "edges": edges, "edge_count": len(edges)}


async def get_cluster_sources(
    conn: AsyncConnection,
    cluster_id: str,
    limit: int = 200,
    offset: int = 0,
) -> dict[str, Any]:
    """Build a topic→source catalog for a cluster.

    Joins the cluster's units (``exemplar_unit_ids``) back to their parent
    ``sources`` so operators can see which source documents feed a topic.

    Returns a dict with ``cluster`` metadata and a ``sources`` list (source_id,
    file_name, file_path, source_type, status, unit_count), plus totals.
    """
    cluster_row = await conn.execute(
        text("""
            SELECT cluster_id, topic_name, top_keywords, unit_count
            FROM topic_clusters
            WHERE cluster_id = CAST(:cid AS uuid)
        """),
        {"cid": cluster_id},
    )
    cluster = cluster_row.fetchone()
    if not cluster:
        return {"error": f"Cluster {cluster_id} not found"}
    cluster_data = dict(cluster._mapping)

    rows = await conn.execute(
        text("""
            WITH member_units AS (
                SELECT tc.exemplar_unit_ids AS ids
                FROM topic_clusters tc
                WHERE tc.cluster_id = CAST(:cid AS uuid)
            )
            SELECT
                s.source_id,
                s.file_name,
                s.file_path,
                s.source_type,
                s.status,
                count(u.unit_id) AS unit_count
            FROM member_units mu
            JOIN LATERAL unnest(mu.ids) AS uid ON true
            JOIN units u ON u.unit_id = uid
            JOIN sources s ON s.source_id = u.source_id
            GROUP BY s.source_id, s.file_name, s.file_path, s.source_type, s.status
            ORDER BY unit_count DESC, s.file_name
            LIMIT :limit OFFSET :offset
        """),
        {"cid": cluster_id, "limit": limit, "offset": offset},
    )
    sources = [dict(r._mapping) for r in rows.fetchall()]

    total_row = await conn.execute(
        text("""
            WITH member_units AS (
                SELECT tc.exemplar_unit_ids AS ids
                FROM topic_clusters tc
                WHERE tc.cluster_id = CAST(:cid AS uuid)
            )
            SELECT count(DISTINCT s.source_id) AS total_sources,
                   count(u.unit_id) AS total_units
            FROM member_units mu
            JOIN LATERAL unnest(mu.ids) AS uid ON true
            JOIN units u ON u.unit_id = uid
            JOIN sources s ON s.source_id = u.source_id
        """),
        {"cid": cluster_id},
    )
    total = dict(total_row.fetchone()._mapping)

    return {
        "cluster": cluster_data,
        "sources": sources,
        "total_sources": total["total_sources"],
        "total_units": total["total_units"],
    }


async def get_cluster_zip(
    conn: AsyncConnection,
    cluster_id: str,
) -> bytes:
    """Build a ZIP with all cluster exports (markdown, json, graphml, sources)."""
    import zipfile

    pack = await build_context_pack(conn, cluster_id)
    if "error" in pack:
        raise ValueError(pack["error"])

    buf = io.BytesIO()
    topic_slug = pack["cluster"]["topic_name"].lower().replace(" ", "-")[:50]

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # Markdown wiki page
        zf.writestr(
            f"{topic_slug}.md",
            pack["markdown"],
        )

        # JSON for RAG
        zf.writestr(
            f"{topic_slug}.json",
            json.dumps(pack["json"], indent=2, default=str),
        )

        # GraphML for visualization
        zf.writestr(
            f"{topic_slug}.graphml",
            pack["graphml"],
        )

        # Manifest
        manifest = {
            "cluster_id": str(cluster_id),
            "topic_name": pack["cluster"]["topic_name"],
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "contents": [
                f"{topic_slug}.md",
                f"{topic_slug}.json",
                f"{topic_slug}.graphml",
            ],
            "stats": pack["stats"],
        }
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, default=str))

    buf.seek(0)
    return buf.read()
