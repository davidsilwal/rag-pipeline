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
  Cluster,
  DedupReview,
  DedupPendingPair,
  DedupReviewRequest,
  DedupStats,
  IncrementalSource,  IncrementalPlan,
  IncrementalUpdateRequest,
  IncrementalUpdateResult,
  AskResult,
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

  // ── Ingest (add sources) ─────────────────────────────────────────────────
  browseIngestNode(path?: string) {
    const q = path ? `?path=${encodeURIComponent(path)}` : "";
    return this.request<{
      path: string;
      absolute: string;
      is_root: boolean;
      dirs: { name: string; path: string; is_dir: boolean; mime_type: string | null; size_bytes: number }[];
      files: { name: string; path: string; is_dir: boolean; mime_type: string | null; size_bytes: number }[];
    }>("GET", `/ingest/node${q}`);
  }

  registerServerPath(path: string) {
    return this.request<{ registered: number; source_ids: string[] }>(
      "POST",
      "/ingest/server",
      { path },
    );
  }

  async uploadSources(files: File[]) {
    const fd = new FormData();
    for (const f of files) fd.append("files", f, f.name);
    const res = await fetch(`${this.baseUrl}/ingest/upload`, {
      method: "POST",
      headers: this.token ? { Authorization: `Bearer ${this.token}` } : {},
      body: fd,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json() as Promise<{ registered: number; source_ids: string[] }>;
  }

  addUrlSource(url: string) {
    return this.request<{ status: string; url: string; clone_scope_id: string }>(
      "POST",
      "/ingest/url",
      { url },
    );
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

  askQuestion(question: string, top_k?: number, scope?: string, clusterId?: string) {
    return this.request<AskResult>("POST", "/ask/", {
      question,
      top_k: top_k || 6,
      scope: scope || "all",
      cluster_id: clusterId || null,
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

  // ── Jobs ────────────────────────────────────────────────────────────────
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

  // ── Clusters & Export ────────────────────────────────────────────────────
  listClusters(params?: { limit?: number; offset?: number }) {
    const qs = new URLSearchParams();
    if (params?.limit) qs.set("limit", String(params.limit));
    if (params?.offset) qs.set("offset", String(params.offset));
    const q = qs.toString();
    return this.request<Cluster[]>("GET", `/export/clusters${q ? `?${q}` : ""}`);
  }

  exportClusterMarkdown(clusterId: string) {
    return this.request<string>("GET", `/export/cluster/${clusterId}/markdown`);
  }

  exportClusterJson(clusterId: string) {
    return this.request<Record<string, unknown>>("GET", `/export/cluster/${clusterId}/json`);
  }

  exportClusterGraphml(clusterId: string) {
    return this.request<string>("GET", `/export/cluster/${clusterId}/graphml`);
  }

  exportClusterZip(clusterId: string) {
    return this.request<string>("GET", `/export/cluster/${clusterId}/zip`);
  }

  exportClusterContextPack(clusterId: string) {
    return this.request<string>("GET", `/export/cluster/${clusterId}/context-pack`);
  }

  getClusterGraph(minSharedSources?: number) {
    const q = minSharedSources ? `?min_shared_sources=${minSharedSources}` : "";
    return this.request<{
      nodes: {
        cluster_id: string;
        topic_name: string;
        top_keywords: string[];
        unit_count: number;
        created_at: string;
        consensus_score: number;
      }[];
      edges: {
        source: string;
        target: string;
        weight: number;
        shared_source_ids: string[];
      }[];
      edge_count: number;
    }>("GET", `/export/cluster-graph${q}`);
  }

  exportClusterSources(clusterId: string) {
    return this.request<{
      cluster: { cluster_id: string; topic_name: string; top_keywords: string[]; unit_count: number };
      sources: {
        source_id: string;
        file_name: string;
        file_path: string | null;
        source_type: string;
        status: string;
        unit_count: number;
      }[];
      total_sources: number;
      total_units: number;
    }>("GET", `/export/cluster/${clusterId}/sources`);
  }

  // ── Dedup Review ─────────────────────────────────────────────────────────
  listDedupPending(params?: {
    limit?: number;
    offset?: number;
    method?: string;
    min_similarity?: number;
  }) {
    const qs = new URLSearchParams();
    if (params?.limit) qs.set("limit", String(params.limit));
    if (params?.offset) qs.set("offset", String(params.offset));
    if (params?.method) qs.set("method", params.method);
    if (params?.min_similarity != null)
      qs.set("min_similarity", String(params.min_similarity));
    const q = qs.toString();
    return this.request<{ pairs: DedupPendingPair[]; total: number; limit: number; offset: number }>("GET", `/dedup/pending${q ? `?${q}` : ""}`);
  }

  listDedupReviews(params?: { limit?: number; offset?: number }) {
    const qs = new URLSearchParams();
    if (params?.limit) qs.set("limit", String(params.limit));
    if (params?.offset) qs.set("offset", String(params.offset));
    const q = qs.toString();
    return this.request<{ reviews: DedupReview[]; total: number }>("GET", `/dedup/reviews${q ? `?${q}` : ""}`);
  }

  submitDedupReview(pairId: string, data: DedupReviewRequest) {
    return this.request<DedupReview>("POST", `/dedup/review/${pairId}`, data);
  }

  getDedupStats() {
    return this.request<DedupStats>("GET", "/dedup/stats");
  }

  applyDedupSuppressions() {
    return this.request<{ suppressed: number; errors: string[] }>("POST", "/dedup/apply");
  }

  // ── Incremental Updates ────────────────────────────────────────────────────
  listStaleSources(params?: { limit?: number; offset?: number }) {
    const qs = new URLSearchParams();
    if (params?.limit) qs.set("limit", String(params.limit));
    if (params?.offset) qs.set("offset", String(params.offset));
    const q = qs.toString();
    return this.request<{ sources: IncrementalSource[]; total: number }>("GET", `/incremental/stale${q ? `?${q}` : ""}`);
  }

  getIncrementalPlan(sourceId: string) {
    return this.request<IncrementalPlan>("GET", `/incremental/plan/${sourceId}`);
  }

  detectIncrementalChanges(sourceId: string) {
    return this.request<IncrementalPlan>("GET", `/incremental/detect/${sourceId}`);
  }

  executeIncrementalUpdate(sourceId: string, data: IncrementalUpdateRequest) {
    return this.request<IncrementalUpdateResult>("POST", `/incremental/execute/${sourceId}`, data);
  }

  markSourceProcessed(sourceId: string, processedBy: string, notes?: string) {
    const qs = new URLSearchParams({ processed_by: processedBy });
    if (notes) qs.set("notes", notes);
    return this.request<{ source_id: string; new_version: number; last_processed_at: string }>("POST", `/incremental/mark-processed/${sourceId}?${qs.toString()}`);
  }

  // ── System ────────────────────────────────────────────────────────────────
  getMetrics() {
    return this.request<MetricsResponse>("GET", "/metrics");
  }

  getAlerts() {
    return this.request<{ alerts: Alert[]; count: number }>("GET", "/alerts");
  }
}

// Sent for "load the entire wiki" — large enough to exceed the page count
// but small enough to keep a single request.
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
