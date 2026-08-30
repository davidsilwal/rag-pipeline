// ── Sources ──────────────────────────────────────────────────────────────────
export interface Source {
  source_id: string;
  drive_item_id: string | null;
  drive_id: string | null;
  file_path: string;
  file_name: string;
  mime_type: string;
  size_bytes: number;
  sha256_hash: string;
  status:
    | "discovered"
    | "downloaded"
    | "extracted"
    | "indexed"
    | "quarantine"
    | "error";
  source_type: "local" | "github" | "onedrive";
  source_url: string | null;
  source_metadata: Record<string, unknown>;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface RegisterSourcePayload {
  drive_item_id?: string | null;
  drive_id?: string | null;
  file_path: string;
  file_name: string;
  mime_type: string;
  size_bytes: number;
  sha256_hash: string;
  status?: string;
  source_type?: string;
  source_url?: string | null;
  source_metadata?: Record<string, unknown>;
}

// ── Units ────────────────────────────────────────────────────────────────────
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

// ── Wiki ─────────────────────────────────────────────────────────────────────
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
  git_commit_sha?: string | null;
  last_verified_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface WikiChunk {
  chunk_id: string;
  page_id: string;
  content: string;
  metadata: Record<string, unknown>;
  created_at: string | null;
}

export interface WikiGraphTerm {
  term: string;
  weight: number;
}

export interface WikiGraphNode {
  id: string;
  title: string;
  file_path: string;
  page_type: string;
  subfolder: string;
  preview: string;
  /** Node lives outside the selected scope (cross-project link target). */
  cross?: boolean;
  /** Cluster node in the whole-wiki macro view (a project or area). */
  cluster?: boolean;
  /** Cluster kind: "project" | "area". */
  kind?: string;
  /** Pages in a cluster node. */
  count?: number;
  /** Top distinctive terms with relative weights (comparison panel). */
  top_terms?: WikiGraphTerm[];
}

export interface WikiGraphLink {
  source: string;
  target: string;
  score: number;
  /** Edge connects a scope page to a related page outside the scope. */
  cross?: boolean;
  /** Top shared terms between the endpoints (drawn as edge labels). */
  terms?: string[];
  /** Shared terms weighted by the weaker side (comparison panel bars). */
  term_weights?: WikiGraphTerm[];
}

export interface WikiGraphResponse {
  scope: string;
  nodes: WikiGraphNode[];
  links: WikiGraphLink[];
}

// ── Export & Clustering ─────────────────────────────────────────────────────────
export interface Cluster {
  cluster_id: string;
  topic_name: string;
  page_count: number;
  entity_count: number;
  relationship_count: number;
  source_unit_count: number;
  keywords: string[];
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ClusterExportOptions {
  format: 'markdown' | 'json' | 'graphml' | 'zip' | 'context-pack';
  include_neighbors?: boolean;
  max_pages?: number;
  max_entities?: number;
}

export interface DedupPendingPair {
  pair_id: string;
  kept_unit_id: string;
  suppressed_unit_id: string;
  similarity_score: number;
  method: "exact_sha256" | "minhash_lsh" | string;
  created_at?: string | null;
  kept_source_id?: string | null;
  kept_source_name?: string | null;
  kept_source_path?: string | null;
  kept_text_preview?: string | null;
  suppressed_source_id?: string | null;
  suppressed_source_name?: string | null;
  suppressed_source_path?: string | null;
  suppressed_text_preview?: string | null;
}

export interface DedupReview {
  pair_id: string;
  source_a_id: string;
  source_b_id: string;
  similarity_score: number;
  decision: 'keep' | 'suppress' | 'merge' | 'skip';
  reviewer: string | null;
  reviewed_at?: string | null;
  notes: string | null;
  created_at?: string | null;
}

export interface DedupReviewRequest {
  decision: 'keep' | 'suppress' | 'merge' | 'skip';
  reviewer: string;
  notes?: string;
}

export interface DedupStats {
  total_pairs: number;
  pending_review: number;
  keep: number;
  suppress: number;
  merge: number;
  skip: number;
}

export interface IncrementalSource {
  source_id: string;
  source_name: string;
  source_version: number;
  last_processed_at: string | null;
  last_processed_by: string | null;
  status: string;
  needs_incremental: boolean;
}

export interface IncrementalPlan {
  source_id: string;
  file_path: string;
  source_version: number;
  last_processed_at: string | null;
  current_modified_at: string;
  is_stale: boolean;
  total_units: number;
  pending_units: number;
  recommendation: string;
  stages?: {
    embed: { needed: boolean; unit_count: number };
    dedupe: { needed: boolean; unit_count: number };
    extract: { needed: boolean; note?: string };
    cluster: { needed: boolean; affected_cluster_count: number; note?: string };
  };
  total_work?: number;
}

export interface IncrementalUpdateRequest {
  processed_by: string;
  reembed: boolean;
  rededupe: boolean;
  reextract: boolean;
  recluster: boolean;
}

export interface IncrementalUpdateResult {
  source_id: string;
  stages_executed: { stage: string; status: string; units_processed?: number }[];
  stages_skipped: string[];
  version_update: { new_version: number; last_processed_at: string };
}

// ── GraphRAG ─────────────────────────────────────────────────────────────────
export interface GraphragEntity {
  entity_id: string;
  name: string;
  entity_type: string;
  description: string;
  frequency: number;
}

export interface GraphragRelationship {
  rel_id: string;
  source: string;
  source_type: string;
  target: string;
  target_type: string;
  relationship_type: string;
  description: string;
  weight: number;
}

export interface GraphragCommunity {
  community_id: string;
  level: number;
  title: string;
  summary: string;
  member_entities: string[];
}

export interface GraphragStats {
  entities: number;
  relationships: number;
  communities: number;
  by_type: { type: string; count: number }[];
}

export interface GraphragProgress {
  processed: number;
  total: number;
  task_status: string | null;
  task_attempts: number;
}

// ── Search ───────────────────────────────────────────────────────────────────
export interface SearchResult {
  chunk_id: string;
  file_path?: string;
  heading_path?: string[];
  content?: string;
  rank?: number;
  rrf_score?: number;
}

export interface AskSource {
  chunk_id: string;
  file_path?: string;
  heading_path?: string[];
  content?: string;
  score?: number;
}

export interface AskResult {
  question: string;
  answer: string | null;
  sources: AskSource[];
  llm_error?: string | null;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  sources?: AskSource[];
  error?: boolean;
}

// ── Worker ───────────────────────────────────────────────────────────────────
export interface Worker {
  worker_id: string;
  name: string;
  platform: string;
  hostname: string | null;
  version: string | null;
  ip: string | null;
  status: "online" | "offline" | "draining";
  capabilities: Record<string, unknown>;
  stages_enabled: string[];
  concurrency_max: number;
  registered_at: string;
  last_heartbeat: string;
  heartbeat_age_seconds?: number;
  running_tasks?: number;
}

// ── Task ─────────────────────────────────────────────────────────────────────
export interface Task {
  task_id: string;
  stage: string;
  scope_type: string;
  scope_id: string;
  priority: number;
  status:
    | "queued"
    | "claimed"
    | "running"
    | "succeeded"
    | "failed"
    | "dead_letter";
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

// ── Jobs ─────────────────────────────────────────────────────────────────────
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
  lease_token: string | null;
  task_id: string | null;
  started_at: string;
}

// ── System ───────────────────────────────────────────────────────────────────
export interface HealthResponse {
  status: "ok" | "degraded";
  postgres: boolean;
  redis: boolean;
  disk_space_gb: number;
  queue: Record<
    string,
    { queued: number; claimed: number; dead_letter: number }
  >;
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

export interface OnboardRequest {
  name: string;
  platform: string;
  stages_enabled?: string[];
  capabilities?: Record<string, unknown>;
}

export interface OnboardResponse {
  worker_id: string;
  token: string;
  env: Record<string, string>;
}

// ── Pipeline stages ──────────────────────────────────────────────────────────
export const PIPELINE_STAGES = [
  "discover",
  "extract",
  "chunk",
  "embed",
  "dedup",
  "cluster",
  "consensus",
  "graphrag",
  "compile",
] as const;

export type PipelineStage = (typeof PIPELINE_STAGES)[number];

export const STAGE_COLORS: Record<string, string> = {
  discover: "bg-slate-500",
  extract: "bg-blue-500",
  chunk: "bg-indigo-500",
  embed: "bg-violet-500",
  dedup: "bg-purple-500",
  cluster: "bg-fuchsia-500",
  consensus: "bg-pink-500",
  graphrag: "bg-rose-500",
  compile: "bg-amber-500",
};

export const STATUS_COLORS: Record<string, string> = {
  online: "text-emerald-500",
  offline: "text-red-500",
  draining: "text-amber-500",
  discovered: "bg-slate-100 text-slate-700",
  downloaded: "bg-blue-100 text-blue-700",
  extracted: "bg-indigo-100 text-indigo-700",
  indexed: "bg-emerald-100 text-emerald-700",
  quarantine: "bg-amber-100 text-amber-700",
  error: "bg-red-100 text-red-700",
  queued: "bg-slate-100 text-slate-700",
  cloning: "bg-violet-100 text-violet-700",
  cloned: "bg-emerald-100 text-emerald-700",
  claimed: "bg-blue-100 text-blue-700",
  running: "bg-indigo-100 text-indigo-700",
  succeeded: "bg-emerald-100 text-emerald-700",
  failed: "bg-red-100 text-red-700",
  dead_letter: "bg-red-200 text-red-900",
};
