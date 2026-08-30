import { AppShell } from "@/components/layout/app-shell";
import { ClusterListClient } from "./cluster-list-client";

export interface ClusterItem {
  cluster_id: string;
  topic_name: string;
  page_count: number;
  entity_count: number;
  relationship_count: number;
  source_unit_count: number;
  keywords: string[];
  consensus_score?: number;
  created_at?: string;
}

interface ClusterListResult {
  clusters: ClusterItem[];
  error: string | null;
}

interface ClusterApiRow {
  cluster_id: string;
  topic_name: string;
  top_keywords?: string[] | null;
  unit_count: number;
  page_count: number;
  entity_count: number;
  relationship_count: number;
  consensus_score?: number;
  created_at?: string;
}

async function fetchClusters(): Promise<ClusterListResult> {
  const env = process.env.NEXT_PUBLIC_API_URL;
  if (!env) {
    return { clusters: [], error: "API URL not configured" };
  }
  const token = process.env.DASHBOARD_API_TOKEN ?? "";
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  try {
    const res = await fetch(
      `${env.replace(/\/+$/, "")}/export/clusters?limit=100`,
      { cache: "no-store", headers },
    );
    if (!res.ok) {
      return {
        clusters: [],
        error:
          res.status === 401 || res.status === 403
            ? "The API rejected the dashboard token (401/403). Check DASHBOARD_API_TOKEN."
            : `The API returned an error (HTTP ${res.status}).`,
      };
    }
    const body = (await res.json()) as { clusters?: ClusterApiRow[] };
    const rows = body.clusters ?? [];
    const clusters: ClusterItem[] = rows.map((r) => ({
      cluster_id: r.cluster_id,
      topic_name: r.topic_name,
      page_count: r.page_count ?? 0,
      entity_count: r.entity_count ?? 0,
      relationship_count: r.relationship_count ?? 0,
      source_unit_count: r.unit_count ?? 0,
      keywords: r.top_keywords ?? [],
      consensus_score: r.consensus_score,
      created_at: r.created_at,
    }));
    return { clusters, error: null };
  } catch {
    return {
      clusters: [],
      error: "Could not reach the API server. Check NEXT_PUBLIC_API_URL.",
    };
  }
}

export default async function ClustersPage() {
  const { clusters, error } = await fetchClusters();

  return (
    <AppShell>
      <ClusterListClient initialClusters={clusters} initialError={error} />
    </AppShell>
  );
}