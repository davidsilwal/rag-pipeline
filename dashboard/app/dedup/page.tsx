import { AppShell } from "@/components/layout/app-shell";
import { DedupReviewClient } from "./dedup-review-client";

export interface DedupPendingItem {
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

interface DedupReviewResult {
  pairs: DedupPendingItem[];
  total: number;
  error: string | null;
}

async function fetchDedupPending(
  method?: string,
): Promise<DedupReviewResult> {
  const env = process.env.NEXT_PUBLIC_API_URL;
  if (!env) {
    return { pairs: [], total: 0, error: "API URL not configured" };
  }
  const token = process.env.DASHBOARD_API_TOKEN ?? "";
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const methodQs = method ? `&method=${encodeURIComponent(method)}` : "";
  try {
    const res = await fetch(
      `${env.replace(/\/+$/, "")}/dedup/pending?limit=100${methodQs}`,
      { cache: "no-store", headers },
    );
    if (!res.ok) {
      return {
        pairs: [],
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
      pairs: [],
      total: 0,
      error: "Could not reach the API server. Check NEXT_PUBLIC_API_URL.",
    };
  }
}

export default async function DedupReviewPage({
  searchParams,
}: {
  searchParams: Promise<{ method?: string; min_similarity?: string }>;
}) {
  const sp = await searchParams;
  const method = sp.method || undefined;
  const minSimilarity = sp.min_similarity
    ? Number(sp.min_similarity)
    : undefined;
  const { pairs, total, error } = await fetchDedupPending(method);
  const validMethod =
    method === "all" ||
    method === "exact_sha256" ||
    method === "minhash_lsh"
      ? method
      : method
        ? undefined
        : "all";

  return (
    <AppShell>
      <DedupReviewClient
        initialPairs={pairs}
        initialTotal={total}
        initialError={error}
        initialMethod={validMethod}
        initialMinSimilarity={minSimilarity}
      />
    </AppShell>
  );
}