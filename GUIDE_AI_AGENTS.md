# Sharing Wiki & Knowledge Graph with AI Agents

A complete guide to exposing your RAG pipeline's wiki pages and knowledge graph to AI agents like OpenCode, Claude, Hermes, OMP, and any MCP-compatible tool.

---

## Table of Contents

- [Quick Start](#quick-start)
- [MCP Server (Recommended)](#1-mcp-server-recommended)
- [REST API](#2-rest-api)
- [Export Bundles](#3-export-bundles)
- [Agent Configuration](#4-agent-configuration)
- [Available Tools](#5-available-tools)
- [Troubleshooting](#6-troubleshooting)

---

## Quick Start

The fastest way to get started:

```bash
# 1. Start the MCP server (SSE mode for remote access)
DATABASE_URL="postgresql://postgres:your_password@localhost:5432/knowledge_base" \
python mcp_server/server.py --transport sse --port 8080

# 2. Configure your AI agent to connect
#    SSE URL: http://100.72.153.12:8080/sse

# 3. Start asking questions
#    "Search the wiki for information about our microservices"
#    "What entities are related to PostgreSQL?"
#    "Show me the knowledge graph around Kubernetes"
```

---

## 1. MCP Server (Recommended)

The **Model Context Protocol (MCP)** server is the universal standard for exposing data to AI agents. It works with OpenCode, Claude Desktop, Hermes, OMP, and any MCP-compatible client.

### What It Provides

11 tools that give AI agents full access to your knowledge base:

| Tool | Description | Example |
|------|-------------|---------|
| `search_wiki` | Search wiki pages by content | "Find pages about Kafka" |
| `get_wiki_page` | Get full page markdown | "Show me the README page" |
| `list_wiki_pages` | List pages with filters | "List all project docs" |
| `search_entities` | Search knowledge graph entities | "Find entities related to Python" |
| `get_entity` | Get entity + relationships + communities | "Tell me about Kubernetes" |
| `search_relationships` | Search entity connections | "What connects Docker to K8s?" |
| `get_communities` | List entity communities | "Show me entity groups" |
| `semantic_search` | Semantic search via embeddings | "Find docs about deployment" |
| `get_entity_graph` | Get local graph around an entity | "Graph around Python (depth 2)" |
| `export_wiki_markdown` | Export pages as markdown | "Export all pages as markdown" |
| `export_knowledge_graph` | Export as JSON/GraphML | "Export the knowledge graph" |

### Running the MCP Server

#### Stdio Mode (for Claude Desktop, OpenCode local)

```bash
DATABASE_URL="postgresql://postgres:your_password@localhost:5432/knowledge_base" \
python mcp_server/server.py
```

#### SSE Mode (for remote access, web clients)

```bash
DATABASE_URL="postgresql://postgres:your_password@localhost:5432/knowledge_base" \
python mcp_server/server.py --transport sse --port 8080
```

#### Docker

```bash
docker compose up -d mcp-server
# SSE available at http://100.72.153.12:8080/sse
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql://postgres:postgres@localhost:5432/knowledge_base` | PostgreSQL connection string |
| `EMBEDDING_MODEL` | `BAAI/bge-m3` | Sentence-transformers model for semantic search |

---

## 2. REST API

The control API already exposes all data. Use this if your AI agent can make HTTP requests.

### Wiki Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/wiki/pages` | GET | List all pages (with `limit`, `offset`, `prefix`, `page_type`) |
| `/api/v1/wiki/pages/{page_id}` | GET | Get a specific page by ID |
| `/api/v1/wiki/pages/stats` | GET | Get page statistics |
| `/api/v1/wiki/search?q=...` | GET | Search pages by content |
| `/api/v1/wiki/export` | GET | Export pages as ZIP |

### Knowledge Graph Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/wiki/graphrag/entities` | GET | List entities (with `limit`, `offset`, `entity_type`) |
| `/api/v1/wiki/graphrag/relationships` | GET | List relationships (with `limit`, `offset`) |
| `/api/v1/wiki/graphrag/communities` | GET | List communities (with `limit`, `offset`) |
| `/api/v1/wiki/graphrag/stats` | GET | Stats summary (entity/relationship/community counts) |
| `/api/v1/wiki/graphrag/progress` | GET | GraphRAG processing progress |

### Example: Fetching Data via API

```bash
# List wiki pages
curl -H "Authorization: Bearer YOUR_TOKEN" \
  "http://100.72.153.12:8000/api/v1/wiki/pages?limit=10"

# Search entities
curl -H "Authorization: Bearer YOUR_TOKEN" \
  "http://100.72.153.12:8000/api/v1/wiki/graphrag/entities?limit=20"

# Get stats
curl -H "Authorization: Bearer YOUR_TOKEN" \
  "http://100.72.153.12:8000/api/v1/wiki/graphrag/stats"
```

---

## 3. Export Bundles

### Markdown Export

Export all wiki pages as concatenated markdown:

```bash
# Via API
curl -H "Authorization: Bearer YOUR_TOKEN" \
  "http://100.72.153.12:8000/api/v1/wiki/export" \
  --output wiki-export.zip

# Via MCP tool
# AI agent calls: export_wiki_markdown
```

### Knowledge Graph Export

Export the knowledge graph as JSON or GraphML:

```bash
# Via MCP tool
# AI agent calls: export_knowledge_graph (format: "json" or "graphml")
```

### GraphML Format (for graph visualization tools)

The GraphML export works with:
- Gephi
- Cytoscape
- yEd
- NetworkX
- Graphviz

---

## 4. Agent Configuration

### OpenCode

Add to `~/.config/opencode/config.json`:

```json
{
  "mcpServers": {
    "wiki-knowledge-graph": {
      "url": "http://100.72.153.12:8080/sse"
    }
  }
}
```

Or for local stdio:

```json
{
  "mcpServers": {
    "wiki-knowledge-graph": {
      "command": "python",
      "args": ["/path/to/mcp_server/server.py"],
      "env": {
        "DATABASE_URL": "postgresql://postgres:password@localhost:5432/knowledge_base"
      }
    }
  }
}
```

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "wiki": {
      "command": "python",
      "args": ["/path/to/mcp_server/server.py"],
      "env": {
        "DATABASE_URL": "postgresql://postgres:password@localhost:5432/knowledge_base"
      }
    }
  }
}
```

### Hermes / OMP / Any MCP Client

Connect via SSE URL:

```
http://100.72.153.12:8080/sse
```

---

## 5. Available Tools

### search_wiki

Search wiki pages by title, content, or file path.

```json
{
  "query": "Kafka",
  "page_type": "project",
  "limit": 20
}
```

### get_wiki_page

Get full page content by ID or file path.

```json
{
  "page_id": "uuid-here"
}
```

### search_entities

Search knowledge graph entities by name or type.

```json
{
  "query": "Python",
  "entity_type": "technology",
  "limit": 50
}
```

### get_entity

Get entity with all relationships and community memberships.

```json
{
  "name": "Kubernetes"
}
```

### get_entity_graph

Get the local knowledge graph around an entity (like a mind map).

```json
{
  "name": "Docker",
  "depth": 2
}
```

### search_relationships

Find connections between entities.

```json
{
  "source": "Docker",
  "target": "Kubernetes",
  "limit": 20
}
```

### get_communities

List groups of related entities.

```json
{
  "limit": 50
}
```

### semantic_search

Semantic search via vector embeddings.

```json
{
  "query": "How to deploy microservices",
  "limit": 10
}
```

### export_wiki_markdown

Export pages as markdown.

```json
{
  "prefix": "projects/backend",
  "limit": 100
}
```

### export_knowledge_graph

Export the knowledge graph.

```json
{
  "format": "json",
  "limit": 500
}
```

---

## 6. Troubleshooting

### MCP Server Won't Start

**Check database connection:**
```bash
DATABASE_URL="postgresql://postgres:password@localhost:5432/knowledge_base" \
python -c "import asyncpg; import asyncio; asyncio.run(asyncpg.create_pool('postgresql://postgres:password@localhost:5432/knowledge_base'))"
```

**Check MCP package:**
```bash
pip install mcp[cli] sentence-transformers
```

### 401 Unauthorized

The AI agent needs to send the API token. Check:
1. Token is set in the agent's config
2. Token matches the `API_TOKEN` in `.env`
3. Request includes `Authorization: Bearer <token>` header

### No Knowledge Graph Data

Run the GraphRAG pipeline first:
1. Go to `http://100.72.153.12:3000/process`
2. Add a source folder
3. Wait for processing to complete
4. Knowledge graph will be populated automatically

### Slow Semantic Search

Semantic search requires embeddings. If not available, it falls back to keyword search. To enable:
1. Ensure `sentence-transformers` is installed
2. The first search will download the embedding model (~1GB)

---

## Example AI Prompts

Once connected, try these prompts with your AI agent:

### Wiki Search
- "Search the wiki for information about our microservices architecture"
- "Find all pages related to database configuration"
- "What documentation exists for the deployment pipeline?"

### Knowledge Graph
- "What entities are related to PostgreSQL in the knowledge graph?"
- "Show me the knowledge graph around 'Kubernetes' with depth 2"
- "What communities exist in the knowledge graph?"
- "How is Docker connected to other entities?"

### Semantic Search
- "Find documents similar to 'How to set up CI/CD pipeline'"
- "What pages discuss authentication strategies?"
- "Find content about scaling and performance"

### Export
- "Export all wiki pages about backend services as markdown"
- "Export the knowledge graph as GraphML for Gephi"
- "Give me a summary of all entities in the technology category"

---

## Architecture

```
┌─────────────────────┐     ┌──────────────────────┐
│   AI Agent          │     │   MCP Server          │
│   (OpenCode, etc.)  │────▶│   (mcp_server.py)     │
└─────────────────────┘     └──────────┬───────────┘
                                       │
                              ┌────────▼────────┐
                              │   PostgreSQL     │
                              │   (wiki_pages,   │
                              │    graphrag_*)   │
                              └─────────────────┘
```

The MCP server connects directly to PostgreSQL, providing fast access to:
- Wiki pages (markdown content, metadata)
- Knowledge graph entities, relationships, communities
- Vector embeddings for semantic search
