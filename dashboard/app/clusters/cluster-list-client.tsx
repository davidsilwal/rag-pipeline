"use client";

import { useState } from "react";
import Link from "next/link";
import {
  Download,
  FileText,
  Braces,
  Network,
  FileArchive,
  Sparkles,
  ChevronRight,
  ChevronDown,
  ListTree,
  Loader2,
} from "lucide-react";
import type { ClusterItem } from "./page";
import { getApiClient } from "@/lib/api";
import { useAuth } from "@/lib/auth";

interface ClusterSource {
  source_id: string;
  file_name: string;
  file_path: string | null;
  source_type: string;
  status: string;
  unit_count: number;
}

interface ClusterSourcesResult {
  sources: ClusterSource[];
  total_sources: number;
  total_units: number;
}

type ExportFormat = "markdown" | "json" | "graphml" | "zip" | "context-pack";

const EXPORT_FORMATS: { value: ExportFormat; label: string; icon: React.ComponentType<{ className?: string }>; description: string }[] = [
  { value: "markdown", label: "Markdown", icon: FileText, description: "Single .md file with all pages" },
  { value: "json", label: "JSON (RAG)", icon: Braces, description: "Structured JSON for RAG pipelines" },
  { value: "graphml", label: "GraphML", icon: Network, description: "Graph for visualization" },
  { value: "zip", label: "ZIP bundle", icon: FileArchive, description: "All formats in one archive" },
  { value: "context-pack", label: "Context pack", icon: Sparkles, description: "LLM-ready context window" },
];

export function ClusterListClient({
  initialClusters,
  initialError,
}: {
  initialClusters: ClusterItem[];
  initialError: string | null;
}) {
  const { apiUrl, token } = useAuth();
  const [query, setQuery] = useState("");
  const [exporting, setExporting] = useState<string | null>(null);
  const [exportFormat, setExportFormat] = useState<ExportFormat>("markdown");
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [sourcesMap, setSourcesMap] = useState<Record<string, ClusterSourcesResult>>({});
  const [loadingSources, setLoadingSources] = useState<string | null>(null);

  async function toggleSources(clusterId: string) {
    const willExpand = expanded !== clusterId;
    setExpanded(willExpand ? clusterId : null);
    if (!willExpand || sourcesMap[clusterId]) return;
    setLoadingSources(clusterId);
    setError(null);
    try {
      const api = getApiClient(apiUrl, token);
      const data = await api.exportClusterSources(clusterId);
      setSourcesMap((m) => ({
        ...m,
        [clusterId]: {
          sources: data.sources as ClusterSource[],
          total_sources: data.total_sources,
          total_units: data.total_units,
        },
      }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load sources");
    } finally {
      setLoadingSources(null);
    }
  }

  const filtered = initialClusters.filter((c) => {
    if (!query.trim()) return true;
    const q = query.toLowerCase();
    return (
      c.topic_name.toLowerCase().includes(q) ||
      c.keywords?.some((k) => k.toLowerCase().includes(q))
    );
  });

  async function handleExport(clusterId: string, topicName: string) {
    setExporting(clusterId);
    setError(null);
    try {
      const api = getApiClient(apiUrl, token);
      let content: string | Record<string, unknown>;
      let filename: string;
      let mime: string;

      switch (exportFormat) {
        case "markdown":
          content = await api.exportClusterMarkdown(clusterId);
          filename = `${slug(topicName)}.md`;
          mime = "text/markdown;charset=utf-8";
          break;
        case "json":
          content = await api.exportClusterJson(clusterId);
          filename = `${slug(topicName)}.json`;
          mime = "application/json;charset=utf-8";
          break;
        case "graphml":
          content = await api.exportClusterGraphml(clusterId);
          filename = `${slug(topicName)}.graphml`;
          mime = "application/xml;charset=utf-8";
          break;
        case "zip":
          content = await api.exportClusterZip(clusterId);
          filename = `${slug(topicName)}.zip`;
          mime = "application/zip";
          break;
        case "context-pack":
          content = await api.exportClusterContextPack(clusterId);
          filename = `${slug(topicName)}-context.md`;
          mime = "text/markdown;charset=utf-8";
          break;
      }

      const blob =
        typeof content === "string"
          ? new Blob([content], { type: mime })
          : new Blob([JSON.stringify(content, null, 2)], { type: mime });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Export failed");
    } finally {
      setExporting(null);
    }
  }

  return (
    <div className="space-y-6">
      <header className="space-y-2">
        <h1 className="text-2xl font-bold">Topic Clusters</h1>
        <p className="text-sm text-zinc-500">
          {initialClusters.length} cluster{initialClusters.length === 1 ? "" : "s"} ·
          Each cluster groups related wiki pages, entities, and source material.
        </p>
      </header>

      {error && (
        <div className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-300">
          {error}
        </div>
      )}

      {initialError && (
        <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-800 dark:bg-amber-900/30 dark:text-amber-300">
          {initialError}
        </div>
      )}

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <input
          type="search"
          placeholder="Filter clusters…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="flex-1 rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-2 text-sm"
        />
        <div className="flex items-center gap-1 rounded-md border border-zinc-300 dark:border-zinc-700 p-1 bg-zinc-50 dark:bg-zinc-900">
          {EXPORT_FORMATS.map((f) => (
            <button
              key={f.value}
              type="button"
              onClick={() => setExportFormat(f.value)}
              title={f.description}
              className={`inline-flex items-center gap-1.5 rounded px-2 py-1 text-xs font-medium transition-colors ${
                exportFormat === f.value
                  ? "bg-indigo-100 text-indigo-700 dark:bg-indigo-900/40 dark:text-indigo-300"
                  : "text-zinc-600 hover:bg-zinc-200 dark:text-zinc-400 dark:hover:bg-zinc-800"
              }`}
            >
              <f.icon className="h-3 w-3" />
              {f.label}
            </button>
          ))}
        </div>
      </div>

      {filtered.length === 0 ? (
        <div className="rounded-lg border border-dashed border-zinc-300 dark:border-zinc-700 p-8 text-center text-sm text-zinc-500">
          {initialClusters.length === 0
            ? "No clusters yet. Run the pipeline to generate them."
            : "No clusters match your filter."}
        </div>
      ) : (
        <ul className="grid grid-cols-1 gap-3 lg:grid-cols-2">
          {filtered.map((c) => (
            <li
              key={c.cluster_id}
              className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-4"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <Link
                    href={`/wiki/graph?cluster=${c.cluster_id}`}
                    className="text-base font-semibold text-zinc-900 dark:text-zinc-100 hover:underline flex items-center gap-1"
                  >
                    {c.topic_name || `Cluster ${c.cluster_id.slice(0, 8)}`}
                    <ChevronRight className="h-3 w-3" />
                  </Link>
                  {c.keywords?.length > 0 && (
                    <div className="mt-1 flex flex-wrap gap-1">
                      {c.keywords.slice(0, 6).map((k) => (
                        <span
                          key={k}
                          className="inline-block rounded-full bg-zinc-100 dark:bg-zinc-800 px-2 py-0.5 text-xs text-zinc-600 dark:text-zinc-400"
                        >
                          {k}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
                <button
                  type="button"
                  onClick={() => handleExport(c.cluster_id, c.topic_name)}
                  disabled={exporting === c.cluster_id}
                  className="inline-flex items-center gap-1.5 rounded-md border border-zinc-300 dark:border-zinc-700 px-3 py-1.5 text-xs font-medium text-zinc-600 dark:text-zinc-300 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors disabled:opacity-50"
                >
                  <Download className="h-3 w-3" />
                  {exporting === c.cluster_id ? "Exporting…" : "Export"}
                </button>
              </div>
              <dl className="mt-3 grid grid-cols-4 gap-2 text-xs text-zinc-500">
                <div>
                  <dt className="font-medium text-zinc-700 dark:text-zinc-300">
                    {c.page_count}
                  </dt>
                  <dd>pages</dd>
                </div>
                <div>
                  <dt className="font-medium text-zinc-700 dark:text-zinc-300">
                    {c.entity_count}
                  </dt>
                  <dd>entities</dd>
                </div>
                <div>
                  <dt className="font-medium text-zinc-700 dark:text-zinc-300">
                    {c.relationship_count}
                  </dt>
                  <dd>relations</dd>
                </div>
                <div>
                  <dt className="font-medium text-zinc-700 dark:text-zinc-300">
                    {typeof c.consensus_score === "number"
                      ? c.consensus_score.toFixed(2)
                      : "—"}
                  </dt>
                  <dd>consensus</dd>
                </div>
              </dl>

              <button
                type="button"
                onClick={() => toggleSources(c.cluster_id)}
                className="mt-3 inline-flex items-center gap-1.5 text-xs font-medium text-indigo-600 dark:text-indigo-400 hover:underline"
              >
                {expanded === c.cluster_id ? (
                  <ChevronDown className="h-3 w-3" />
                ) : (
                  <ListTree className="h-3 w-3" />
                )}
                Source documents{" "}
                {sourcesMap[c.cluster_id]
                  ? `(${sourcesMap[c.cluster_id].total_sources})`
                  : ""}
              </button>

              {expanded === c.cluster_id && (
                <div className="mt-2 rounded-md border border-zinc-200 dark:border-zinc-800 bg-zinc-50 dark:bg-zinc-950 p-3">
                  {loadingSources === c.cluster_id ? (
                    <div className="flex items-center gap-2 text-sm text-zinc-500">
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      Loading sources…
                    </div>
                  ) : (
                    <SourceCatalog data={sourcesMap[c.cluster_id]} />
                  )}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function SourceCatalog({ data }: { data?: ClusterSourcesResult }) {
  if (!data) return null;
  if (data.sources.length === 0) {
    return (
      <p className="text-sm text-zinc-500">
        No source documents found for this cluster.
      </p>
    );
  }
  return (
    <div>
      <p className="mb-2 text-xs text-zinc-500">
        <span className="font-semibold text-zinc-700 dark:text-zinc-300">
          {data.total_sources}
        </span>{" "}
        source document{data.total_sources === 1 ? "" : "s"}{" "}
        ·{" "}
        <span className="font-semibold text-zinc-700 dark:text-zinc-300">
          {data.total_units}
        </span>{" "}
        unit{data.total_units === 1 ? "" : "s"} in this topic.
      </p>
      <ul className="space-y-1.5">
        {data.sources.map((s) => {
          const wikiHref =
            s.file_path && s.file_path.endsWith(".md")
              ? `/wiki/${s.file_path.split("/").map(encodeURIComponent).join("/")}`
              : null;
          const label = s.file_name || s.source_id;
          return (
            <li
              key={s.source_id}
              className="flex items-center gap-2 text-sm"
            >
              <FileText className="h-3.5 w-3.5 text-zinc-400 shrink-0" />
              {wikiHref ? (
                <Link
                  href={wikiHref}
                  className="min-w-0 flex-1 truncate text-indigo-600 dark:text-indigo-400 hover:underline"
                >
                  {label}
                </Link>
              ) : (
                <span className="min-w-0 flex-1 truncate text-zinc-700 dark:text-zinc-300">
                  {label}
                </span>
              )}
              <span className="shrink-0 rounded-full bg-zinc-200 dark:bg-zinc-800 px-1.5 py-0.5 text-xs text-zinc-600 dark:text-zinc-400">
                {s.unit_count} unit{s.unit_count === 1 ? "" : "s"}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function slug(s: string): string {
  return (
    s
      .toLowerCase()
      .trim()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 60) || "cluster"
  );
}
