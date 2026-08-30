"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  FolderOpen,
  Play,
  CheckCircle2,
  XCircle,
  Clock,
  Loader2,
  AlertTriangle,
  RefreshCw,
  FileStack,
} from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { StatusBadge } from "@/components/ui/badge";
import { useApi } from "@/lib/hooks";
import { relativeTime, shortId, stageLabel } from "@/lib/utils";
import { PIPELINE_STAGES } from "@/lib/types";
import type { Source, Task } from "@/lib/types";

interface ProcessingJob {
  sourceId: string;
  filePath: string;
  fileName: string;
  status: string;
  tasks: Task[];
  startedAt: string;
}

const STAGE_ORDER = [...PIPELINE_STAGES];

export default function ProcessPage() {
  const api = useApi();
  const [folderPath, setFolderPath] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [jobs, setJobs] = useState<ProcessingJob[]>([]);
  const [recentSources, setRecentSources] = useState<Source[]>([]);
  const [showSources, setShowSources] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Load recent sources to check which ones are being processed
  const fetchRecentSources = useCallback(async () => {
    return api.listSources({ limit: 50 });
  }, [api]);

  const loadRecentSources = useCallback(async () => {
    try {
      setRecentSources(await fetchRecentSources());
    } catch {
      // Ignore
    }
  }, [fetchRecentSources]);

  // Load tasks for active sources
  const fetchJobsFor = useCallback(async (sources: Source[]) => {
    const jobsList: ProcessingJob[] = [];
    for (const src of sources.filter(
      (s) =>
        s.status !== "indexed" &&
        s.status !== "error" &&
        s.status !== "quarantine",
    )) {
      try {
        const res = await api.listTasks({
          limit: 20,
        });
        const allTasks = res.tasks || [];
        const srcTasks = allTasks.filter(
          (t: Task) => t.scope_id === src.source_id,
        );
        jobsList.push({
          sourceId: src.source_id,
          filePath: src.file_path,
          fileName: src.file_name,
          status: src.status,
          tasks: srcTasks,
          startedAt: src.created_at || "",
        });
      } catch {
        // Skip
      }
    }
    return jobsList;
  }, [api]);

  const loadJobs = useCallback(async () => {
    try {
      setJobs(await fetchJobsFor(recentSources));
    } catch {
      // Ignore
    }
  }, [fetchJobsFor, recentSources]);

  // Initial load
  useEffect(() => {
    let active = true;
    (async () => {
      await Promise.resolve();
      if (!active) return;
      try {
        const sources = await fetchRecentSources();
        if (active) setRecentSources(sources);
      } catch {
        // Ignore
      }
    })();
    return () => {
      active = false;
    };
  }, [fetchRecentSources]);

  useEffect(() => {
    if (recentSources.length === 0) return;
    let active = true;
    (async () => {
      await Promise.resolve();
      if (!active) return;
      try {
        const jobsList = await fetchJobsFor(recentSources);
        if (active) setJobs(jobsList);
      } catch {
        // Ignore
      }
    })();
    return () => {
      active = false;
    };
  }, [fetchJobsFor, recentSources]);

  // Auto-poll while there are active jobs
  useEffect(() => {
    const hasActive = jobs.some(
      (j) =>
        j.status !== "indexed" &&
        j.status !== "error" &&
        j.status !== "quarantine",
    );
    if (hasActive) {
      pollRef.current = setInterval(() => {
        loadRecentSources();
        loadJobs();
      }, 5000);
    }
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [jobs, loadRecentSources, loadJobs]);

  // Submit a folder path for processing
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!folderPath.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      // Register the source — the worker's discover stage handles folder scanning,
      // but for a single file we can register it directly.
      const path = folderPath.trim();
      const fileName = path.split("/").pop() || path;
      const sha = await sha256(path);
      await api.registerSource({
        drive_item_id: `local:${sha}`,
        drive_id: "local",
        file_path: path,
        file_name: fileName,
        mime_type: guessMime(fileName),
        size_bytes: 0,
        sha256_hash: sha,
        status: "discovered",
        source_type: "local",
      });
      setFolderPath("");
      loadRecentSources();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to register source");
    } finally {
      setSubmitting(false);
    }
  };

  // Trigger discovery scan on the worker
  const handleDiscover = async () => {
    setSubmitting(true);
    setError(null);
    try {
      // The discover task is a corpus-scope task. We can trigger it by
      // listing tasks and checking, but there's no direct "trigger discover" endpoint.
      // Instead, show a message to the user.
      setError(
        "Discovery scan is triggered automatically by the worker on startup. " +
          "To scan a new folder, set LOCAL_SOURCE_DIR in docker-compose.yml and restart the worker.",
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <AppShell>
      <div className="space-y-6 max-w-4xl">
        <h2 className="flex items-center gap-2 text-lg font-bold">
          <FolderOpen className="h-5 w-5 text-indigo-500" />
          Process Sources
        </h2>

        {/* Input form */}
        <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-950 p-6">
          <h3 className="text-sm font-semibold text-zinc-700 dark:text-zinc-300 mb-3">
            Add a source to process
          </h3>
          <p className="text-xs text-zinc-500 mb-4">
            Enter a file path on the server (e.g.{" "}
            <code className="bg-zinc-100 dark:bg-zinc-800 px-1 rounded">
              projects/my-doc/docs.md
            </code>
            ). The file must be accessible to the worker container at this path.
          </p>
          <form onSubmit={handleSubmit} className="flex gap-2">
            <input
              type="text"
              value={folderPath}
              onChange={(e) => setFolderPath(e.target.value)}
              placeholder="e.g. projects/my-doc/README.md"
              className="flex-1 rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-4 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              disabled={submitting}
            />
            <button
              type="submit"
              disabled={submitting || !folderPath.trim()}
              className="inline-flex items-center gap-1.5 rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-500 disabled:opacity-50"
            >
              {submitting ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Play className="h-4 w-4" />
              )}
              Add Source
            </button>
            <button
              type="button"
              onClick={handleDiscover}
              disabled={submitting}
              className="inline-flex items-center gap-1.5 rounded-md border border-zinc-300 dark:border-zinc-700 px-4 py-2 text-sm font-medium text-zinc-600 dark:text-zinc-300 hover:bg-zinc-50 dark:hover:bg-zinc-800 disabled:opacity-50"
            >
              <RefreshCw className="h-4 w-4" />
              Scan Folder
            </button>
          </form>
          {error && (
            <div className="mt-3 rounded-md border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/30 px-3 py-2 text-xs text-amber-700 dark:text-amber-300">
              <AlertTriangle className="inline h-3 w-3 mr-1" />
              {error}
            </div>
          )}
        </div>

        {/* Active processing jobs */}
        {jobs.length > 0 && (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold text-zinc-700 dark:text-zinc-300">
                Active Sources ({jobs.length})
              </h3>
              <button
                type="button"
                onClick={() => {
                  loadRecentSources();
                  loadJobs();
                }}
                className="text-xs text-zinc-500 hover:text-zinc-700"
              >
                <RefreshCw className="h-3 w-3 inline" /> Refresh
              </button>
            </div>
            {jobs.map((job) => (
              <SourceJob key={job.sourceId} job={job} />
            ))}
          </div>
        )}

        {/* Recent sources toggle */}
        <div>
          <button
            type="button"
            onClick={() => {
              setShowSources(!showSources);
              if (!showSources) loadRecentSources();
            }}
            className="flex items-center gap-2 text-sm font-medium text-zinc-600 dark:text-zinc-400 hover:text-zinc-800 dark:hover:text-zinc-200"
          >
            <FileStack className="h-4 w-4" />
            {showSources ? "Hide" : "Show"} All Sources ({recentSources.length})
          </button>
          {showSources && (
            <div className="mt-3 rounded-lg border border-zinc-200 dark:border-zinc-800 overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-zinc-50 dark:bg-zinc-900">
                  <tr>
                    <th className="px-4 py-2 text-left font-medium text-zinc-600 dark:text-zinc-400">
                      File
                    </th>
                    <th className="px-4 py-2 text-left font-medium text-zinc-600 dark:text-zinc-400">
                      Status
                    </th>
                    <th className="px-4 py-2 text-left font-medium text-zinc-600 dark:text-zinc-400">
                      Updated
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
                  {recentSources.map((s) => (
                    <tr
                      key={s.source_id}
                      className="hover:bg-zinc-50 dark:hover:bg-zinc-900/50"
                    >
                      <td className="px-4 py-2.5">
                        <span className="font-medium">{s.file_name}</span>
                        <span className="ml-2 text-xs text-zinc-400">
                          {shortId(s.source_id)}
                        </span>
                      </td>
                      <td className="px-4 py-2.5">
                        <StatusBadge status={s.status} />
                      </td>
                      <td className="px-4 py-2.5 text-xs text-zinc-500">
                        {relativeTime(s.updated_at || s.created_at)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </AppShell>
  );
}

/** A single source processing job with pipeline stage progress */
function SourceJob({ job }: { job: ProcessingJob }) {
  const completedStages = new Set(
    job.tasks.filter((t) => t.status === "succeeded").map((t) => t.stage),
  );
  const failedStages = new Set(
    job.tasks
      .filter((t) => t.status === "failed" || t.status === "dead_letter")
      .map((t) => t.stage),
  );
  const runningTask = job.tasks.find(
    (t) => t.status === "running" || t.status === "claimed",
  );
  const currentStage = runningTask?.stage || null;

  const isComplete = job.status === "indexed";
  const hasFailed = job.status === "error" || job.status === "quarantine";
  const isActive =
    !isComplete && !hasFailed && (currentStage || completedStages.size > 0);

  return (
    <div
      className={`rounded-lg border p-4 ${
        isComplete
          ? "border-emerald-200 bg-emerald-50/50 dark:border-emerald-900 dark:bg-emerald-950/20"
          : hasFailed
            ? "border-red-200 bg-red-50/50 dark:border-red-900 dark:bg-red-950/20"
            : "border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-950"
      }`}
    >
      <div className="flex items-start justify-between gap-3 mb-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            {isComplete ? (
              <CheckCircle2 className="h-4 w-4 text-emerald-500 shrink-0" />
            ) : hasFailed ? (
              <XCircle className="h-4 w-4 text-red-500 shrink-0" />
            ) : isActive ? (
              <Loader2 className="h-4 w-4 text-indigo-500 animate-spin shrink-0" />
            ) : (
              <Clock className="h-4 w-4 text-zinc-400 shrink-0" />
            )}
            <h4 className="text-sm font-semibold truncate">{job.fileName}</h4>
          </div>
          <p className="text-xs text-zinc-500 mt-0.5 truncate">{job.filePath}</p>
        </div>
        <StatusBadge status={job.status} />
      </div>

      {/* Pipeline stage progress */}
      <div className="flex flex-wrap gap-1.5">
        {STAGE_ORDER.map((stage) => {
          const isDone = completedStages.has(stage);
          const isCurrent = currentStage === stage;
          const isFailed = failedStages.has(stage);
          return (
            <div
              key={stage}
              className={`flex items-center gap-1 rounded px-2 py-1 text-[11px] font-medium transition-colors ${
                isDone
                  ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300"
                  : isFailed
                    ? "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300"
                    : isCurrent
                      ? "bg-indigo-100 text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300"
                      : "bg-zinc-100 text-zinc-400 dark:bg-zinc-900 dark:text-zinc-600"
              }`}
            >
              {isDone ? (
                <CheckCircle2 className="h-3 w-3" />
              ) : isFailed ? (
                <XCircle className="h-3 w-3" />
              ) : isCurrent ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <div className="h-3 w-3 rounded-full border border-current opacity-40" />
              )}
              {stageLabel(stage)}
            </div>
          );
        })}
      </div>

      {/* Completion notification */}
      {isComplete && (
        <div className="mt-3 flex items-center justify-between gap-2 rounded-md bg-emerald-100 dark:bg-emerald-950/40 px-3 py-2 text-xs text-emerald-700 dark:text-emerald-300">
          <div className="flex items-center gap-2">
            <CheckCircle2 className="h-3.5 w-3.5" />
            Processing complete! The source has been compiled into a wiki page.
          </div>
          <Link
            href={`/wiki/${job.sourceId}`}
            className="shrink-0 rounded bg-emerald-600 px-2 py-1 text-[11px] font-semibold text-white hover:bg-emerald-700 transition-colors"
          >
            View Wiki Page →
          </Link>
        </div>
      )}
      {hasFailed && (
        <div className="mt-3 flex items-center gap-2 rounded-md bg-red-100 dark:bg-red-950/40 px-3 py-2 text-xs text-red-700 dark:text-red-300">
          <XCircle className="h-3.5 w-3.5" />
          Processing failed. Check the Tasks page for error details.
        </div>
      )}
    </div>
  );
}

/** Quick SHA-256 of a path string (client-side, for drive_item_id) */
async function sha256(input: string): Promise<string> {
  const data = new TextEncoder().encode(input);
  const hash = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(hash))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

/** Guess MIME type from filename extension */
function guessMime(name: string): string {
  const ext = name.split(".").pop()?.toLowerCase() || "";
  const map: Record<string, string> = {
    md: "text/markdown",
    markdown: "text/markdown",
    txt: "text/plain",
    py: "text/x-python",
    js: "text/javascript",
    ts: "text/typescript",
    json: "application/json",
    yaml: "text/yaml",
    yml: "text/yaml",
    csv: "text/csv",
    html: "text/html",
    xml: "text/xml",
    pdf: "application/pdf",
    docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  };
  return map[ext] || "application/octet-stream";
}
