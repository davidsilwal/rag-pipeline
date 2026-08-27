#!/usr/bin/env python3
"""MCP Server for Wiki + Knowledge Graph RAG.

Exposes wiki pages, entities, relationships, communities, and semantic search
to AI agents via the Model Context Protocol (MCP).

Usage:
  python mcp_server/server.py                    # stdio mode (for Claude, OpenCode, etc.)
  python mcp_server/server.py --transport sse    # SSE mode (for remote access)

Environment:
  DATABASE_URL  - PostgreSQL connection string (default: postgresql://postgres:postgres@localhost:5432/knowledge_base)
  API_TOKEN     - Optional API token for the control API (for export features)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

import asyncpg
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types
from mcp.types import (
    TextContent,
    Tool,
)

# ---------------------------------------------------------------------------
# Database connection
# ---------------------------------------------------------------------------

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/knowledge_base",
)

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
    return _pool


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

app = Server("wiki-knowledge-graph")


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS: list[Tool] = [
    Tool(
        name="search_wiki",
        description="Search wiki pages by title, content, or file path. Returns matching pages with previews.",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query (matches title, content, file path)"},
                "page_type": {"type": "string", "description": "Filter by page type (optional)"},
                "limit": {"type": "integer", "description": "Max results (default 20)", "default": 20},
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="get_wiki_page",
        description="Get a wiki page by ID or file path. Returns full markdown content.",
        inputSchema={
            "type": "object",
            "properties": {
                "page_id": {"type": "string", "description": "Page UUID"},
                "file_path": {"type": "string", "description": "File path (alternative to page_id)"},
            },
        },
    ),
    Tool(
        name="list_wiki_pages",
        description="List wiki pages with optional filters. Returns page metadata and previews.",
        inputSchema={
            "type": "object",
            "properties": {
                "prefix": {"type": "string", "description": "Filter by folder prefix (optional)"},
                "page_type": {"type": "string", "description": "Filter by page type (optional)"},
                "limit": {"type": "integer", "description": "Max results (default 50)", "default": 50},
                "offset": {"type": "integer", "description": "Offset for pagination (default 0)", "default": 0},
            },
        },
    ),
    Tool(
        name="search_entities",
        description="Search knowledge graph entities by name or type. Returns entity details with descriptions.",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query (matches entity name)"},
                "entity_type": {"type": "string", "description": "Filter by type: person, org, concept, location, technology, event"},
                "limit": {"type": "integer", "description": "Max results (default 50)", "default": 50},
            },
        },
    ),
    Tool(
        name="get_entity",
        description="Get a knowledge graph entity with its relationships and community memberships.",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Entity name (case-insensitive)"},
            },
            "required": ["name"],
        },
    ),
    Tool(
        name="search_relationships",
        description="Search knowledge graph relationships between entities.",
        inputSchema={
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "Source entity name (optional)"},
                "target": {"type": "string", "description": "Target entity name (optional)"},
                "relationship_type": {"type": "string", "description": "Filter by relationship type (optional)"},
                "limit": {"type": "integer", "description": "Max results (default 50)", "default": 50},
            },
        },
    ),
    Tool(
        name="get_communities",
        description="List knowledge graph communities (groups of related entities).",
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max results (default 50)", "default": 50},
            },
        },
    ),
    Tool(
        name="semantic_search",
        description="Semantic search across wiki content using vector embeddings. Finds conceptually similar content.",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural language search query"},
                "limit": {"type": "integer", "description": "Max results (default 10)", "default": 10},
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="get_entity_graph",
        description="Get the local knowledge graph around an entity — its direct connections and their connections.",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Entity name to start from"},
                "depth": {"type": "integer", "description": "Graph depth (default 1, max 2)", "default": 1},
            },
            "required": ["name"],
        },
    ),
    Tool(
        name="export_wiki_markdown",
        description="Export wiki pages as markdown. Returns concatenated markdown content.",
        inputSchema={
            "type": "object",
            "properties": {
                "prefix": {"type": "string", "description": "Filter by folder prefix (optional)"},
                "page_type": {"type": "string", "description": "Filter by page type (optional)"},
                "limit": {"type": "integer", "description": "Max pages (default 100)", "default": 100},
            },
        },
    ),
    Tool(
        name="export_knowledge_graph",
        description="Export the knowledge graph as structured JSON (entities, relationships, communities).",
        inputSchema={
            "type": "object",
            "properties": {
                "format": {"type": "string", "description": "Export format: 'json' or 'graphml' (default json)", "default": "json"},
                "limit": {"type": "integer", "description": "Max entities (default 500)", "default": 500},
            },
        },
    ),
]


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


async def handle_search_wiki(args: dict) -> str:
    pool = await get_pool()
    query = args["query"]
    page_type = args.get("page_type")
    limit = args.get("limit", 20)

    sql = """
        SELECT page_id, file_path, title, page_type, domain, status,
               left(markdown_body, 500) AS preview, updated_at
        FROM wiki_pages
        WHERE (title ILIKE :q OR file_path ILIKE :q OR markdown_body ILIKE :q)
    """
    params: dict[str, Any] = {"q": f"%{query}%", "lim": limit}

    if page_type:
        sql += " AND page_type = :pt"
        params["pt"] = page_type

    sql += " ORDER BY updated_at DESC LIMIT :lim"

    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, **params)

    if not rows:
        return json.dumps({"results": [], "message": f"No pages found matching '{query}'"})

    results = []
    for r in rows:
        results.append({
            "page_id": str(r["page_id"]),
            "title": r["title"],
            "file_path": r["file_path"],
            "page_type": r["page_type"],
            "domain": r["domain"],
            "status": r["status"],
            "preview": (r["preview"] or "")[:300],
            "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
        })

    return json.dumps({"results": results, "count": len(results)}, indent=2)


async def handle_get_wiki_page(args: dict) -> str:
    pool = await get_pool()
    page_id = args.get("page_id")
    file_path = args.get("file_path")

    if page_id:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM wiki_pages WHERE page_id = $1", page_id
            )
    elif file_path:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM wiki_pages WHERE file_path = $1", file_path
            )
    else:
        return json.dumps({"error": "Provide either page_id or file_path"})

    if not row:
        return json.dumps({"error": "Page not found"})

    return json.dumps({
        "page_id": str(row["page_id"]),
        "title": row["title"],
        "file_path": row["file_path"],
        "page_type": row["page_type"],
        "domain": row["domain"],
        "status": row["status"],
        "markdown": row["markdown_body"],
        "frontmatter": json.loads(row["frontmatter"]) if row["frontmatter"] else None,
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
    }, indent=2)


async def handle_list_wiki_pages(args: dict) -> str:
    pool = await get_pool()
    prefix = args.get("prefix")
    page_type = args.get("page_type")
    limit = args.get("limit", 50)
    offset = args.get("offset", 0)

    sql = "SELECT page_id, file_path, title, page_type, domain, status, updated_at FROM wiki_pages"
    conditions = []
    params: dict[str, Any] = {"lim": limit, "off": offset}

    if prefix:
        folder = prefix.rstrip("/")
        conditions.append("(file_path = :pfx OR file_path LIKE :pfx_slash)")
        params["pfx"] = folder
        params["pfx_slash"] = f"{folder}/%"
    if page_type:
        conditions.append("page_type = :pt")
        params["pt"] = page_type

    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY updated_at DESC LIMIT :lim OFFSET :off"

    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, **params)

    results = []
    for r in rows:
        results.append({
            "page_id": str(r["page_id"]),
            "title": r["title"],
            "file_path": r["file_path"],
            "page_type": r["page_type"],
            "domain": r["domain"],
            "status": r["status"],
            "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
        })

    return json.dumps({"results": results, "count": len(results), "offset": offset}, indent=2)


async def handle_search_entities(args: dict) -> str:
    pool = await get_pool()
    query = args["query"]
    entity_type = args.get("entity_type")
    limit = args.get("limit", 50)

    sql = "SELECT entity_id, name, entity_type, description, frequency FROM graphrag_entities WHERE name ILIKE :q"
    params: dict[str, Any] = {"q": f"%{query}%", "lim": limit}

    if entity_type:
        sql += " AND entity_type = :et"
        params["et"] = entity_type

    sql += " ORDER BY frequency DESC, name LIMIT :lim"

    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, **params)

    results = []
    for r in rows:
        results.append({
            "entity_id": str(r["entity_id"]),
            "name": r["name"],
            "type": r["entity_type"],
            "description": r["description"],
            "frequency": r["frequency"],
        })

    return json.dumps({"results": results, "count": len(results)}, indent=2)


async def handle_get_entity(args: dict) -> str:
    pool = await get_pool()
    name = args["name"]

    async with pool.acquire() as conn:
        entity = await conn.fetchrow(
            "SELECT * FROM graphrag_entities WHERE LOWER(name) = LOWER($1)", name
        )
        if not entity:
            return json.dumps({"error": f"Entity '{name}' not found"})

        # Get relationships
        rels = await conn.fetch("""
            SELECT r.relationship_type, r.description, r.weight,
                   e1.name as source_name, e1.entity_type as source_type,
                   e2.name as target_name, e2.entity_type as target_type
            FROM graphrag_relationships r
            JOIN graphrag_entities e1 ON r.source_entity_id = e1.entity_id
            JOIN graphrag_entities e2 ON r.target_entity_id = e2.entity_id
            WHERE e1.entity_id = $1 OR e2.entity_id = $1
            ORDER BY r.weight DESC
            LIMIT 50
        """, entity["entity_id"])

        # Get communities
        comms = await conn.fetch("""
            SELECT title, summary, member_entities
            FROM graphrag_communities
            WHERE $1 = ANY(member_entities)
            LIMIT 10
        """, name)

    relationships = []
    for r in rels:
        relationships.append({
            "source": r["source_name"],
            "target": r["target_name"],
            "type": r["relationship_type"],
            "description": r["description"],
            "weight": float(r["weight"]) if r["weight"] else 1.0,
        })

    communities = []
    for c in comms:
        communities.append({
            "title": c["title"],
            "summary": c["summary"],
        })

    return json.dumps({
        "entity": {
            "name": entity["name"],
            "type": entity["entity_type"],
            "description": entity["description"],
            "frequency": entity["frequency"],
        },
        "relationships": relationships,
        "communities": communities,
    }, indent=2)


async def handle_search_relationships(args: dict) -> str:
    pool = await get_pool()
    source = args.get("source")
    target = args.get("target")
    rel_type = args.get("relationship_type")
    limit = args.get("limit", 50)

    sql = """
        SELECT r.relationship_type, r.description, r.weight,
               e1.name as source_name, e2.name as target_name
        FROM graphrag_relationships r
        JOIN graphrag_entities e1 ON r.source_entity_id = e1.entity_id
        JOIN graphrag_entities e2 ON r.target_entity_id = e2.entity_id
        WHERE 1=1
    """
    params: dict[str, Any] = {"lim": limit}

    if source:
        sql += " AND e1.name ILIKE :src"
        params["src"] = f"%{source}%"
    if target:
        sql += " AND e2.name ILIKE :tgt"
        params["tgt"] = f"%{target}%"
    if rel_type:
        sql += " AND r.relationship_type ILIKE :rt"
        params["rt"] = f"%{rel_type}%"

    sql += " ORDER BY r.weight DESC LIMIT :lim"

    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, **params)

    results = []
    for r in rows:
        results.append({
            "source": r["source_name"],
            "target": r["target_name"],
            "type": r["relationship_type"],
            "description": r["description"],
            "weight": float(r["weight"]) if r["weight"] else 1.0,
        })

    return json.dumps({"results": results, "count": len(results)}, indent=2)


async def handle_get_communities(args: dict) -> str:
    pool = await get_pool()
    limit = args.get("limit", 50)

    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT community_id, title, summary, member_entities, level
            FROM graphrag_communities
            ORDER BY array_length(member_entities, 1) DESC NULLS LAST
            LIMIT $1
        """, limit)

    results = []
    for r in rows:
        results.append({
            "community_id": str(r["community_id"]),
            "title": r["title"],
            "summary": r["summary"],
            "member_count": len(r["member_entities"]) if r["member_entities"] else 0,
            "members": r["member_entities"][:20] if r["member_entities"] else [],
            "level": r["level"],
        })

    return json.dumps({"results": results, "count": len(results)}, indent=2)


async def handle_semantic_search(args: dict) -> str:
    """Semantic search using embedding similarity.

    This requires an embedding of the query. We'll use the same BGE-M3 model
    that's used for wiki page embeddings, or fall back to keyword search.
    """
    pool = await get_pool()
    query = args["query"]
    limit = args.get("limit", 10)

    # Try to generate embedding for the query
    try:
        import httpx
        embedding_model = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")

        # Use the local embedding service or sentence-transformers
        try:
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer(embedding_model)
            query_embedding = model.encode(query).tolist()
        except Exception:
            # Fallback: use keyword search if embedding not available
            return await _keyword_search_fallback(pool, query, limit)

        # Search for similar chunks using cosine similarity
        async with pool.acquire() as conn:
            # Use pgvector if available
            rows = await conn.fetch("""
                SELECT c.chunk_id, c.content, c.source_id, c.char_start, c.char_end,
                       s.file_path, s.title,
                       1 - (c.embedding <=> $1::vector) as similarity
                FROM chunks c
                JOIN sources s ON c.source_id = s.source_id
                WHERE c.embedding IS NOT NULL
                ORDER BY c.embedding <=> $1::vector
                LIMIT $2
            """, str(query_embedding), limit)

        if not rows:
            return await _keyword_search_fallback(pool, query, limit)

        results = []
        for r in rows:
            results.append({
                "content": (r["content"] or "")[:500],
                "source_id": str(r["source_id"]),
                "file_path": r["file_path"],
                "title": r["title"],
                "similarity": round(float(r["similarity"]), 4),
            })

        return json.dumps({"results": results, "count": len(results), "method": "semantic"}, indent=2)

    except Exception as e:
        return await _keyword_search_fallback(pool, query, limit)


async def _keyword_search_fallback(pool: asyncpg.Pool, query: str, limit: int) -> str:
    """Fallback to keyword search when embeddings aren't available."""
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT page_id, title, file_path, left(markdown_body, 500) as preview
            FROM wiki_pages
            WHERE markdown_body ILIKE $1 OR title ILIKE $1
            ORDER BY updated_at DESC
            LIMIT $2
        """, f"%{query}%", limit)

    results = []
    for r in rows:
        results.append({
            "page_id": str(r["page_id"]),
            "title": r["title"],
            "file_path": r["file_path"],
            "preview": r["preview"][:300] if r["preview"] else "",
        })

    return json.dumps({"results": results, "count": len(results), "method": "keyword"}, indent=2)


async def handle_get_entity_graph(args: dict) -> str:
    """Get the local knowledge graph around an entity."""
    pool = await get_pool()
    name = args["name"]
    depth = min(args.get("depth", 1), 2)

    async with pool.acquire() as conn:
        entity = await conn.fetchrow(
            "SELECT entity_id, name, entity_type FROM graphrag_entities WHERE LOWER(name) = LOWER($1)",
            name,
        )
        if not entity:
            return json.dumps({"error": f"Entity '{name}' not found"})

        # Get direct relationships (depth 1)
        edges = await conn.fetch("""
            SELECT e1.name as source, e1.entity_type as source_type,
                   e2.name as target, e2.entity_type as target_type,
                   r.relationship_type, r.description, r.weight
            FROM graphrag_relationships r
            JOIN graphrag_entities e1 ON r.source_entity_id = e1.entity_id
            JOIN graphrag_entities e2 ON r.target_entity_id = e2.entity_id
            WHERE e1.entity_id = $1 OR e2.entity_id = $1
            ORDER BY r.weight DESC
            LIMIT 100
        """, entity["entity_id"])

        nodes = {name.lower(): {"name": name, "type": entity["entity_type"]}}
        links = []

        for e in edges:
            src = e["source"]
            tgt = e["target"]
            nodes[src.lower()] = {"name": src, "type": e["source_type"]}
            nodes[tgt.lower()] = {"name": tgt, "type": e["target_type"]}
            links.append({
                "source": src,
                "target": tgt,
                "type": e["relationship_type"],
                "description": e["description"],
                "weight": float(e["weight"]) if e["weight"] else 1.0,
            })

        # Depth 2: get connections of connected nodes
        if depth >= 2:
            connected_ids = set()
            for e in edges:
                if e["source"].lower() == name.lower():
                    connected_ids.add(e["target"])
                else:
                    connected_ids.add(e["source"])

            for cname in list(connected_ids)[:10]:
                ce = await conn.fetchrow(
                    "SELECT entity_id, name, entity_type FROM graphrag_entities WHERE LOWER(name) = LOWER($1)",
                    cname,
                )
                if ce:
                    depth2_edges = await conn.fetch("""
                        SELECT e1.name as source, e2.name as target,
                               r.relationship_type, r.weight
                        FROM graphrag_relationships r
                        JOIN graphrag_entities e1 ON r.source_entity_id = e1.entity_id
                        JOIN graphrag_entities e2 ON r.target_entity_id = e2.entity_id
                        WHERE (e1.entity_id = $1 OR e2.entity_id = $1)
                          AND e1.entity_id != $2 AND e2.entity_id != $2
                        ORDER BY r.weight DESC LIMIT 10
                    """, ce["entity_id"], entity["entity_id"])

                    for d2e in depth2_edges:
                        n1 = d2e["source"]
                        n2 = d2e["target"]
                        if n1.lower() not in nodes:
                            nodes[n1.lower()] = {"name": n1, "type": "unknown"}
                        if n2.lower() not in nodes:
                            nodes[n2.lower()] = {"name": n2, "type": "unknown"}
                        links.append({
                            "source": n1,
                            "target": n2,
                            "type": d2e["relationship_type"],
                            "weight": float(d2e["weight"]) if d2e["weight"] else 1.0,
                        })

    return json.dumps({
        "center": name,
        "nodes": list(nodes.values()),
        "links": links,
        "node_count": len(nodes),
        "edge_count": len(links),
    }, indent=2)


async def handle_export_wiki_markdown(args: dict) -> str:
    pool = await get_pool()
    prefix = args.get("prefix")
    page_type = args.get("page_type")
    limit = args.get("limit", 100)

    sql = "SELECT title, file_path, markdown_body FROM wiki_pages"
    conditions = []
    params: dict[str, Any] = {"lim": limit}

    if prefix:
        folder = prefix.rstrip("/")
        conditions.append("(file_path = :pfx OR file_path LIKE :pfx_slash)")
        params["pfx"] = folder
        params["pfx_slash"] = f"{folder}/%"
    if page_type:
        conditions.append("page_type = :pt")
        params["pt"] = page_type

    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY updated_at DESC LIMIT :lim"

    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, **params)

    # Build concatenated markdown
    parts = []
    for r in rows:
        title = r["title"] or "Untitled"
        fp = r["file_path"] or ""
        md = r["markdown_body"] or ""
        parts.append(f"# {title}\n\n> Source: `{fp}`\n\n{md}\n\n---\n")

    combined = "\n".join(parts)
    return json.dumps({
        "pages": len(parts),
        "total_chars": len(combined),
        "markdown": combined[:100000],  # Cap at 100KB for safety
    }, indent=2)


async def handle_export_knowledge_graph(args: dict) -> str:
    pool = await get_pool()
    fmt = args.get("format", "json")
    limit = args.get("limit", 500)

    async with pool.acquire() as conn:
        entities = await conn.fetch("""
            SELECT name, entity_type, description, frequency
            FROM graphrag_entities
            ORDER BY frequency DESC
            LIMIT $1
        """, limit)

        relationships = await conn.fetch("""
            SELECT e1.name as source, e2.name as target,
                   r.relationship_type, r.description, r.weight
            FROM graphrag_relationships r
            JOIN graphrag_entities e1 ON r.source_entity_id = e1.entity_id
            JOIN graphrag_entities e2 ON r.target_entity_id = e2.entity_id
            LIMIT $1
        """, limit * 2)

        communities = await conn.fetch("""
            SELECT title, summary, member_entities, level
            FROM graphrag_communities
            LIMIT 100
        """)

    if fmt == "graphml":
        # GraphML export for graph visualization tools
        lines = ['<?xml version="1.0" encoding="UTF-8"?>']
        lines.append('<graphml xmlns="http://graphml.graphstruct.org/graphml">')
        lines.append('<key id="type" for="node" attr.name="type" attr.type="string"/>')
        lines.append('<key id="desc" for="node" attr.name="description" attr.type="string"/>')
        lines.append('<graph id="knowledge-graph" edgedefault="undirected">')

        for e in entities:
            name_escaped = (e["name"] or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            desc_escaped = (e["description"] or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            eid = name_escaped.replace(" ", "_").lower()
            lines.append(f'  <node id="{eid}"><data key="type">{e["entity_type"]}</data><data key="desc">{desc_escaped}</data></node>')

        for r in relationships:
            s = (r["source"] or "").replace(" ", "_").lower().replace("&", "&amp;")
            t = (r["target"] or "").replace(" ", "_").lower().replace("&", "&amp;")
            lines.append(f'  <edge source="{s}" target="{t}" label="{r["relationship_type"]}"/>')

        lines.append('</graph></graphml>')
        return "\n".join(lines)

    # JSON format
    graph = {
        "entities": [{"name": e["name"], "type": e["entity_type"], "description": e["description"], "frequency": e["frequency"]} for e in entities],
        "relationships": [{"source": r["source"], "target": r["target"], "type": r["relationship_type"], "description": r["description"], "weight": float(r["weight"]) if r["weight"] else 1.0} for r in relationships],
        "communities": [{"title": c["title"], "summary": c["summary"], "members": c["member_entities"] or [], "level": c["level"]} for c in communities],
    }

    return json.dumps({
        "entity_count": len(graph["entities"]),
        "relationship_count": len(graph["relationships"]),
        "community_count": len(graph["communities"]),
        "graph": graph,
    }, indent=2)


# ---------------------------------------------------------------------------
# Request router
# ---------------------------------------------------------------------------

HANDLERS = {
    "search_wiki": handle_search_wiki,
    "get_wiki_page": handle_get_wiki_page,
    "list_wiki_pages": handle_list_wiki_pages,
    "search_entities": handle_search_entities,
    "get_entity": handle_get_entity,
    "search_relationships": handle_search_relationships,
    "get_communities": handle_get_communities,
    "semantic_search": handle_semantic_search,
    "get_entity_graph": handle_get_entity_graph,
    "export_wiki_markdown": handle_export_wiki_markdown,
    "export_knowledge_graph": handle_export_knowledge_graph,
}


async def handle_list_tools(request: types.ListToolsRequest) -> types.ListToolsResult:
    return types.ListToolsResult(tools=TOOLS)


async def handle_call_tool(request: types.CallToolRequest) -> types.CallToolResult:
    name = request.params.name
    arguments = request.params.arguments or {}
    handler = HANDLERS.get(name)
    if not handler:
        return types.CallToolResult(
            content=[TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]
        )

    try:
        result = await handler(arguments)
        return types.CallToolResult(
            content=[TextContent(type="text", text=result)]
        )
    except Exception as e:
        return types.CallToolResult(
            content=[TextContent(type="text", text=json.dumps({"error": str(e)}))]
        )


# Register request handlers
app.add_request_handler("tools/list", types.ListToolsRequest, handle_list_tools)
app.add_request_handler("tools/call", types.CallToolRequest, handle_call_tool)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    parser = argparse.ArgumentParser(description="Wiki + Knowledge Graph MCP Server")
    parser.add_argument("--transport", choices=["stdio", "sse"], default="stdio",
                       help="Transport mode (default: stdio)")
    parser.add_argument("--port", type=int, default=8080,
                       help="Port for SSE mode (default: 8080)")
    args = parser.parse_args()

    if args.transport == "stdio":
        async with stdio_server() as (read_stream, write_stream):
            await app.run(read_stream, write_stream, app.create_initialization_options())
    else:
        from mcp.server.sse import SseServerTransport
        from starlette.applications import Starlette
        from starlette.routing import Route, Mount
        import uvicorn

        sse = SseServerTransport("/messages/")

        async def handle_sse(request):
            async with sse.connect_sse(
                request.scope, request.receive, request._send
            ) as streams:
                await app.run(
                    streams[0], streams[1], app.create_initialization_options()
                )

        starlette_app = Starlette(
            routes=[
                Route("/sse", endpoint=handle_sse),
                Mount("/messages/", app=sse.handle_post_message),
            ],
        )

        print(f"MCP SSE server running on http://0.0.0.0:{args.port}/sse")
        uvicorn.run(starlette_app, host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    asyncio.run(main())
