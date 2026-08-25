import type {
  Source,
  RegisterSourcePayload,
  Unit,
  WikiPage,
  WikiChunk,
  SearchResult,
  Worker,
  Task,
  PipelineJob,
  HealthResponse,
  MetricsResponse,
  Alert,
  OnboardRequest,
  OnboardResponse,
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
  listWikiPages(limit?: number) {
    return this.request<WikiPage[]>(
      "GET",
      `/wiki/pages?limit=${limit || 50}`,
    );
  }

  getWikiPage(id: string) {
    return this.request<WikiPage>("GET", `/wiki/pages/${id}`);
  }

  getWikiPageByFile(filePath: string) {
    return this.request<WikiPage>(
      "GET",
      `/wiki/by-file/${encodeURIComponent(filePath)}`,
    );
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
  }) {
    const qs = new URLSearchParams();
    if (params?.stage) qs.set("stage", params.stage);
    if (params?.status) qs.set("status", params.status);
    if (params?.worker_id) qs.set("worker_id", params.worker_id);
    if (params?.limit) qs.set("limit", String(params.limit));
    const q = qs.toString();
    return this.request<Task[]>("GET", `/tasks/${q ? `?${q}` : ""}`);
  }

  requeueTask(id: string) {
    return this.request<{ status: string }>("POST", `/tasks/${id}/requeue`);
  }

  // ── Jobs ─────────────────────────────────────────────────────────────────
  listJobs() {
    return this.request<PipelineJob[]>("GET", "/jobs/");
  }

  // ── System ───────────────────────────────────────────────────────────────
  getMetrics() {
    return this.request<MetricsResponse>("GET", "/metrics");
  }

  getAlerts() {
    return this.request<{ alerts: Alert[]; count: number }>("GET", "/alerts");
  }
}

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
