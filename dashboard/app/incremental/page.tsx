import { AppShell } from "@/components/layout/app-shell";
import { IncrementalSourceListClient } from "./incremental-source-list-client";

export interface IncrementalSourceItem {
  source_id: string;
  source_version: string;
  last_processed_at: string;
  last_processed_by: string;
  status: string;
  needs_incremental: boolean;
}

interface IncrementalSourceListResult {
  sources: IncrementalSourceItem[];
  total: number;
  error: string | null;
}

async function fetchIncrementalSources(): Promise<IncrementalSourceListResult> {
  const env = process.env.NEXT_PUBLIC_API_URL;
  if (!env) {
    return { sources: [], total: 0, error: "API URL not configured" };
  }
  const token = process.env.DASHBOARD_API_TOKEN ?? "";
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  try {
    const res = await fetch(
      `${env.replace(/\/+$/, "")}/incremental/stale?limit=100`,
      { cache: "no-store", headers },
    );
    if (!res.ok) {
      return {
        sources: [],
        total: 0,
        error:
          res.status === 401 || res.status === 403
            ? "The API rejected the dashboard token (401/403). Check DASHBOARD_API_TOKEN."
            : `The API returned an error (HTTP ${res.status}).`,
      };
    }
    return { ...(await res.json()), error: null };
  } catch {
    return {
      sources: [],
      total: 0,
      error: "Could not reach the API server. Check NEXT_PUBLIC_API_URL.",
    };
  }
}

export default async function IncrementalSourcesPage() {
  const { sources, total, error } = await fetchIncrementalSources();

  return (
    <AppShell>
      <IncrementalSourceListClient
        initialSources={sources}
        initialTotal={total}
        initialError={error}
      />
    </AppShell>
  );
}