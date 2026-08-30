"use client";

import { useState } from "react";
import Link from "next/link";
import { FileStack, Trash2, RefreshCw, Eye, Plus, GitBranch, Loader2, Check } from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { StatusBadge, getStatusVariant } from "@/components/ui/badge";
import { AddSourcePanel } from "@/components/sources/add-source-panel";
import { useSources, useApi } from "@/lib/hooks";
import { formatBytes, relativeTime, mimeIcon } from "@/lib/utils";

const STATUS_OPTIONS = [
  "",
  "discovered",
  "downloaded",
  "extracted",
  "indexed",
  "quarantine",
  "error",
];

export default function SourcesPage() {
  const [status, setStatus] = useState<string>("");
  const [limit, setLimit] = useState(50);
  const [showAdd, setShowAdd] = useState(false);
  const { data: sources, mutate } = useSources(status || undefined, limit);
  const api = useApi();
  const [deleting, setDeleting] = useState<string | null>(null);

  const handleDelete = async (id: string) => {
    if (!confirm("Delete this source and all associated data?")) return;
    setDeleting(id);
    try {
      await api.deleteSource(id);
      mutate();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Delete failed");
    } finally {
      setDeleting(null);
    }
  };

  return (
    <AppShell>
      <div className="space-y-4">
        {/* Header */}
        <div className="flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-lg font-bold">
            <FileStack className="h-5 w-5" />
            Sources
          </h2>
          <span className="text-sm text-zinc-500">
            {sources?.length ?? 0} items
          </span>
          <button
            onClick={() => setShowAdd(true)}
            className="inline-flex items-center gap-1.5 rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700"
          >
            <Plus className="h-4 w-4" />
            Add source
          </button>
        </div>

        {/* Filters */}
        <div className="flex items-center gap-3">
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            className="rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-1.5 text-sm"
          >
            <option value="">All statuses</option>
            {STATUS_OPTIONS.filter(Boolean).map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <select
            value={limit}
            onChange={(e) => setLimit(Number(e.target.value))}
            className="rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-1.5 text-sm"
          >
            <option value={25}>25</option>
            <option value={50}>50</option>
            <option value={100}>100</option>
          </select>
          <button
            onClick={() => mutate()}
            className="flex items-center gap-1 text-sm text-zinc-500 hover:text-zinc-700"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            Refresh
          </button>
        </div>

        {/* Table */}
        <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-zinc-50 dark:bg-zinc-900">
              <tr>
                <th className="px-4 py-2 text-left font-medium text-zinc-600 dark:text-zinc-400">
                  File
                </th>
                <th className="px-4 py-2 text-left font-medium text-zinc-600 dark:text-zinc-400">
                  Type
                </th>
                <th className="px-4 py-2 text-left font-medium text-zinc-600 dark:text-zinc-400">
                  Status
                </th>
                <th className="px-4 py-2 text-right font-medium text-zinc-600 dark:text-zinc-400">
                  Size
                </th>
                <th className="px-4 py-2 text-left font-medium text-zinc-600 dark:text-zinc-400">
                  Updated
                </th>
                <th className="px-4 py-2 text-right font-medium text-zinc-600 dark:text-zinc-400">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
              {!sources ? (
                [...Array(5)].map((_, i) => (
                  <tr key={i}>
                    <td colSpan={6} className="px-4 py-3">
                      <div className="h-4 bg-zinc-100 dark:bg-zinc-800 rounded animate-pulse" />
                    </td>
                  </tr>
                ))
              ) : sources.length === 0 ? (
                <tr>
                  <td
                    colSpan={6}
                    className="px-4 py-8 text-center text-zinc-400"
                  >
                    No sources found
                  </td>
                </tr>
              ) : (
                sources.map((s) => (
                  <tr
                    key={s.source_id}
                    className="hover:bg-zinc-50 dark:hover:bg-zinc-900/50"
                  >
                    <td className="px-4 py-2.5">
                      <div className="flex items-center gap-2">
                        <span>{mimeIcon(s.mime_type)}</span>
                        <div className="min-w-0">
                          <Link
                            href={`/sources/${s.source_id}`}
                            className="font-medium text-indigo-600 hover:text-indigo-800 truncate max-w-xs block"
                          >
                            {s.file_name}
                          </Link>
                          {s.source_type === "github" &&
                            Boolean(s.source_metadata?.is_repo_marker) && (
                              <CloneProgress
                                status={String(
                                  s.source_metadata.clone_status || s.status || "",
                                )}
                              />
                            )}
                        </div>
                      </div>
                    </td>
                    <td className="px-4 py-2.5 text-zinc-500 text-xs">
                      {s.mime_type}
                    </td>
                    <td className="px-4 py-2.5">
                      <StatusBadge status={s.status} />
                    </td>
                    <td className="px-4 py-2.5 text-right text-zinc-500 text-xs">
                      {formatBytes(s.size_bytes)}
                    </td>
                    <td className="px-4 py-2.5 text-zinc-500 text-xs">
                      {relativeTime(s.updated_at || s.created_at)}
                    </td>
                    <td className="px-4 py-2.5 text-right">
                      <div className="flex items-center justify-end gap-1">
                        <Link
                          href={`/sources/${s.source_id}`}
                          className="p-1 text-zinc-400 hover:text-indigo-600"
                        >
                          <Eye className="h-4 w-4" />
                        </Link>
                        <button
                          onClick={() => handleDelete(s.source_id)}
                          disabled={deleting === s.source_id}
                          className="p-1 text-zinc-400 hover:text-red-600 disabled:opacity-50"
                        >
                          <Trash2 className="h-4 w-4" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {showAdd && (
        <AddSourcePanel
          onClose={() => setShowAdd(false)}
          onAdded={() => {
            setShowAdd(false);
            mutate();
          }}
        />
      )}
    </AppShell>
  );
}

function CloneProgress({ status }: { status: string }) {
  const variant = getStatusVariant(status);
  const label = status === "succeeded" ? "done" : status;
  const busy = status === "queued" || status === "claimed" || status === "running";
  return (
    <span
      className={`mt-0.5 flex items-center gap-1 text-[11px] ${variantToText(variant)}`}
      title={`GitHub clone: ${status}`}
    >
      {status === "succeeded" || status === "indexed" ? (
        <Check className="h-3 w-3" />
      ) : busy ? (
        <Loader2 className="h-3 w-3 animate-spin" />
      ) : (
        <GitBranch className="h-3 w-3" />
      )}
      repo · {label}
    </span>
  );
}

function variantToText(v: ReturnType<typeof getStatusVariant>): string {
  switch (v) {
    case "success":
      return "text-emerald-600";
    case "error":
      return "text-red-600";
    case "warning":
      return "text-amber-600";
    default:
      return "text-zinc-400";
  }
}
