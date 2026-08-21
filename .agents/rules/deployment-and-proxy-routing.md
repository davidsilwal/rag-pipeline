# Deployment and Proxy Routing Learnings

## 1. Public Domain Map
- **DeepSeek Harness Chat:** `https://chat.aarohanithub.com.np` (Port 3080)
- **TDAI Memory Hub:** `https://memory-hub.aarohanithub.com.np` (Port 8125)
- **TDAI Agent Memory Proxy:** `https://agent-memory-proxy.aarohanithub.com.np` (Port 8096)
- **TDAI MCP Gateway:** `https://memory-mcp.aarohanithub.com.np` (Port 8095)
- **TDAI Memory Core:** `https://memory-core.aarohanithub.com.np` (Port 8420)
- **LiteLLM Proxy:** `https://llm.aarohanithub.com.np` (Port 4000)
- **OpenWiki:** `https://wiki.aarohanithub.com.np` (Port 8081 / 4321)

---

## 2. DeepSeek Harness (`dsh`) Configuration & Fixes
- **Settings & Privileged Methods Access over Remote Domains:**
  - In `packages/client/connection/src/index.ts`, privileged methods (`settings.*`, `credentials.*`, `llm.discoverModels`, etc.) must pass `isTrustedApiRequest(request, trustedHosts)` instead of `[]` to allow access when accessing via trusted domains (e.g. `chat.aarohanithub.com.np`).
  - In `packages/client/connection/src/client/index.ts`, `isLoopback` on the client `ConnectionHandle` should be `true` so the web UI unlocks full interactive editing (Add/Edit provider, API keys).
  - In `packages/client/tsdown.client.ts`, `INLINE_SAFE` must include `client-web-react`, `client-schema-form`, and `client-ui-attachment` to ensure client plugins bundle necessary helpers inline rather than requiring non-existent platform seed modules.

---

## 3. TDAI Memory Hub Client Access Endpoints
- In `tdai-memory-hub` container (and `start-memory-hub.sh`), set:
  ```bash
  REMOTE_INSTANCE_PROXY_URL="https://agent-memory-proxy.aarohanithub.com.np"
  ```
- This ensures the UI dashboard at `https://memory-hub.aarohanithub.com.np/#/team/api-keys` displays real public base URLs (`/dsh/default`, `/claude-code/default`, `/codex/default`, etc.) instead of `localhost:8096`.

---

## 4. Agent Proxy DeepSeek Endpoint
- **Base URL for SDKs / Clients:** `https://agent-memory-proxy.aarohanithub.com.np/dsh/default/v1`
- **Chat Completions Endpoint:** `https://agent-memory-proxy.aarohanithub.com.np/dsh/default/v1/chat/completions`
