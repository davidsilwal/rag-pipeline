# Agent Memory Integration Guide & Architecture

## Overview
This document outlines the multi-agent long-term memory architecture across the workspace, integrating **TDAI Agent Memory (TencentDB)** with **OMP (Oh My Pi)**, **AGY (Antigravity)**, **OpenCode Interpreter**, **DeepSeek DSH**, and **Hermes**.

---

## 1. Stack Components & Ports

| Component | Port / Host | Description |
| :--- | :--- | :--- |
| **`tdai-memory-core`** | `8420` | Core data-plane & REST API (L0 turns, L1 facts, L2 playbooks, L3 directives) |
| **`tdai-proxy`** | `8096` | OpenAI/Anthropic-compatible proxy with automatic turn extraction & prompt injection |
| **`tdai-mcp-gateway`** | `8095` | MCP SSE server exposing cognitive memory tools |
| **`tdai-memory-hub`** | `8125` / `8424` | Web dashboard & knowledge graph manager |
| **`tdai-embed-server`** | `8090` | Local embedding server (`text-embedding-3-small`, 1536 dim) |

---

## 2. Agent Connection Matrix

### A. OMP / Pi Agent (`omp`)
- **Integration**: Direct extension hook at `~/.omp/agent/extensions/memory-tencentdb.ts`
- **Hook Events**:
  - `message_start`: Tracks latest user turn
  - `turn_end`: Captures completed assistant turn to `/v3/conversation/add`
  - `session_shutdown`: Flushes session to `/session/end`
- **Auth Key**: `TENCENTDB_MEMORY_API_KEY` (configured in `~/.omp/agent/.env`)
- **Fallback**: `__REDACTED_MEMORY_KEY__`

### B. Antigravity CLI (`agy`)
- **Integration**: Skill at `~/.gemini/antigravity-cli/skills/tdai-memory/SKILL.md`
- **Tools**: Direct HTTP REST curl calls & MCP SSE integration (`http://127.0.0.1:8095/sse`)
- **Default Tenancy**:
  - Team ID: `team-aqg8ql1bqk`
  - Agent ID: `agt-default`
  - User ID: `usr-aqeft9nupo`

### C. OpenCode Interpreter (`opencode`)
- **Integration**: OpenAI-compatible provider in `~/.config/opencode/opencode.jsonc`
- **Base URL**: `http://127.0.0.1:8096/opencode/default/v1`
- **API Key**: `__REDACTED_MEMORY_KEY__`

### D. DeepSeek Harness (`dsh`)
- **Integration**: Provider in `~/.dsh/settings.yaml` and `~/.dsh/.credentials.yaml`
- **Base URL**: `http://127.0.0.1:8096/dsh/default`
- **API Key**: `TDAI_MEMORY_API_KEY` (`__REDACTED_MEMORY_KEY__`)

### E. Hermes Agent (`hermes`)
- **Integration**: Custom provider in `~/.hermes/config.yaml` and environment `~/.hermes/.env`
- **Base URL**: `http://127.0.0.1:8096/hermes/default`
- **API Key**: `__REDACTED_MEMORY_KEY__`

---

## 3. Environment Variables Reference

Source `agents.env` in any shell to configure all agents:
```bash
source /root/my/tencentdb-agent-memory/agents.env
```

Key environment variables:
```bash
export TDAI_GATEWAY_HOST="127.0.0.1"
export TDAI_GATEWAY_PORT="8420"
export TENCENTDB_MEMORY_URL="http://127.0.0.1:8420"
export TENCENTDB_MEMORY_API_KEY="__REDACTED_MEMORY_KEY__"
export TDAI_GATEWAY_API_KEY="__REDACTED_MEMORY_KEY__"
export MEMORY_CORE_GATEWAY_API_KEY="__REDACTED_MEMORY_KEY__"
export TDAI_MEMORY_API_KEY="__REDACTED_MEMORY_KEY__"
```

---

## 4. Troubleshooting & Best Practices

1. **401 Unauthorized Errors on `/v3/conversation/add`**:
   - Cause: Missing or invalid Bearer token against `tdai-memory-core`.
   - Fix: Ensure `__REDACTED_MEMORY_KEY__` is present in the `Authorization: Bearer <token>` header.
2. **Session Locking & Auto-Compaction**:
   - If multiple turns are submitted simultaneously, `tdai-memory-core` queues operations via Redis pipeline worker (`worker-mt1neg68-rj2z`).
   - Client-side backoff exponential retry (250ms * 2^attempt) handles transient load gracefully.
