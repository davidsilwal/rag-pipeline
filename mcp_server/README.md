# Wiki + Knowledge Graph MCP Server

Exposes your wiki pages, knowledge graph, and semantic search to AI agents via the **Model Context Protocol (MCP)**.

> 📖 **Full guide**: See [GUIDE_AI_AGENTS.md](../GUIDE_AI_AGENTS.md) for complete setup instructions, all tools, and agent configuration.

## Quick Start

```bash
# Start SSE server
DATABASE_URL="postgresql://postgres:password@localhost:5432/knowledge_base" \
python mcp_server/server.py --transport sse --port 8080

# Connect your AI agent to:
# http://100.72.153.12:8080/sse
```

## Tools

| Tool | Description |
|------|-------------|
| `search_wiki` | Search wiki pages by content |
| `get_wiki_page` | Get full page markdown |
| `list_wiki_pages` | List pages with filters |
| `search_entities` | Search knowledge graph entities |
| `get_entity` | Get entity + relationships + communities |
| `get_entity_graph` | Get local graph around an entity |
| `search_relationships` | Find entity connections |
| `get_communities` | List entity groups |
| `semantic_search` | Vector similarity search |
| `export_wiki_markdown` | Export pages as markdown |
| `export_knowledge_graph` | Export as JSON/GraphML |

## Agent Configuration

### OpenCode
```json
{
  "mcpServers": {
    "wiki": {
      "url": "http://100.72.153.12:8080/sse"
    }
  }
}
```

### Claude Desktop
```json
{
  "mcpServers": {
    "wiki": {
      "command": "python",
      "args": ["mcp_server/server.py"],
      "env": {
        "DATABASE_URL": "postgresql://postgres:password@localhost:5432/knowledge_base"
      }
    }
  }
}
```

### Docker
```bash
docker compose up -d mcp-server
```
