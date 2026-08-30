"use client";

import { useCallback, useEffect, useState } from "react";
import { History, RefreshCw, ChevronLeft, ChevronRight } from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { StatusBadge } from "@/components/ui/badge";
import { useApi } from "@/lib/hooks";
import { relativeTime, shortId, stageLabel } from "@/lib/utils";
import type { PipelineJob } from "@/lib/types";

const PAGE_SIZE = 50;

export default function JobsPage() {
  const api = useApi();
  const [jobs, setJobs] = useState<PipelineJob[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(0);
  const [status, setStatus] = useState("");
  const [stage, setStage] = useState("");

  const fetchJobsData = useCallback(async () => {
    return api.listJobs({
      status: status || undefined,
      stage: stage || undefined,
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
    });
  }, [api, page, status, stage]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetchJobsData();
      setJobs(res.jobs);
      setTotal(res.total);
    } catch {
      setJobs([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [fetchJobsData]);

  useEffect(() => {
    let active = true;
    (async () => {
      await Promise.resolve();
      if (!active) return;
      setLoading(true);
      try {
        const res = await fetchJobsData();
        if (!active) return;
        setJobs(res.jobs);
        setTotal(res.total);
      } catch {
        if (!active) return;
        setJobs([]);
        setTotal(0);
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, [fetchJobsData]);

  // Reset to page 0 when filters change
  const resetPage = () => setPage(0);

  const totalPages = Math.ceil(total / PAGE_SIZE);

  const selectClass =
    "rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-1.5 text-sm";

  return (
    <AppShell>
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-lg font-bold">
            <History className="h-5 w-5" />
            Pipeline Jobs
          </h2>
          <div className="flex items-center gap-3">
            <span className="text-sm text-zinc-500">
              {total} total jobs
            </span>
            <button
              onClick={load}
              className="flex items-center gap-1 text-sm text-zinc-500 hover:text-zinc-700"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
              Refresh
            </button>
          </div>
        </div>

        {/* Filters */}
        <div className="flex items-center gap-3">
          <select
            value={status}
            onChange={(e) => { setStatus(e.target.value); resetPage(); }}
            className={selectClass}
          >
            <option value="">All statuses</option>
            <option value="running">Running</option>
            <option value="completed">Completed</option>
            <option value="failed">Failed</option>
            <option value="queued">Queued</option>
          </select>
          <select
            value={stage}
            onChange={(e) => { setStage(e.target.value); resetPage(); }}
            className={selectClass}
          >
            <option value="">All stages</option>
            <option value="discover">Discover</option>
            <option value="extract">Extract</option>
            <option value="chunk">Chunk</option>
            <option value="embed">Embed</option>
            <option value="dedup">Dedup</option>
            <option value="cluster">Cluster</option>
            <option value="consensus">Consensus</option>
            <option value="graphrag">GraphRAG</option>
            <option value="compile">Compile</option>
          </select>
        </div>

        {/* Table */}
        <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-zinc-50 dark:bg-zinc-900">
              <tr>
                <th className="px-4 py-2 text-left font-medium text-zinc-600 dark:text-zinc-400">
                  Job
                </th>
                <th className="px-4 py-2 text-left font-medium text-zinc-600 dark:text-zinc-400">
                  Type
                </th>
                <th className="px-4 py-2 text-left font-medium text-zinc-600 dark:text-zinc-400">
                  Stage
                </th>
                <th className="px-4 py-2 text-left font-medium text-zinc-600 dark:text-zinc-400">
                  Status
                </th>
                <th className="px-4 py-2 text-right font-medium text-zinc-600 dark:text-zinc-400">
                  Processed
                </th>
                <th className="px-4 py-2 text-right font-medium text-zinc-600 dark:text-zinc-400">
                  Failed
                </th>
                <th className="px-4 py-2 text-left font-medium text-zinc-600 dark:text-zinc-400">
                  Worker
                </th>
                <th className="px-4 py-2 text-left font-medium text-zinc-600 dark:text-zinc-400">
                  Started
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
              {loading ? (
                [...Array(10)].map((_, i) => (
                  <tr key={i}>
                    <td colSpan={8} className="px-4 py-3">
                      <div className="h-4 bg-zinc-100 dark:bg-zinc-800 rounded animate-pulse" />
                    </td>
                  </tr>
                ))
              ) : jobs.length === 0 ? (
                <tr>
                  <td
                    colSpan={8}
                    className="px-4 py-8 text-center text-zinc-400"
                  >
                    No jobs found
                  </td>
                </tr>
              ) : (
                jobs.map((j) => (
                  <tr
                    key={j.job_id}
                    className="hover:bg-zinc-50 dark:hover:bg-zinc-900/50"
                  >
                    <td className="px-4 py-2.5 font-mono text-xs">
                      {shortId(j.job_id)}
                    </td>
                    <td className="px-4 py-2.5 text-xs">{j.job_type}</td>
                    <td className="px-4 py-2.5">
                      {j.stage ? (
                        <span className="text-xs">{stageLabel(j.stage)}</span>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="px-4 py-2.5">
                      <StatusBadge status={j.status} />
                    </td>
                    <td className="px-4 py-2.5 text-right text-xs">
                      {j.items_processed}
                    </td>
                    <td className="px-4 py-2.5 text-right text-xs">
                      {j.items_failed > 0 ? (
                        <span className="text-red-500">{j.items_failed}</span>
                      ) : (
                        "0"
                      )}
                    </td>
                    <td className="px-4 py-2.5 text-xs text-zinc-500">
                      {j.worker_id ? shortId(j.worker_id) : "—"}
                    </td>
                    <td className="px-4 py-2.5 text-xs text-zinc-500">
                      {relativeTime(j.started_at)}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="flex items-center justify-between">
            <span className="text-xs text-zinc-500">
              Page {page + 1} of {totalPages} ({total} jobs)
            </span>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                disabled={page === 0}
                className="inline-flex items-center gap-1 rounded-md border border-zinc-300 dark:border-zinc-700 px-3 py-1.5 text-xs font-medium text-zinc-600 dark:text-zinc-300 hover:bg-zinc-50 dark:hover:bg-zinc-800 disabled:opacity-50"
              >
                <ChevronLeft className="h-3 w-3" />
                Previous
              </button>
              {/* Page number buttons */}
              {Array.from({ length: Math.min(7, totalPages) }, (_, i) => {
                let pageNum: number;
                if (totalPages <= 7) {
                  pageNum = i;
                } else if (page < 3) {
                  pageNum = i;
                } else if (page > totalPages - 4) {
                  pageNum = totalPages - 7 + i;
                } else {
                  pageNum = page - 3 + i;
                }
                return (
                  <button
                    key={pageNum}
                    type="button"
                    onClick={() => setPage(pageNum)}
                    className={`rounded-md px-2.5 py-1 text-xs font-medium ${
                      pageNum === page
                        ? "bg-indigo-600 text-white"
                        : "text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800"
                    }`}
                  >
                    {pageNum + 1}
                  </button>
                );
              })}
              <button
                type="button"
                onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
                disabled={page >= totalPages - 1}
                className="inline-flex items-center gap-1 rounded-md border border-zinc-300 dark:border-zinc-700 px-3 py-1.5 text-xs font-medium text-zinc-600 dark:text-zinc-300 hover:bg-zinc-50 dark:hover:bg-zinc-800 disabled:opacity-50"
              >
                Next
                <ChevronRight className="h-3 w-3" />
              </button>
            </div>
          </div>
        )}
      </div>
    </AppShell>
  );
}
