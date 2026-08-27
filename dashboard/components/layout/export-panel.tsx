"use client";

import React, { useState, useRef } from "react";
import {
  Download,
  Filter,
  Calendar,
  ChevronDown,
  ChevronUp,
  CheckCircle2,
  Loader2,
  X,
  FileArchive,
} from "lucide-react";
import { useAuth } from "@/lib/auth";

// ─── Filter Options ────────────────────────────────────────────────────────────
const PAGE_TYPES = ["page", "redirect", "stub"];
const ENTITY_TYPES = ["concept", "technology", "event", "org", "person", "location"];

interface ExportFilters {
  prefix: string;
  pageType: string;
  entityTypes: string[];
}

// ─── Progress Bar ──────────────────────────────────────────────────────────────
function ProgressBar({ progress, label }: { progress: number; label: string }) {
  return (
    <div className="w-full">
      <div className="flex justify-between text-xs text-zinc-500 mb-1">
        <span>{label}</span>
        <span>{Math.round(progress)}%</span>
      </div>
      <div className="w-full h-1.5 bg-zinc-200 dark:bg-zinc-800 rounded-full overflow-hidden">
        <div
          className="h-full bg-indigo-500 transition-all duration-300 ease-out rounded-full"
          style={{ width: `${Math.min(progress, 100)}%` }}
        />
      </div>
    </div>
  );
}

// ─── Filter Modal ──────────────────────────────────────────────────────────────
function FilterModal({
  filters,
  setFilters,
  onApply,
  onClose,
}: {
  filters: ExportFilters;
  setFilters: (f: ExportFilters) => void;
  onApply: () => void;
  onClose: () => void;
}) {
  const toggleEntityType = (t: string) => {
    const next = filters.entityTypes.includes(t)
      ? filters.entityTypes.filter((x) => x !== t)
      : [...filters.entityTypes, t];
    setFilters({ ...filters, entityTypes: next });
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white dark:bg-zinc-900 rounded-xl shadow-xl w-full max-w-md mx-4 overflow-hidden">
        <div className="flex items-center justify-between px-5 py-4 border-b border-zinc-200 dark:border-zinc-800">
          <h3 className="font-semibold text-sm">Export Filters</h3>
          <button onClick={onClose} className="text-zinc-400 hover:text-zinc-600">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="px-5 py-4 space-y-4">
          {/* Path prefix */}
          <div>
            <label className="block text-xs font-medium text-zinc-600 dark:text-zinc-400 mb-1">
              Path Prefix
            </label>
            <input
              type="text"
              value={filters.prefix}
              onChange={(e) => setFilters({ ...filters, prefix: e.target.value })}
              placeholder="e.g. kubernetes (blank = all)"
              className="w-full px-3 py-2 text-sm border border-zinc-300 dark:border-zinc-700 rounded-lg bg-white dark:bg-zinc-800 focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
          </div>

          {/* Page type */}
          <div>
            <label className="block text-xs font-medium text-zinc-600 dark:text-zinc-400 mb-1">
              Page Type
            </label>
            <select
              value={filters.pageType}
              onChange={(e) => setFilters({ ...filters, pageType: e.target.value })}
              className="w-full px-3 py-2 text-sm border border-zinc-300 dark:border-zinc-700 rounded-lg bg-white dark:bg-zinc-800 focus:outline-none focus:ring-2 focus:ring-indigo-500"
            >
              <option value="">All types</option>
              {PAGE_TYPES.map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
          </div>

          {/* Entity types */}
          <div>
            <label className="block text-xs font-medium text-zinc-600 dark:text-zinc-400 mb-1">
              Entity Types (leave empty = all)
            </label>
            <div className="flex flex-wrap gap-1.5">
              {ENTITY_TYPES.map((t) => (
                <button
                  key={t}
                  onClick={() => toggleEntityType(t)}
                  className={`px-2.5 py-1 text-xs rounded-full border transition-colors ${
                    filters.entityTypes.includes(t)
                      ? "bg-indigo-100 border-indigo-400 text-indigo-700 dark:bg-indigo-900/30 dark:border-indigo-500 dark:text-indigo-400"
                      : "bg-white border-zinc-300 text-zinc-600 dark:bg-zinc-800 dark:border-zinc-700 dark:text-zinc-400"
                  }`}
                >
                  {t}
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className="flex items-center justify-end gap-2 px-5 py-3 border-t border-zinc-200 dark:border-zinc-800 bg-zinc-50 dark:bg-zinc-950">
          <button
            onClick={onClose}
            className="px-3 py-1.5 text-xs font-medium text-zinc-600 hover:text-zinc-800 dark:text-zinc-400"
          >
            Cancel
          </button>
          <button
            onClick={onApply}
            className="px-4 py-1.5 text-xs font-medium bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition-colors"
          >
            Export with Filters
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── Scheduled Exports Panel ───────────────────────────────────────────────────
interface StoredExport {
  filename: string;
  size_bytes: number;
  created_at: string;
}

function ScheduledExports({
  token,
  apiUrl,
}: {
  token: string;
  apiUrl: string;
}) {
  const [exports, setExports] = useState<StoredExport[]>([]);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [triggering, setTriggering] = useState(false);

  const fetchExports = async () => {
    setLoading(true);
    try {
      const base = apiUrl.replace(/\/+$/, "");
      const res = await fetch(`${base}/export/scheduled`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        const data = await res.json();
        setExports(data.exports || []);
      }
    } catch {
      // silent
    } finally {
      setLoading(false);
    }
  };

  const triggerExport = async () => {
    setTriggering(true);
    try {
      const base = apiUrl.replace(/\/+$/, "");
      const res = await fetch(`${base}/export/trigger`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        await fetchExports();
      }
    } catch {
      // silent
    } finally {
      setTriggering(false);
    }
  };

  const downloadExport = (filename: string) => {
    const base = apiUrl.replace(/\/+$/, "");
    const a = document.createElement("a");
    a.href = `${base}/export/scheduled/${filename}`;
    a.download = filename;
    // Trigger download with auth
    fetch(`${base}/export/scheduled/${filename}`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((r) => r.blob())
      .then((blob) => {
        a.href = URL.createObjectURL(blob);
        a.click();
        URL.revokeObjectURL(a.href);
      });
  };

  const formatBytes = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  return (
    <div className="border-t border-zinc-200 dark:border-zinc-800">
      <button
        onClick={() => {
          setExpanded(!expanded);
          if (!expanded) fetchExports();
        }}
        className="w-full flex items-center gap-2 px-3 py-2 text-xs font-medium text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300"
      >
        <Calendar className="h-3 w-3" />
        Scheduled Exports
        {expanded ? <ChevronUp className="h-3 w-3 ml-auto" /> : <ChevronDown className="h-3 w-3 ml-auto" />}
      </button>

      {expanded && (
        <div className="px-3 pb-3 space-y-2">
          <button
            onClick={triggerExport}
            disabled={triggering}
            className="w-full flex items-center justify-center gap-1.5 px-2 py-1.5 text-xs font-medium bg-indigo-50 text-indigo-700 rounded-lg hover:bg-indigo-100 dark:bg-indigo-900/20 dark:text-indigo-400 disabled:opacity-50 transition-colors"
          >
            {triggering ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <Download className="h-3 w-3" />
            )}
            {triggering ? "Exporting..." : "Export Now"}
          </button>

          {loading ? (
            <div className="flex items-center justify-center py-2">
              <Loader2 className="h-3 w-3 animate-spin text-zinc-400" />
            </div>
          ) : exports.length === 0 ? (
            <p className="text-xs text-zinc-400 text-center py-2">No exports yet</p>
          ) : (
            <div className="space-y-1 max-h-40 overflow-y-auto">
              {exports.map((exp) => (
                <button
                  key={exp.filename}
                  onClick={() => downloadExport(exp.filename)}
                  className="w-full flex items-center gap-2 px-2 py-1.5 text-xs rounded-lg hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors text-left"
                >
                  <FileArchive className="h-3 w-3 text-zinc-400 shrink-0" />
                  <span className="truncate flex-1 text-zinc-600 dark:text-zinc-400">
                    {exp.filename.replace("wiki-kg-export-", "").replace(".zip", "")}
                  </span>
                  <span className="text-zinc-400 text-[10px] shrink-0">
                    {formatBytes(exp.size_bytes)}
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Main Export Panel ─────────────────────────────────────────────────────────
export function ExportPanel() {
  const { token, apiUrl } = useAuth();
  const [showFilters, setShowFilters] = useState(false);
  const [progress, setProgress] = useState(0);
  const [status, setStatus] = useState<"idle" | "preparing" | "downloading" | "done" | "error">("idle");
  const [errorMsg, setErrorMsg] = useState("");
  const [filters, setFilters] = useState<ExportFilters>({
    prefix: "",
    pageType: "",
    entityTypes: [],
  });
  const abortRef = useRef<AbortController | null>(null);

  const doExport = async (f: ExportFilters) => {
    setStatus("preparing");
    setProgress(0);
    setErrorMsg("");

    const base = apiUrl.replace(/\/+$/, "");
    const params = new URLSearchParams();
    if (f.prefix) params.set("prefix", f.prefix);
    if (f.pageType) params.set("page_type", f.pageType);
    if (f.entityTypes.length > 0) params.set("entity_types", f.entityTypes.join(","));

    const url = `${base}/export/zip${params.toString() ? "?" + params.toString() : ""}`;

    try {
      abortRef.current?.abort();
      abortRef.current = new AbortController();

      setStatus("preparing");
      setProgress(5);

      const res = await fetch(url, {
        headers: { Authorization: `Bearer ${token}` },
        signal: abortRef.current.signal,
      });

      if (!res.ok) throw new Error(`Export failed (${res.status})`);

      const contentLength = parseInt(res.headers.get("Content-Length") || "0", 10);
      const reader = res.body?.getReader();
      if (!reader) throw new Error("No response body");

      setStatus("downloading");
      const chunks: Uint8Array[] = [];
      let received = 0;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        chunks.push(value);
        received += value.length;
        if (contentLength > 0) {
          setProgress(5 + (received / contentLength) * 90);
        } else {
          // Unknown size — just show indeterminate progress
          setProgress((prev) => Math.min(prev + 2, 95));
        }
      }

      setProgress(95);

      // Create blob and trigger download
      const blob = new Blob(chunks as BlobPart[], { type: "application/zip" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `wiki-kg-export-${new Date().toISOString().slice(0, 10)}.zip`;
      a.click();
      URL.revokeObjectURL(a.href);

      setProgress(100);
      setStatus("done");
      setTimeout(() => {
        setStatus("idle");
        setProgress(0);
      }, 2000);
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") return;
      setStatus("error");
      setErrorMsg(e instanceof Error ? e.message : "Export failed");
      setTimeout(() => {
        setStatus("idle");
        setProgress(0);
      }, 3000);
    }
  };

  const hasFilters = filters.prefix || filters.pageType || filters.entityTypes.length > 0;

  // Status label
  const statusLabel =
    status === "preparing"
      ? "Preparing export..."
      : status === "downloading"
        ? "Downloading..."
        : status === "done"
          ? "Export complete!"
          : status === "error"
            ? errorMsg
            : hasFilters
              ? "Export (filtered)"
              : "Export all";

  return (
    <>
      <div className="px-2 py-2 border-t border-zinc-200 dark:border-zinc-800 space-y-1">
        {/* Progress bar (shown during export) */}
        {(status === "preparing" || status === "downloading") && (
          <div className="px-1 pb-1">
            <ProgressBar
              progress={progress}
              label={status === "preparing" ? "Generating ZIP..." : "Downloading..."}
            />
          </div>
        )}

        {/* Export button row */}
        <div className="flex items-center gap-1">
          <button
            onClick={() => {
              if (status !== "idle") return;
              doExport(filters);
            }}
            disabled={status !== "idle"}
            className={`flex-1 flex items-center gap-2 px-3 py-2 rounded-md text-sm font-medium transition-colors disabled:opacity-60 ${
              status === "done"
                ? "bg-green-50 text-green-700 dark:bg-green-900/20 dark:text-green-400"
                : status === "error"
                  ? "bg-red-50 text-red-700 dark:bg-red-900/20 dark:text-red-400"
                  : "text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-900"
            }`}
          >
            {status === "preparing" || status === "downloading" ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : status === "done" ? (
              <CheckCircle2 className="h-4 w-4" />
            ) : (
              <Download className="h-4 w-4" />
            )}
            <span className="truncate text-xs">{statusLabel}</span>
          </button>

          {/* Filter toggle */}
          <button
            onClick={() => setShowFilters(true)}
            className={`px-2 py-2 rounded-md transition-colors ${
              hasFilters
                ? "bg-indigo-50 text-indigo-600 dark:bg-indigo-900/20 dark:text-indigo-400"
                : "text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-900"
            }`}
            title="Export filters"
          >
            <Filter className="h-4 w-4" />
          </button>
        </div>

        {hasFilters && (
          <div className="px-1">
            <p className="text-[10px] text-zinc-400 truncate">
              Filtered:{filters.prefix && ` prefix=${filters.prefix}`}{filters.pageType && ` type=${filters.pageType}`}{filters.entityTypes.length > 0 && ` entities=${filters.entityTypes.join(",")}`}
            </p>
          </div>
        )}
      </div>

      {/* Scheduled Exports */}
      <ScheduledExports token={token} apiUrl={apiUrl} />

      {/* Filter Modal */}
      {showFilters && (
        <FilterModal
          filters={filters}
          setFilters={setFilters}
          onApply={() => {
            setShowFilters(false);
            doExport(filters);
          }}
          onClose={() => setShowFilters(false)}
        />
      )}
    </>
  );
}
