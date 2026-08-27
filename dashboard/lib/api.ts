import type {
  Source,
  RegisterSourcePayload,
  Unit,
  WikiPage,
  WikiChunk,
  WikiGraphResponse,
  SearchResult,
  Worker,
  Task,
  PipelineJob,
  HealthResponse,
  MetricsResponse,
  Alert,
  OnboardRequest,
  OnboardResponse,
  GraphragEntity,
  GraphragRelationship,
  GraphragCommunity,
  GraphragStats,
  GraphragProgress,
} from "./types";

class ApiClient {
  private baseUrl: string;
  private token: string;

  constructor(baseUrl: string, token: string) {
    this.baseUrl = baseUrl;
    this.token = token;
  }

  setToken(token: string) {
    this.token = token;
  }

  setBaseUrl(url: string) {
    this.baseUrl = url;
  }

  private async request<T>(
    method: string,
    path: string,
    body?: unknown,
  ): Promise<T> {
    const headers: Record<string, string> = {
      Accept: "application/json",
    };
    if (this.token) {
      headers["Authorization"] = `Bearer ${this.token}`;
    }
    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
    }

    const res = await fetch(`${this.baseUrl}${path}`, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }

    if (res.status === 204 || res.headers.get("content-length") === "0") {
      return undefined as T;
    }

    const ct = res.headers.get("content-type") || "";
    if (ct.includes("application/json")) {
      return res.json() as Promise<T>;
    }
    return res.text() as unknown as T;
  }

  // ── Health (no auth required) ────────────────────────────────────────────
  getHealth() {
    return this.request<HealthResponse>("GET", "/health");
  }

  // ── Sources ──────────────────────────────────────────────────────────────
  listSources(params?: { status?: string; limit?: number }) {
    const qs = new URLSearchParams();
    if (params?.status) qs.set("status", params.status);
    if (params?.limit) qs.set("limit", String(params.limit));
    const q = qs.toString();
    return this.request<Source[]>("GET", `/sources/${q ? `?${q}` : ""}`);
  }

  getSource(id: string) {
    return this.request<Source>("GET", `/sources/by-id/${id}`);
  }

  registerSource(data: RegisterSourcePayload) {
    return this.request<{ source_id: string }>("POST", "/sources/register", data);
  }

  deleteSource(id: string) {
    return this.request<void>("DELETE", `/sources/${id}`);
  }

  getSourceText(id: string) {
    return this.request<string>("GET", `/sources/${id}/text`);
  }

  setSourceStatus(id: string, status: string, errorMessage?: string) {
    return this.request<{ status: string }>(
      "POST",
      `/sources/${id}/status`,
      { status, error_message: errorMessage || null },
    );
  }

  // ── Units ────────────────────────────────────────────────────────────────
  listUnits(params?: { source_id?: string; limit?: number }) {
    const qs = new URLSearchParams();
    if (params?.source_id) qs.set("source_id", params.source_id);
    if (params?.limit) qs.set("limit", String(params.limit));
    const q = qs.toString();
    return this.request<Unit[]>("GET", `/units/${q ? `?${q}` : ""}`);
  }

  // ── Wiki ─────────────────────────────────────────────────────────────────
  listWikiPages(limit?: number, prefix?: string) {
    // limit <= 0 means "return everything" — the dashboard lists the full
    // wiki and filters/paginates client-side. `prefix` narrows to pages under
    // a folder path (used for the sibling-docs sidebar on the reader).
    const lim = limit ?? 50;
    const qs = prefix ? `&prefix=${encodeURIComponent(prefix)}` : "";
    return this.request<WikiPage[]>("GET", `/wiki/pages?limit=${lim}${qs}`);
  }

  getWikiPage(id: string) {
    return this.request<WikiPage>("GET", `/wiki/pages/${id}`);
  }

  updateWikiPage(id: string, data: { markdown_body?: string; title?: string }) {
    return this.request<{ page_id: string; status: string }>(
      "PATCH",
      `/wiki/pages/${id}`,
      data,
    );
  }

  getWikiPageByFile(filePath: string) {
    return this.request<WikiPage>(
      "GET",
      `/wiki/by-file/${encodeURIComponent(filePath)}`,
    );
  }

  listWikiChunks(params?: { page_id?: string; limit?: number }) {
    const qs = new URLSearchParams();
    if (params?.page_id) qs.set("page_id", params.page_id);
    if (params?.limit) qs.set("limit", String(params.limit));
    const q = qs.toString();
    return this.request<WikiChunk[]>("GET", `/wiki/chunks/${q ? `?${q}` : ""}`);
  }

  getWikiGraph(
    scope: string,
    topK = 4,
    minScore = 0.1,
    cross = false,
    crossK = 5,
  ) {
    const qs = new URLSearchParams({
      scope,
      top_k: String(topK),
      min_score: String(minScore),
      cross: String(cross),
      cross_k: String(crossK),
    });
    return this.request<WikiGraphResponse>(
      "GET",
      `/wiki/graph?${qs.toString()}`,
    );
  }

  // ── GraphRAG ────────────────────────────────────────────────────────────
  listGraphragEntities(params?: {
    limit?: number;
    offset?: number;
    entity_type?: string;
    search?: string;
  }) {
    const qs = new URLSearchParams();
    if (params?.limit) qs.set("limit", String(params.limit));
    if (params?.offset) qs.set("offset", String(params.offset));
    if (params?.entity_type) qs.set("entity_type", params.entity_type);
    if (params?.search) qs.set("search", params.search);
    const q = qs.toString();
    return this.request<GraphragEntity[]>("GET", `/wiki/graphrag/entities${q ? `?${q}` : ""}`);
  }

  listGraphragRelationships(params?: {
    limit?: number;
    offset?: number;
    entity_id?: string;
  }) {
    const qs = new URLSearchParams();
    if (params?.limit) qs.set("limit", String(params.limit));
    if (params?.offset) qs.set("offset", String(params.offset));
    if (params?.entity_id) qs.set("entity_id", params.entity_id);
    const q = qs.toString();
    return this.request<GraphragRelationship[]>("GET", `/wiki/graphrag/relationships${q ? `?${q}` : ""}`);
  }

  listGraphragCommunities(limit?: number, offset?: number) {
    const qs = new URLSearchParams();
    if (limit) qs.set("limit", String(limit));
    if (offset) qs.set("offset", String(offset));
    const q = qs.toString();
    return this.request<GraphragCommunity[]>("GET", `/wiki/graphrag/communities${q ? `?${q}` : ""}`);
  }

  getGraphragStats() {
    return this.request<GraphragStats>("GET", "/wiki/graphrag/stats");
  }

  getGraphragProgress() {
    return this.request<GraphragProgress>("GET", "/wiki/graphrag/progress");
  }

  // ── Search ───────────────────────────────────────────────────────────────
  searchFTS(query: string, top_k?: number) {
    return this.request<SearchResult[]>("POST", "/search/fts", {
      query,
      top_k: top_k || 20,
    });
  }

  searchHybrid(query_text: string, top_k?: number) {
    return this.request<SearchResult[]>("POST", "/search/hybrid", {
      query_text,
      top_k: top_k || 20,
    });
  }

  // ── Workers ──────────────────────────────────────────────────────────────
  listWorkers() {
    return this.request<Worker[]>("GET", "/workers/");
  }

  getWorker(id: string) {
    return this.request<Worker>("GET", `/workers/${id}`);
  }

  onboardWorker(data: OnboardRequest) {
    return this.request<OnboardResponse>("POST", "/system/onboard", data);
  }

  // ── Tasks ────────────────────────────────────────────────────────────────
  listTasks(params?: {
    stage?: string;
    status?: string;
    worker_id?: string;
    limit?: number;
    offset?: number;
  }) {
    const qs = new URLSearchParams();
    if (params?.stage) qs.set("stage", params.stage);
    if (params?.status) qs.set("status", params.status);
    if (params?.worker_id) qs.set("worker_id", params.worker_id);
    if (params?.limit) qs.set("limit", String(params.limit));
    if (params?.offset) qs.set("offset", String(params.offset));
    const q = qs.toString();
    return this.request<{ tasks: Task[]; total: number; limit: number; offset: number }>("GET", `/tasks/${q ? `?${q}` : ""}`);
  }

  requeueTask(id: string) {
    return this.request<{ status: string }>("POST", `/tasks/${id}/requeue`);
  }

  // ── Jobs ─────────────────────────────────────────────────────────────────
  listJobs(params?: {
    status?: string;
    stage?: string;
    job_type?: string;
    limit?: number;
    offset?: number;
  }) {
    const qs = new URLSearchParams();
    if (params?.status) qs.set("status", params.status);
    if (params?.stage) qs.set("stage", params.stage);
    if (params?.job_type) qs.set("job_type", params.job_type);
    if (params?.limit) qs.set("limit", String(params.limit));
    if (params?.offset) qs.set("offset", String(params.offset));
    const q = qs.toString();
    return this.request<{ jobs: PipelineJob[]; total: number; limit: number; offset: number }>("GET", `/jobs/${q ? `?${q}` : ""}`);
  }

  // ── System ───────────────────────────────────────────────────────────────
  getMetrics() {
    return this.request<MetricsResponse>("GET", "/metrics");
  }

  getAlerts() {
    return this.request<{ alerts: Alert[]; count: number }>("GET", "/alerts");
  }
}

// Sent for "load the entire wiki" — large enough to exceed the page count
// but small enough to keep a single request. Works against both the current
// backend (which always applies LIMIT) and the updated one (which treats
// limit <= 0 as "no limit").
export const ALL_PAGES_LIMIT = 100_000;

// Singleton factory
let _client: ApiClient | null = null;

export function getApiClient(baseUrl: string, token: string): ApiClient {
  if (!_client) {
    _client = new ApiClient(baseUrl, token);
  } else {
    _client.setBaseUrl(baseUrl);
    _client.setToken(token);
  }
  return _client;
}
