"use client";

import { History, RefreshCw } from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { StatusBadge } from "@/components/ui/badge";
import { useJobs } from "@/lib/hooks";
import { relativeTime, shortId, stageLabel } from "@/lib/utils";

export default function JobsPage() {
  const { data: jobs, isLoading, mutate } = useJobs();

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
              {jobs?.length ?? 0} jobs
            </span>
            <button
              onClick={() => mutate()}
              className="flex items-center gap-1 text-sm text-zinc-500 hover:text-zinc-700"
            >
              <RefreshCw className="h-3.5 w-3.5" />
              Refresh
            </button>
          </div>
        </div>

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
              {!jobs ? (
                [...Array(5)].map((_, i) => (
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
                    No jobs recorded
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
      </div>
    </AppShell>
  );
}
