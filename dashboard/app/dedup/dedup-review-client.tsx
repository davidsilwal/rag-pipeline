"use client";

import { useState } from "react";
import { Check, X, FileText } from "lucide-react";
import type { DedupPendingItem } from "./page";
import { getApiClient } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { truncate } from "@/lib/utils";

type ReviewDecision = "keep" | "suppress";
type MethodFilter = "all" | "exact_sha256" | "minhash_lsh";

const METHOD_TABS: { value: MethodFilter; label: string; hint: string }[] = [
  { value: "all", label: "All", hint: "Every detected pair" },
  { value: "minhash_lsh", label: "Near-dup", hint: "Semantic near-duplicates (MinHash)" },
  { value: "exact_sha256", label: "Exact hash", hint: "Byte-identical content" },
];

function methodLabel(m: unknown): string {
  if (m === "minhash_lsh") return "near-dup";
  if (m === "exact_sha256") return "exact";
  return String(m ?? "unknown");
}

export function DedupReviewClient({
  initialPairs,
  initialTotal,
  initialError,
  initialMethod,
  initialMinSimilarity,
}: {
  initialPairs: DedupPendingItem[];
  initialTotal: number;
  initialError: string | null;
  initialMethod?: MethodFilter;
  initialMinSimilarity?: number;
}) {
  const { apiUrl, token } = useAuth();
  const [tab, setTab] = useState<MethodFilter>(initialMethod || "all");
  const [query, setQuery] = useState("");
  const [minSimilarity, setMinSimilarity] = useState<number>(
    initialMinSimilarity ?? 0.5,
  );
  const [pairs, setPairs] = useState<DedupPendingItem[]>(initialPairs);
  const [total, setTotal] = useState(initialTotal);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState<Record<string, boolean>>({});
  const [error, setError] = useState<string | null>(null);

  async function reload(methodTab: MethodFilter, minSim: number) {
    setLoading(true);
    setError(null);
    try {
      const api = getApiClient(apiUrl, token);
      const data = await api.listDedupPending({
        limit: 100,
        min_similarity: minSim,
        method: methodTab === "all" ? undefined : methodTab,
      });
      setPairs(data.pairs);
      setTotal(data.total);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load pairs");
    } finally {
      setLoading(false);
    }
  }

  function switchTab(m: MethodFilter) {
    setTab(m);
    void reload(m, minSimilarity);
  }

  function onMinSimilarityChange(v: number) {
    setMinSimilarity(v);
    void reload(tab, v);
  }

  async function handleReview(pairId: string, decision: ReviewDecision) {
    setSubmitting((prev) => ({ ...prev, [pairId]: true }));
    setError(null);
    try {
      const api = getApiClient(apiUrl, token);
      await api.submitDedupReview(pairId, {
        decision,
        reviewer: "dashboard-user",
      });
      // Remove the reviewed pair from the working set so it disappears.
      setPairs((prev) => prev.filter((p) => p.pair_id !== pairId));
      setTotal((t) => Math.max(0, t - 1));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Review failed");
    } finally {
      setSubmitting((prev) => ({ ...prev, [pairId]: false }));
    }
  }

  const filtered = pairs.filter((p) => {
    if (!query.trim()) return true;
    const q = query.toLowerCase();
    return [p.kept_source_name, p.kept_source_path, p.suppressed_source_name, p.suppressed_source_path]
      .join(" ")
      .toLowerCase()
      .includes(q);
  });

  return (
    <div className="space-y-6">
      <header className="space-y-2">
        <h1 className="text-2xl font-bold">Dedup Review</h1>
        <p className="text-sm text-zinc-500">
          {total} un-reviewed duplicate pair{total === 1 ? "" : "s"}.
          Review each pair to keep one copy (Accept) or suppress it as a
          duplicate. Use the tabs to focus on semantic near-duplicates vs
          exact hash matches.
        </p>
      </header>

      {(initialError || error) && (
        <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-800 dark:bg-amber-900/30 dark:text-amber-300">
          {error || initialError}
        </div>
      )}

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-1 rounded-md border border-zinc-300 dark:border-zinc-700 p-1 bg-zinc-50 dark:bg-zinc-900">
          {METHOD_TABS.map((m) => (
            <button
              key={m.value}
              type="button"
              onClick={() => switchTab(m.value)}
              title={m.hint}
              className={`inline-flex items-center gap-1.5 rounded px-2.5 py-1 text-xs font-medium transition-colors ${
                tab === m.value
                  ? "bg-indigo-100 text-indigo-700 dark:bg-indigo-900/40 dark:text-indigo-300"
                  : "text-zinc-600 hover:bg-zinc-200 dark:text-zinc-400 dark:hover:bg-zinc-800"
              }`}
            >
              {m.label}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-3 sm:justify-end">
          <input
            type="search"
            placeholder="Filter by source…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="flex-1 rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-2 text-sm sm:max-w-xs"
          />
          <label className="flex items-center gap-2 text-xs text-zinc-500 shrink-0">
            Min sim
            <input
              type="number"
              min={0}
              max={1}
              step={0.05}
              value={minSimilarity}
              onChange={(e) => onMinSimilarityChange(Number(e.target.value))}
              className="w-20 rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-2 py-1.5 text-sm"
            />
          </label>
        </div>
      </div>

      {loading && (
        <div className="text-sm text-zinc-500">Loading pairs…</div>
      )}

      {!loading && filtered.length === 0 ? (
        <div className="rounded-lg border border-dashed border-zinc-300 dark:border-zinc-700 p-8 text-center text-sm text-zinc-500">
          {total === 0
            ? "No reviewable pairs in this view. Run ingestion / dedup to generate candidates, or switch the method tab."
            : "No pairs match your filter."}
        </div>
      ) : (
        <ul className="divide-y divide-zinc-200 dark:divide-zinc-800">
          {filtered.map((p) => (
            <li key={p.pair_id} className="py-4">
              <div className="flex items-start gap-4">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap mb-1.5">
                    <span
                      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${
                        methodLabel(p.method) === "near-dup"
                          ? "bg-violet-100 text-violet-700 dark:bg-violet-900/30 dark:text-violet-300"
                          : "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400"
                      }`}
                    >
                      {methodLabel(p.method)}
                    </span>
                    <span className="text-xs text-zinc-500">
                      Similarity:{" "}
                      <span className="font-semibold">
                        {(p.similarity_score * 100).toFixed(0)}%
                      </span>
                    </span>
                  </div>

                  <div className="grid gap-3 sm:grid-cols-2">
                    {[
                      {
                        name: p.kept_source_name,
                        path: p.kept_source_path,
                        preview: p.kept_text_preview,
                        accent: "text-emerald-600",
                      },
                      {
                        name: p.suppressed_source_name,
                        path: p.suppressed_source_path,
                        preview: p.suppressed_text_preview,
                        accent: "text-red-600",
                      },
                    ].map((side, i) => (
                      <div
                        key={i}
                        className="rounded-md border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-950 p-3"
                      >
                        <div className="flex items-center gap-1.5 text-sm font-medium text-zinc-900 dark:text-zinc-100">
                          <FileText className={`h-3.5 w-3.5 ${side.accent}`} />
                          <span className="truncate">
                            {side.name || "Unknown source"}
                          </span>
                        </div>
                        {side.path && (
                          <div className="mt-0.5 text-xs text-zinc-400 truncate">
                            {side.path}
                          </div>
                        )}
                        {side.preview && (
                          <p className="mt-2 text-xs text-zinc-500 line-clamp-3">
                            {truncate(side.preview, 200)}
                          </p>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
                <div className="flex items-end space-x-2 shrink-0">
                  <button
                    type="button"
                    onClick={() => handleReview(p.pair_id, "keep")}
                    disabled={submitting[p.pair_id]}
                    className="inline-flex items-center gap-1.5 rounded-md border border-green-300 dark:border-green-700 px-3 py-1 text-xs font-medium text-green-700 dark:text-green-300 transition-colors hover:bg-green-50 dark:hover:bg-green-900/30 disabled:opacity-50"
                  >
                    {submitting[p.pair_id] ? (
                      "Working…"
                    ) : (
                      <>
                        <Check className="h-3 w-3" />
                        Accept
                      </>
                    )}
                  </button>
                  <button
                    type="button"
                    onClick={() => handleReview(p.pair_id, "suppress")}
                    disabled={submitting[p.pair_id]}
                    className="inline-flex items-center gap-1.5 rounded-md border border-red-300 dark:border-red-700 px-3 py-1 text-xs font-medium text-red-700 dark:text-red-300 transition-colors hover:bg-red-50 dark:hover:bg-red-900/30 disabled:opacity-50"
                  >
                    {submitting[p.pair_id] ? (
                      "Working…"
                    ) : (
                      <>
                        <X className="h-3 w-3" />
                        Suppress
                      </>
                    )}
                  </button>
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}