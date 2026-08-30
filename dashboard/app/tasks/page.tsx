"use client";

import { useCallback, useEffect, useState } from "react";
import { ListTodo, RefreshCw, RotateCcw, ChevronLeft, ChevronRight } from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { StatusBadge, Badge } from "@/components/ui/badge";
import { useApi } from "@/lib/hooks";
import {
  relativeTime,
  shortId,
  truncate,
  stageLabel,
} from "@/lib/utils";
import { PIPELINE_STAGES } from "@/lib/types";
import type { Task } from "@/lib/types";

const PAGE_SIZE = 100;

export default function TasksPage() {
  const api = useApi();
  const [tasks, setTasks] = useState<Task[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(0);
  const [stage, setStage] = useState("");
  const [status, setStatus] = useState("");
  const [requeuing, setRequeuing] = useState<string | null>(null);

  const fetchTasksData = useCallback(async () => {
    return api.listTasks({
      stage: stage || undefined,
      status: status || undefined,
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
    });
  }, [api, page, stage, status]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetchTasksData();
      setTasks(res.tasks);
      setTotal(res.total);
    } catch {
      setTasks([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [fetchTasksData]);

  useEffect(() => {
    let active = true;
    (async () => {
      await Promise.resolve();
      if (!active) return;
      setLoading(true);
      try {
        const res = await fetchTasksData();
        if (!active) return;
        setTasks(res.tasks);
        setTotal(res.total);
      } catch {
        if (!active) return;
        setTasks([]);
        setTotal(0);
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, [fetchTasksData]);

  const resetPage = () => setPage(0);
  const totalPages = Math.ceil(total / PAGE_SIZE);

  const handleRequeue = async (id: string) => {
    setRequeuing(id);
    try {
      await api.requeueTask(id);
      load();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Requeue failed");
    } finally {
      setRequeuing(null);
    }
  };

  const selectClass =
    "rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-1.5 text-sm";

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
              {total} total tasks
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
            value={stage}
            onChange={(e) => { setStage(e.target.value); resetPage(); }}
            className={selectClass}
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
            onChange={(e) => { setStatus(e.target.value); resetPage(); }}
            className={selectClass}
          >
            <option value="">All statuses</option>
            <option value="queued">Queued</option>
            <option value="claimed">Claimed</option>
            <option value="running">Running</option>
            <option value="succeeded">Succeeded</option>
            <option value="failed">Failed</option>
            <option value="dead_letter">Dead Letter</option>
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
              {loading ? (
                [...Array(10)].map((_, i) => (
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

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="flex items-center justify-between">
            <span className="text-xs text-zinc-500">
              Page {page + 1} of {totalPages} ({total} tasks)
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
