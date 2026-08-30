"use client";

import { useState } from "react";
import {
  RefreshCw,
  Zap,
  Clock,
  User,
  Check,
} from "lucide-react";
import type { IncrementalSourceItem } from "./page";
import { getApiClient } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export function IncrementalSourceListClient({
  initialSources,
  initialTotal,
  initialError,
}: {
  initialSources: IncrementalSourceItem[];
  initialTotal: number;
  initialError: string | null;
}) {
  const { apiUrl, token } = useAuth();
  const [query, setQuery] = useState("");
  const [running, setRunning] = useState<
    Record<string, "plan" | "detect" | "execute" | "mark" | undefined>
  >({});
  const [error, setError] = useState<string | null>(null);

  const filtered = initialSources.filter((s) => {
    if (!query.trim()) return true;
    const q = query.toLowerCase();
    return s.source_id.toLowerCase().includes(q);
  });

  async function handleDetect(sourceId: string) {
    setRunning((prev) => ({ ...prev, [sourceId]: "detect" }));
    setError(null);
    try {
      const api = getApiClient(apiUrl, token);
      const plan = await api.detectIncrementalChanges(sourceId);
      alert(`Detected ${plan.pending_units ?? 0} items to process for source ${sourceId}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Detection failed");
    } finally {
      setRunning((prev) => ({ ...prev, [sourceId]: undefined }));
    }
  }

  async function handleExecute(sourceId: string) {
    setRunning((prev) => ({ ...prev, [sourceId]: "execute" }));
    setError(null);
    try {
      const api = getApiClient(apiUrl, token);
      const result = await api.executeIncrementalUpdate(sourceId, {
        processed_by: "dashboard-user",
        reembed: true,
        rededupe: true,
        reextract: false,
        recluster: true,
      });
      alert(
        `Update started: ${result.stages_executed?.length ?? 0} stage(s) queued`,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Execution failed");
    } finally {
      setRunning((prev) => ({ ...prev, [sourceId]: undefined }));
    }
  }

  async function handleMarkProcessed(sourceId: string) {
    setRunning((prev) => ({ ...prev, [sourceId]: "mark" }));
    setError(null);
    try {
      const api = getApiClient(apiUrl, token);
      const result = await api.markSourceProcessed(
        sourceId,
        "dashboard-user",
        "Marked via dashboard",
      );
      alert(`Source marked as processed, new version: ${result.new_version}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Mark failed");
    } finally {
      setRunning((prev) => ({ ...prev, [sourceId]: undefined }));
    }
  }

  return (
    <div className="space-y-6">
      <header className="space-y-2">
        <h1 className="text-2xl font-bold">Incremental Updates</h1>
        <p className="text-sm text-zinc-500">
          {initialTotal} source{initialTotal === 1 ? "" : "s"} with stale content.
          Run detection to see what changed, then execute updates.
        </p>
      </header>

      {initialError && (
        <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-800 dark:bg-amber-900/30 dark:text-amber-300">
          {initialError}
        </div>
      )}

      {error && (
        <div className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-300">
          {error}
        </div>
      )}

      <div className="flex items-center gap-3 sm:flex-row">
        <input
          type="search"
          placeholder="Filter by source ID…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="flex-1 rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-2 text-sm"
        />
      </div>

      {filtered.length === 0 ? (
        <div className="rounded-lg border border-dashed border-zinc-300 dark:border-zinc-700 p-8 text-center text-sm text-zinc-500">
          {initialTotal === 0
            ? "No stale sources. All content is up to date."
            : "No sources match your filter."}
        </div>
      ) : (
        <div className="space-y-4">
          {filtered.map((s) => (
            <div key={s.source_id} className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-4">
              <div className="flex items-start justify-between gap-3">
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-medium text-zinc-900 dark:text-zinc-100 truncate">
                    {s.source_id}
                  </div>
                  <div className="mt-1 flex items-center gap-2 text-xs text-zinc-500 dark:text-zinc-400">
                    <Clock className="h-3 w-3" />
                    <span title="Last processed">{s.last_processed_at}</span>
                  </div>
                  <div className="mt-1 flex items-center gap-2 text-xs text-zinc-500 dark:text-zinc-400">
                    <User className="h-3 w-3" />
                    <span>{s.last_processed_by}</span>
                  </div>
                  <div className="mt-2 flex items-center gap-3">
                    <span className="px-2 py-0.5 rounded-full text-xs font-medium">
                      {s.needs_incremental
                        ? (
                          <>
                            <Zap className="h-3 w-3 mr-1" /> Stale
                          </>
                        )
                        : (
                          <>
                            <Check className="h-3 w-3 mr-1" /> Fresh
                          </>
                        )}
                    </span>
                  </div>
                </div>
                <div className="flex items-end space-x-2">
                  {s.needs_incremental ? (
                    <>
                      <button
                        type="button"
                        onClick={() => handleDetect(s.source_id)}
                        disabled={running[s.source_id] === "detect"}
                        className="inline-flex items-center gap-1.5 rounded-md border border-blue-300 dark:border-blue-700 px-3 py-1 text-xs font-medium transition-colors hover:bg-blue-50 dark:hover:bg-blue-900/30 disabled:opacity-50"
                      >
                        {running[s.source_id] === "detect" ? (
                          <>
                            <RefreshCw className="h-3 w-3" /> Detecting…
                          </>
                        ) : (
                          <>
                            <RefreshCw className="h-3 w-3" /> Detect
                          </>
                        )}
                      </button>
                      <button
                        type="button"
                        onClick={() => handleExecute(s.source_id)}
                        disabled={running[s.source_id] === "execute" || !s.needs_incremental}
                        className="ml-2 inline-flex items-center gap-1.5 rounded-md border border-green-300 dark:border-green-700 px-3 py-1 text-xs font-medium transition-colors hover:bg-green-50 dark:hover:bg-green-900/30 disabled:opacity-50"
                      >
                        {running[s.source_id] === "execute" ? (
                          <>
                            <Zap className="h-3 w-3" /> Executing…
                          </>
                        ) : (
                          <>
                            <Zap className="h-3 w-3" /> Execute
                          </>
                        )}
                      </button>
                      <button
                        type="button"
                        onClick={() => handleMarkProcessed(s.source_id)}
                        disabled={running[s.source_id] === "mark"}
                        className="ml-2 inline-flex items-center gap-1.5 rounded-md border border-amber-300 dark:border-amber-700 px-3 py-1 text-xs font-medium transition-colors hover:bg-amber-50 dark:hover:bg-amber-900/30 disabled:opacity-50"
                      >
                        {running[s.source_id] === "mark" ? (
                          <>
                            <Clock className="h-3 w-3" /> Marking…
                          </>
                        ) : (
                          <>
                            <Clock className="h-3 w-3" /> Mark Processed
                          </>
                        )}
                      </button>
                    </>
                  ) : (
                    <>
                      <span className="inline-flex items-center gap-1 rounded-full bg-green-100 px-2 py-0.5 text-xs font-medium text-green-800 dark:bg-green-900/30 dark:text-green-300">
                        <Check className="h-3 w-3" /> Up to date
                      </span>
                    </>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}