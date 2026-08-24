# LLM Markdown Wiki — Next.js Frontend Plan

**Stack:** Next.js 15 (App Router) + TypeScript + Tailwind CSS v4 + shadcn/ui  
**Target:** Admin dashboard + wiki browser + search + worker/task observability  
**Auth:** Bearer token (admin API_TOKEN) — stored in localStorage, sent on all requests  
**Backend:** `http://<VPS_PUBLIC_HOST>/api/v1/` (FastAPI Control API)

---

## 1. API Surface Reference

All endpoints are under `/api/v1/` and require `Authorization: Bearer <token>`.

| Prefix | Endpoints | Purpose |
|--------|-----------|---------|
| **Sources** | `POST /register`, `POST /register-batch`, `DELETE /{id}`, `GET /?status=&limit=`, `GET /by-id/{id}`, `POST /{id}/blob`, `GET /{id}/blob`, `POST /{id}/text`, `GET /{id}/text`, `POST /{id}/status` | Content registry + blob/text storage |
| **Units** | `POST /batch`, `POST /` (thin-worker), `GET /?source_id=&limit=` | Canonical extracted units |
| **Wiki** | `GET /pages?limit=`, `POST /pages`, `GET /pages/{id}`, `GET /chunks?limit=&page_id=` | Wiki page CRUD + chunk retrieval |
| **Search** | `POST /fts` (query, top_k), `POST /hybrid` (query_text, query_vector, top_k) | Full-text + hybrid search |
| **Workers** | `POST /register`, `POST /{id}/heartbeat`, `POST /{id}/deregister`, `GET /`, `GET /{id}` | Worker lifecycle |
| **Tasks** | `POST /claim`, `POST /{id}/heartbeat`, `POST /{id}/complete`, `POST /{id}/fail`, `POST /{id}/requeue`, `GET /?stage=&status=&worker_id=&limit=` | Task queue management |
| **Jobs** | `POST /checkpoint`, `GET /` | Pipeline job history |
| **Embed Cache** | `POST /` | Embedding upsert (worker-only) |
| **System** | `GET /health`, `GET /metrics`, `GET /alerts`, `POST /onboard` | Health, observability, onboarding |

---

## 2. Architecture

```
next-app/
├── app/                          # App Router pages
│   ├── layout.tsx                # Root layout (sidebar + auth provider)
│   ├── page.tsx                  # Dashboard home
│   ├── login/page.tsx            # Token entry screen
│   ├── sources/
│   │   ├── page.tsx              # Source list (filterable by status)
│   │   └── [sourceId]/page.tsx   # Source detail (blob/text preview, units)
│   ├── wiki/
│   │   ├── page.tsx              # Wiki page browser
│   │   └── [pageId]/page.tsx     # Full wiki page reader (markdown render)
│   ├── search/page.tsx           # FTS + hybrid search
│   ├── tasks/
│   │   ├── page.tsx              # Task queue dashboard (filters, status)
│   │   └── [taskId]/page.tsx     # Task detail + history
│   ├── workers/
│   │   ├── page.tsx              # Worker list + status
│   │   ├── onboard/page.tsx      # Onboard new Colab/Deepnote worker
│   │   └── [workerId]/page.tsx   # Worker detail + heartbeats
│   ├── jobs/page.tsx             # Pipeline job history
│   └── system/
│       ├── health/page.tsx       # Health + metrics + alerts
│       └── settings/page.tsx     # API token management
├── components/                   # Reusable UI components
│   ├── ui/                       # shadcn primitives (button, card, table, etc.)
│   ├── layout/
│   │   ├── sidebar.tsx           # Navigation sidebar
│   │   ├── header.tsx            # Top bar with search + auth status
│   │   └── auth-provider.tsx     # Auth context + token storage
│   ├── sources/
│   │   ├── source-table.tsx      # Paginated source list
│   │   ├── source-status-badge.tsx
│   │   └── source-upload-dialog.tsx
│   ├── wiki/
│   │   ├── wiki-page-card.tsx
│   │   ├── wiki-reader.tsx       # Markdown renderer with headings
│   │   └── wiki-chunk-viewer.tsx
│   ├── search/
│   │   ├── search-bar.tsx
│   │   └── search-results.tsx
│   ├── tasks/
│   │   ├── task-table.tsx
│   │   ├── task-status-badge.tsx
│   │   └── task-detail-panel.tsx
│   ├── workers/
│   │   ├── worker-grid.tsx       # Worker status cards
│   │   ├── worker-status-indicator.tsx
│   │   └── onboard-form.tsx
│   └── shared/
│       ├── data-table.tsx        # Generic sortable/filterable table
│       ├── stat-card.tsx         # Metric display card
│       ├── alert-banner.tsx
│       ├── json-viewer.tsx       # Collapsible JSON display
│       ├── markdown-renderer.tsx # Render markdown content
│       ├── copy-button.tsx       # Copy to clipboard
│       └── relative-time.tsx     # "5 minutes ago" timestamps
├── lib/
│   ├── api.ts                    # Typed API client (fetch wrapper)
│   ├── types.ts                  # TypeScript interfaces matching API shapes
│   ├── auth.ts                   # Token read/write/clear helpers
│   ├── hooks/
│   │   ├── use-api.ts            # Generic data-fetching hook with SWR
│   │   ├── use-sources.ts        # Source CRUD hooks
│   │   ├── use-wiki.ts           # Wiki page hooks
│   │   ├── use-search.ts         # Search hooks
│   │   ├── use-tasks.ts          # Task queue hooks
│   │   ├── use-workers.ts        # Worker hooks
│   │   ├── use-jobs.ts           # Job history hooks
│   │   └── use-system.ts         # Health/metrics/alerts hooks
│   └── utils.ts                  # formatDate, truncate, mimeIcon, etc.
├── public/
│   └── favicon.ico
├── tailwind.config.ts
├── next.config.ts
├── package.json
└── tsconfig.json
```

---

## 3. Implementation Phases

### Phase 1 — Scaffolding + Auth + API Client (Day 1)

**Goal:** Running Next.js app with token auth and typed API client.

#### 1.1 Project init
```bash
npx create-next-app@latest wiki-dashboard --typescript --tailwind --app --src-dir=no
cd wiki-dashboard
npx shadcn@latest init   # shadcn/ui setup
```

#### 1.2 Install dependencies
```bash
npm install swr              # data fetching + caching
npm install react-markdown   # markdown rendering
npm install react-syntax-highlighter  # code blocks
npm install lucide-react     # icons (already in shadcn)
npm install date-fns         # relative time formatting
npm install nuqs             # URL search params state
npm install zustand          # minimal global state (auth token)
```

#### 1.3 `lib/types.ts` — All API response types
```typescript
// Source
export interface Source {
  source_id: string;
  drive_item_id: string | null;
  drive_id: string | null;
  file_path: string;
  file_name: string;
  mime_type: string;
  size_bytes: number;
  sha256_hash: string;
  status: "discovered" | "downloaded" | "extracted" | "indexed" | "quarantine" | "error";
  source_type: "local" | "github" | "onedrive";
  source_url: string | null;
  source_metadata: Record<string, unknown>;
}

// Unit
export interface Unit {
  unit_id: string;
  source_id: string;
  doc_id: string;
  unit_index: number;
  heading_path: string[];
  unit_type: string;
  raw_text: string;
  clean_text: string;
  char_start: number | null;
  char_end: number | null;
  page_number: number | null;
  bbox_coords: Record<string, unknown> | unknown[] | null;
  content_hash: string;
  disposition: string;
  quality_score: number;
}

// Wiki
export interface WikiPage {
  page_id: string;
  file_path: string;
  title: string;
  page_type: string;
  domain: string | null;
  status: string;
  frontmatter: Record<string, unknown>;
  markdown_preview?: string;
  markdown_body?: string;
  source_unit_ids?: string[];
  git_commit_sha?: string;
  created_at?: string;
  updated_at?: string;
}

export interface WikiChunk {
  chunk_id: string;
  page_id: string;
  content: string;
  metadata: Record<string, unknown>;
  created_at: string | null;
}

// Search
export interface SearchResult {
  chunk_id: string;
  file_path?: string;
  heading_path?: string[];
  content?: string;
  rank?: number;
  rrf_score?: number;
}

// Worker
export interface Worker {
  worker_id: string;
  name: string;
  platform: string;
  hostname: string | null;
  version: string | null;
  status: "online" | "offline" | "draining";
  capabilities: Record<string, unknown>;
  stages_enabled: string[];
  concurrency_max: number;
  registered_at: string;
  last_heartbeat: string;
  heartbeat_age_seconds?: number;
  running_tasks?: number;
}

// Task
export interface Task {
  task_id: string;
  stage: string;
  scope_type: string;
  scope_id: string;
  priority: number;
  status: "queued" | "claimed" | "running" | "succeeded" | "failed" | "dead_letter";
  attempts: number;
  max_attempts: number;
  payload: Record<string, unknown>;
  leased_by: string | null;
  lease_token: string | null;
  lease_expires_at: string | null;
  result_meta: Record<string, unknown> | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  completed_at: string | null;
}

// Job
export interface PipelineJob {
  job_id: string;
  job_type: string;
  status: string;
  items_processed: number;
  items_failed: number;
  log_summary: string | null;
  fingerprint: string | null;
  worker_id: string | null;
  stage: string | null;
  started_at: string;
}

// System
export interface HealthResponse {
  status: "ok" | "degraded";
  postgres: boolean;
  redis: boolean;
  disk_space_gb: number;
  queue: Record<string, { queued: number; claimed: number; dead_letter: number }>;
}

export interface MetricsResponse {
  queue_by_stage_status: { stage: string; status: string; n: number }[];
  workers_by_status: { status: string; n: number }[];
  stale_leases: number;
}

export interface Alert {
  severity: "info" | "warning" | "error";
  kind: string;
  [key: string]: unknown;
}
```

#### 1.4 `lib/api.ts` — Typed fetch wrapper
```typescript
const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";

class ApiClient {
  private token: string;

  constructor(token: string) {
    this.token = token;
  }

  setToken(token: string) {
    this.token = token;
  }

  private async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const res = await fetch(`${API_BASE}${path}`, {
      method,
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${this.token}`,
      },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    if (res.status === 204) return undefined as T;
    return res.json();
  }

  // Sources
  listSources(params?: { status?: string; limit?: number }) {
    const qs = new URLSearchParams();
    if (params?.status) qs.set("status", params.status);
    if (params?.limit) qs.set("limit", String(params.limit));
    return this.request<Source[]>("GET", `/sources/?${qs}`);
  }
  getSource(id: string) { return this.request<Source>("GET", `/sources/by-id/${id}`); }
  registerSource(data: RegisterSourcePayload) { return this.request<{source_id:string}>("POST", "/sources/register", data); }
  deleteSource(id: string) { return this.request<void>("DELETE", `/sources/${id}`); }
  getSourceText(id: string) { /* returns raw text */ }
  getSourceBlob(id: string) { /* returns ArrayBuffer */ }

  // Units
  listUnits(params?: { source_id?: string; limit?: number }) { /* ... */ }
  upsertUnitsBatch(data: { units: UnitIn[] }) { /* ... */ }

  // Wiki
  listWikiPages(limit?: number) { return this.request<WikiPage[]>("GET", `/wiki/pages?limit=${limit||50}`); }
  getWikiPage(id: string) { return this.request<WikiPage>("GET", `/wiki/pages/${id}`); }
  listWikiChunks(params?: { page_id?: string; limit?: number }) { /* ... */ }

  // Search
  searchFTS(query: string, top_k?: number) { return this.request<SearchResult[]>("POST", "/search/fts", { query, top_k: top_k||20 }); }
  searchHybrid(query_text: string, top_k?: number) { return this.request<SearchResult[]>("POST", "/search/hybrid", { query_text, top_k: top_k||20 }); }

  // Workers
  listWorkers() { return this.request<Worker[]>("GET", "/workers/"); }
  getWorker(id: string) { return this.request<Worker>("GET", `/workers/${id}`); }
  onboardWorker(data: OnboardRequest) { return this.request<{worker_id:string;token:string;env:Record<string,string>}>("POST", "/system/onboard", data); }

  // Tasks
  listTasks(params?: { stage?: string; status?: string; worker_id?: string; limit?: number }) { /* ... */ }
  requeueTask(id: string) { return this.request<{status:string}>("POST", `/tasks/${id}/requeue`); }

  // Jobs
  listJobs() { return this.request<PipelineJob[]>("GET", "/jobs/"); }

  // System
  getHealth() { return this.request<HealthResponse>("GET", "/health"); }
  getMetrics() { return this.request<MetricsResponse>("GET", "/metrics"); }
  getAlerts() { return this.request<{alerts:Alert[];count:number}>("GET", "/alerts"); }
}

export function createApiClient(token: string) {
  return new ApiClient(token);
}
```

#### 1.5 `lib/auth.ts` + `components/layout/auth-provider.tsx` — Token management
- Store token in `localStorage` (key: `wiki_api_token`)
- React context wraps the app; provides `token`, `setToken`, `logout`, `isAuthenticated`
- `login/page.tsx` — input field + "Connect" button → validates against `GET /health` → stores token
- Redirect unauthenticated users to `/login`

---

### Phase 2 — Dashboard Home + System Health (Day 2)

**Goal:** At-a-glance status page showing pipeline health, worker status, queue depth.

#### 2.1 `app/page.tsx` — Dashboard
```
┌─────────────────────────────────────────────────────────┐
│  Health: ✅ Postgres  ✅ Redis  💾 45.2 GB free         │
├──────────┬──────────┬──────────┬───────────────────────┤
│ Workers  │ Queued   │ Running  │ Dead Letters          │
│    3 🟢  │   12     │    4     │    0                  │
├──────────┴──────────┴──────────┴───────────────────────┤
│  Queue by Stage                                         │
│  extract: ████░░ 3 queued, 1 claimed                    │
│  chunk:   ██░░░░ 2 queued, 0 claimed                    │
│  embed:   ██████ 5 queued, 2 claimed                    │
│  dedup:   ░░░░░░ 0 queued, 0 claimed                    │
├─────────────────────────────────────────────────────────┤
│  ⚠ Alerts (2)                                          │
│  ⚠ worker gpu-box-02 offline (heartbeat stale 90s)     │
│  ⚠ task abc-123 queued 45 min ago (starvation)         │
├─────────────────────────────────────────────────────────┤
│  Recent Sources                                         │
│  [table: name, status, type, updated]                   │
└─────────────────────────────────────────────────────────┘
```

**Data sources:** `GET /health`, `GET /metrics`, `GET /alerts`, `GET /sources/?limit=10`

**Components:** `StatCard`, `AlertBanner`, `QueueBarChart` (simple CSS bars), `SourceTable`

#### 2.2 Auto-refresh
- Dashboard polls `/health` + `/metrics` every 15 seconds via SWR `refreshInterval`
- Alert count badge in sidebar updates in real-time

---

### Phase 3 — Sources Browser (Day 3)

**Goal:** Browse, filter, inspect, and manage sources.

#### 3.1 `app/sources/page.tsx`
- Filter bar: status dropdown (discovered/downloaded/extracted/indexed/quarantine/error), source type, text search
- Paginated table: file_name, mime_type, status (colored badge), size_bytes (formatted), source_type, updated_at
- Actions: view detail, delete (with confirmation), re-register
- "Register Source" button → opens `SourceUploadDialog` (form for manual registration)

#### 3.2 `app/sources/[sourceId]/page.tsx`
- Source metadata card (all fields, JSON viewer for source_metadata)
- Status badge + transition buttons (extract → chunk → etc.)
- Tabs:
  - **Extracted Text** — `GET /{id}/text` → rendered as plain text with line numbers
  - **Raw Blob** — download link or inline PDF/image viewer
  - **Units** — `GET /?source_id={id}` → paginated unit list with heading_path, text preview, bbox viewer

---

### Phase 4 — Wiki Browser + Reader (Day 4–5)

**Goal:** Browse wiki pages, read full markdown, view chunks.

#### 4.1 `app/wiki/page.tsx`
- Card grid: each card shows title, page_type badge, domain, status, updated_at, markdown_preview snippet
- Filter: page_type, domain, status
- Sort: by updated_at (newest first, default), title, page_type

#### 4.2 `app/wiki/[pageId]/page.tsx`
- Full-page markdown reader using `react-markdown` with:
  - Syntax highlighting for code blocks
  - Table rendering
  - Heading anchors for navigation
  - Frontmatter display (collapsible YAML block)
- Sidebar: table of contents (auto-generated from headings)
- Footer: source_unit_ids, git_commit_sha, created_at, updated_at
- "View Chunks" button → shows associated chunks with metadata

#### 4.3 Wiki page creation/editing (optional — later phase)
- `POST /pages` to create/update wiki pages
- Markdown editor (could use `@uiw/react-md-editor` or simple textarea)
- Preview mode toggle

---

### Phase 5 — Search (Day 5)

**Goal:** Full-text and hybrid search with result display.

#### 5.1 `app/search/page.tsx`
- Large search input at top
- Toggle: FTS (fast, lexical) vs Hybrid (semantic, requires client-side embedding)
- Results list: chunk content snippet, file_path, heading_path breadcrumb, rank/score
- Click result → opens wiki page or chunk detail panel
- Search history (stored in localStorage, last 10 queries)

**Note:** Hybrid search requires `query_vector`. For now, FTS-only is usable. Later: integrate `@xenova/transformers` to compute `bge-m3` vectors client-side, or proxy through a worker endpoint.

---

### Phase 6 — Task Queue Dashboard (Day 6)

**Goal:** Monitor pipeline task queue, inspect individual tasks, manage dead letters.

#### 6.1 `app/tasks/page.tsx`
- Filter bar: stage (extract/chunk/embed/dedup/cluster/consensus/graphrag/compile), status, worker
- Table: task_id (truncated), stage, scope_type, status (badge), attempts, created_at, error_message preview
- Bulk actions: requeue dead letters
- Real-time: auto-refresh every 10s

#### 6.2 `app/tasks/[taskId]/page.tsx`
- Full task detail: all fields
- Timeline: created → claimed → started → completed/failed
- Error display: full error_message with syntax highlighting
- Action: requeue (if dead_letter)

---

### Phase 7 — Workers Management (Day 7)

**Goal:** Monitor workers, onboard new ephemeral workers.

#### 7.1 `app/workers/page.tsx`
- Worker cards grid: name, platform, status indicator (green/red/amber), heartbeat age, running tasks, concurrency bar
- Click card → worker detail page

#### 7.2 `app/workers/onboard/page.tsx`
- Form: name, platform (dropdown), stages_enabled (checkboxes), capabilities (JSON editor)
- Submit → `POST /system/onboard` → display worker_id, token, env vars
- "Copy env block" button for easy Colab/Deepnote paste

#### 7.3 `app/workers/[workerId]/page.tsx`
- Worker metadata
- Heartbeat timeline (last N heartbeats from task_queue leased_by)
- Active tasks list
- Historical tasks

---

### Phase 8 — Jobs History + System (Day 8)

#### 8.1 `app/jobs/page.tsx`
- Pipeline job history table: job_type, status, items_processed/failed, worker_id, stage, started_at
- Filter by stage, status

#### 8.2 `app/system/health/page.tsx`
- Health indicators: Postgres (green/red), Redis (green/red), disk space (gauge)
- Queue depth per stage (bar chart)
- Worker status breakdown (pie/donut or simple counters)
- Stale leases count
- Alerts list with severity badges

---

## 4. Design System

### Color Palette (Tailwind + shadcn)
- **Status colors:** `emerald-500` (online/healthy), `amber-500` (warning/degraded), `red-500` (error/offline), `slate-400` (unknown)
- **Stage colors:** Each pipeline stage gets a distinct color for charts/badges
- **Dark mode:** shadcn's built-in dark mode via `next-themes`

### Key UI Patterns
| Pattern | Component | Usage |
|---------|-----------|-------|
| Status badge | `Badge` variant per status | Source status, task status, worker status |
| Data table | `DataTable` (shadcn + TanStack Table) | All list views with sort/filter/paginate |
| Stat card | `Card` + `StatCard` | Dashboard metrics |
| JSON viewer | Collapsible tree | source_metadata, task payload/result_meta |
| Markdown render | `react-markdown` | Wiki page reader |
| Relative time | `date-fns formatDistanceToNow` | Heartbeats, created_at, updated_at |
| Copy button | `navigator.clipboard` | Tokens, env vars, IDs |
| Confirm dialog | `AlertDialog` (shadcn) | Delete operations |

---

## 5. Data Fetching Strategy

Use **SWR** (stale-while-revalidate) for all API calls:

```typescript
// Example hook
function useSources(status?: string) {
  const { token } = useAuth();
  const api = useMemo(() => createApiClient(token), [token]);
  return useSWR(
    token ? ["sources", status] : null,
    () => api.listSources({ status }),
    { refreshInterval: 30_000 }  // 30s auto-refresh
  );
}
```

**Refresh intervals:**
| Data | Interval | Rationale |
|------|----------|-----------|
| Health + metrics | 15s | Dashboard needs near-real-time |
| Workers | 15s | Heartbeat every 30s |
| Tasks | 10s | Queue changes fast |
| Sources | 30s | Changes less frequently |
| Wiki pages | 60s | Rarely changes during operation |
| Jobs | 60s | Append-only history |

---

## 6. Environment Configuration

```bash
# .env.local
NEXT_PUBLIC_API_URL=http://david-contabo-wo:8000/api/v1
```

---

## 7. Deployment

### Option A: Static export + reverse proxy
```bash
npm run build  # outputs to .next/
# Deploy .next/ to VPS, serve with Caddy alongside FastAPI
```

### Option B: Docker (standalone)
```dockerfile
FROM node:22-alpine AS builder
WORKDIR /app
COPY . .
RUN npm ci && npm run build

FROM node:22-alpine
WORKDIR /app
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
CMD ["node", "server.js"]
```

Add to `docker-compose.yml`:
```yaml
frontend:
  build: ./next-app
  ports:
    - "3000:3000"
  environment:
    NEXT_PUBLIC_API_URL: http://control-api:8000/api/v1
  depends_on:
    - control-api
```

### Option C: Vercel / Netlify
- Set `NEXT_PUBLIC_API_URL` as env var
- `npm run build` → deploy
- Caddy on VPS proxies `/wiki` → frontend, `/api` → FastAPI

---

## 8. Priority Order (Build Sequence)

| Phase | What | Time | Dependency |
|-------|------|------|------------|
| **1** | Scaffold + Auth + API client + types | 1 day | None |
| **2** | Dashboard home (health, metrics, alerts) | 1 day | Phase 1 |
| **3** | Sources browser | 1 day | Phase 1 |
| **4** | Wiki browser + reader | 2 days | Phase 1 |
| **5** | Search | 0.5 days | Phase 1 |
| **6** | Task queue dashboard | 1 day | Phase 1 |
| **7** | Workers management | 1 day | Phase 1 |
| **8** | Jobs + system page | 0.5 days | Phase 1 |

**Total: ~8 days** for full implementation. Phases 2–8 can be parallelized after Phase 1.

---

## 9. Future Enhancements (Post-MVP)

1. **Real-time updates** — WebSocket push for task/worker status (replace polling)
2. **Wiki editor** — In-browser markdown editor with live preview
3. **Search embeddings** — Client-side `bge-m3` via `@xenova/transformers` for true hybrid search
4. **Source upload** — Drag-and-drop file upload (multipart to `POST /{id}/blob`)
5. **Pipeline visualization** — Mermaid or D3 flow diagram showing source → extract → chunk → embed → compile
6. **Dark/light mode toggle** — Already supported by shadcn, just needs theme provider
7. **Multi-language** — i18n for UI strings
8. **Mobile responsive** — shadcn components are responsive, but sidebar needs collapse behavior
9. **API docs link** — Direct link to FastAPI `/docs` Swagger UI
10. **Export** — Download wiki pages as PDF/MD files
