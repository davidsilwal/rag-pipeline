"use client";

import { useState } from "react";
import Link from "next/link";
import {
  ListTodo,
  RefreshCw,
  RotateCcw,
  AlertTriangle,
} from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { StatusBadge, Badge } from "@/components/ui/badge";
import { useTasks, useApi } from "@/lib/hooks";
import {
  relativeTime,
  shortId,
  truncate,
  stageLabel,
} from "@/lib/utils";
import { PIPELINE_STAGES } from "@/lib/types";

export default function TasksPage() {
  const [stage, setStage] = useState("");
  const [status, setStatus] = useState("");
  const [limit, setLimit] = useState(100);
  const { data: tasks, mutate } = useTasks({
    stage: stage || undefined,
    status: status || undefined,
    limit,
  });
  const api = useApi();
  const [requeuing, setRequeuing] = useState<string | null>(null);

  const handleRequeue = async (id: string) => {
    setRequeuing(id);
    try {
      await api.requeueTask(id);
      mutate();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Requeue failed");
    } finally {
      setRequeuing(null);
    }
  };

  return (
    <AppShell>
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-lg font-bold">
            <ListTodo className="h-5 w-5" />
            Tasks
          </h2>
          <div className="flex items-center gap-3">
            <span className="text-sm text-zinc-500">
              {tasks?.length ?? 0} tasks
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

        {/* Filters */}
        <div className="flex items-center gap-3">
          <select
            value={stage}
            onChange={(e) => setStage(e.target.value)}
            className="rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-1.5 text-sm"
          >
            <option value="">All stages</option>
            {PIPELINE_STAGES.map((s) => (
              <option key={s} value={s}>
                {stageLabel(s)}
              </option>
            ))}
          </select>
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            className="rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-1.5 text-sm"
          >
            <option value="">All statuses</option>
            <option value="queued">Queued</option>
            <option value="claimed">Claimed</option>
            <option value="succeeded">Succeeded</option>
            <option value="failed">Failed</option>
            <option value="dead_letter">Dead Letter</option>
          </select>
          <select
            value={limit}
            onChange={(e) => setLimit(Number(e.target.value))}
            className="rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-1.5 text-sm"
          >
            <option value={50}>50</option>
            <option value={100}>100</option>
            <option value={500}>500</option>
          </select>
        </div>

        {/* Table */}
        <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-zinc-50 dark:bg-zinc-900">
              <tr>
                <th className="px-4 py-2 text-left font-medium text-zinc-600 dark:text-zinc-400">
                  Task
                </th>
                <th className="px-4 py-2 text-left font-medium text-zinc-600 dark:text-zinc-400">
                  Stage
                </th>
                <th className="px-4 py-2 text-left font-medium text-zinc-600 dark:text-zinc-400">
                  Scope
                </th>
                <th className="px-4 py-2 text-left font-medium text-zinc-600 dark:text-zinc-400">
                  Status
                </th>
                <th className="px-4 py-2 text-center font-medium text-zinc-600 dark:text-zinc-400">
                  Attempts
                </th>
                <th className="px-4 py-2 text-left font-medium text-zinc-600 dark:text-zinc-400">
                  Created
                </th>
                <th className="px-4 py-2 text-left font-medium text-zinc-600 dark:text-zinc-400">
                  Error
                </th>
                <th className="px-4 py-2 text-right font-medium text-zinc-600 dark:text-zinc-400">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
              {!tasks ? (
                [...Array(5)].map((_, i) => (
                  <tr key={i}>
                    <td colSpan={8} className="px-4 py-3">
                      <div className="h-4 bg-zinc-100 dark:bg-zinc-800 rounded animate-pulse" />
                    </td>
                  </tr>
                ))
              ) : tasks.length === 0 ? (
                <tr>
                  <td
                    colSpan={8}
                    className="px-4 py-8 text-center text-zinc-400"
                  >
                    No tasks found
                  </td>
                </tr>
              ) : (
                tasks.map((t) => (
                  <tr
                    key={t.task_id}
                    className="hover:bg-zinc-50 dark:hover:bg-zinc-900/50"
                  >
                    <td className="px-4 py-2.5 font-mono text-xs">
                      {shortId(t.task_id)}
                    </td>
                    <td className="px-4 py-2.5">
                      <Badge>{stageLabel(t.stage)}</Badge>
                    </td>
                    <td className="px-4 py-2.5 text-xs text-zinc-500">
                      {t.scope_type}:{shortId(t.scope_id)}
                    </td>
                    <td className="px-4 py-2.5">
                      <StatusBadge status={t.status} />
                    </td>
                    <td className="px-4 py-2.5 text-center text-xs">
                      {t.attempts}/{t.max_attempts}
                    </td>
                    <td className="px-4 py-2.5 text-xs text-zinc-500">
                      {relativeTime(t.created_at)}
                    </td>
                    <td className="px-4 py-2.5 text-xs text-red-500 max-w-xs truncate">
                      {t.error_message
                        ? truncate(t.error_message, 80)
                        : "—"}
                    </td>
                    <td className="px-4 py-2.5 text-right">
                      {t.status === "dead_letter" && (
                        <button
                          onClick={() => handleRequeue(t.task_id)}
                          disabled={requeuing === t.task_id}
                          className="inline-flex items-center gap-1 text-xs text-amber-600 hover:text-amber-800 disabled:opacity-50"
                        >
                          <RotateCcw className="h-3 w-3" />
                          Requeue
                        </button>
                      )}
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
