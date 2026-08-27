"use client";

import { use } from "react";
import Link from "next/link";
import { ArrowLeft, FileText, Box, Clock } from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { StatusBadge } from "@/components/ui/badge";
import { useSource, useUnits, useApi } from "@/lib/hooks";
import { formatBytes, relativeTime, truncate } from "@/lib/utils";
import { CopyButton } from "@/components/ui/copy-button";
import { useState } from "react";

export default function SourceDetailPage({
  params,
}: {
  params: Promise<{ sourceId: string }>;
}) {
  const { sourceId } = use(params);
  const { data: source, isLoading } = useSource(sourceId);
  const { data: units } = useUnits(sourceId, 100);
  const api = useApi();
  const [text, setText] = useState<string | null>(null);
  const [textLoading, setTextLoading] = useState(false);

  const loadText = async () => {
    setTextLoading(true);
    try {
      const t = await api.getSourceText(sourceId);
      setText(t as string);
    } catch {
      setText("No extracted text available");
    } finally {
      setTextLoading(false);
    }
  };

  return (
    <AppShell>
      <div className="space-y-6">
        <Link
          href="/sources"
          className="inline-flex items-center gap-1 text-sm text-zinc-500 hover:text-zinc-700"
        >
          <ArrowLeft className="h-4 w-4" />
          Back to Sources
        </Link>

        {isLoading ? (
          <div className="animate-pulse space-y-4">
            <div className="h-8 bg-zinc-100 dark:bg-zinc-800 rounded w-1/3" />
            <div className="h-32 bg-zinc-100 dark:bg-zinc-800 rounded" />
          </div>
        ) : !source ? (
          <div className="text-zinc-400">Source not found</div>
        ) : (
          <>
            {/* Metadata card */}
            <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-950 p-6">
              <div className="flex items-start justify-between mb-4">
                <div>
                  <h2 className="text-xl font-bold">{source.file_name}</h2>
                  <p className="text-sm text-zinc-500 mt-1">
                    {source.mime_type}
                  </p>
                </div>
                <StatusBadge status={source.status} />
              </div>

              <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                <div>
                  <span className="text-zinc-400">Size</span>
                  <p className="font-medium">{formatBytes(source.size_bytes)}</p>
                </div>
                <div>
                  <span className="text-zinc-400">Source Type</span>
                  <p className="font-medium">{source.source_type}</p>
                </div>
                <div>
                  <span className="text-zinc-400">Created</span>
                  <p className="font-medium">
                    {relativeTime(source.created_at)}
                  </p>
                </div>
                <div>
                  <span className="text-zinc-400">Updated</span>
                  <p className="font-medium">
                    {relativeTime(source.updated_at)}
                  </p>
                </div>
              </div>

              <div className="mt-4 space-y-2 text-sm">
                <div className="flex items-center gap-2">
                  <span className="text-zinc-400 w-24 shrink-0">ID</span>
                  <code className="font-mono text-xs bg-zinc-100 dark:bg-zinc-800 px-2 py-0.5 rounded">
                    {source.source_id}
                  </code>
                  <CopyButton text={source.source_id} />
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-zinc-400 w-24 shrink-0">SHA-256</span>
                  <code className="font-mono text-xs bg-zinc-100 dark:bg-zinc-800 px-2 py-0.5 rounded truncate max-w-md">
                    {source.sha256_hash}
                  </code>
                  <CopyButton text={source.sha256_hash} />
                </div>
                {source.file_path && (
                  <div className="flex items-center gap-2">
                    <span className="text-zinc-400 w-24 shrink-0">Path</span>
                    <code className="font-mono text-xs">{source.file_path}</code>
                  </div>
                )}
                {source.source_url && (
                  <div className="flex items-center gap-2">
                    <span className="text-zinc-400 w-24 shrink-0">URL</span>
                    <a
                      href={source.source_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-indigo-600 hover:underline text-xs truncate max-w-md"
                    >
                      {source.source_url}
                    </a>
                  </div>
                )}
              </div>

              {Object.keys(source.source_metadata || {}).length > 0 && (
                <div className="mt-4">
                  <span className="text-zinc-400 text-sm">Metadata</span>
                  <pre className="mt-1 text-xs bg-zinc-50 dark:bg-zinc-900 p-3 rounded overflow-x-auto max-h-48">
                    {JSON.stringify(source.source_metadata, null, 2)}
                  </pre>
                </div>
              )}
            </div>

            {/* Tabs: Text / Units */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              {/* Extracted text */}
              <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-950 p-4">
                <div className="flex items-center justify-between mb-3">
                  <h3 className="flex items-center gap-2 text-sm font-semibold">
                    <FileText className="h-4 w-4" />
                    Extracted Text
                  </h3>
                  {!text && (
                    <button
                      onClick={loadText}
                      disabled={textLoading}
                      className="text-xs text-indigo-600 hover:text-indigo-800 disabled:opacity-50"
                    >
                      {textLoading ? "Loading..." : "Load text"}
                    </button>
                  )}
                </div>
                {text ? (
                  <pre className="text-xs font-mono whitespace-pre-wrap max-h-96 overflow-y-auto bg-zinc-50 dark:bg-zinc-900 p-3 rounded">
                    {text}
                  </pre>
                ) : (
                  <p className="text-sm text-zinc-400">
                    Click &quot;Load text&quot; to fetch extracted content
                  </p>
                )}
              </div>

              {/* Units */}
              <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-950 p-4">
                <h3 className="flex items-center gap-2 text-sm font-semibold mb-3">
                  <Box className="h-4 w-4" />
                  Units ({units?.length ?? 0})
                </h3>
                {!units ? (
                  <div className="space-y-2">
                    {[...Array(3)].map((_, i) => (
                      <div
                        key={i}
                        className="h-12 bg-zinc-100 dark:bg-zinc-800 rounded animate-pulse"
                      />
                    ))}
                  </div>
                ) : units.length === 0 ? (
                  <p className="text-sm text-zinc-400">No units yet</p>
                ) : (
                  <div className="space-y-2 max-h-96 overflow-y-auto">
                    {units.map((u) => (
                      <div
                        key={u.unit_id}
                        className="p-2 rounded bg-zinc-50 dark:bg-zinc-900 text-xs"
                      >
                        <div className="flex items-center justify-between mb-1">
                          <span className="font-medium">
                            #{u.unit_index} — {u.unit_type}
                          </span>
                          {u.page_number != null && (
                            <span className="text-zinc-400 flex items-center gap-1">
                              <Clock className="h-3 w-3" />
                              p.{u.page_number}
                            </span>
                          )}
                        </div>
                        {u.heading_path?.length > 0 && (
                          <div className="text-zinc-400 mb-1">
                            {u.heading_path.join(" → ")}
                          </div>
                        )}
                        <p className="text-zinc-600 dark:text-zinc-400 line-clamp-2">
                          {truncate(u.clean_text || u.raw_text, 200)}
                        </p>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </>
        )}
      </div>
    </AppShell>
  );
}
