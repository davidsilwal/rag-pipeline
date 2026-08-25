"use client";

import { use, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft, Clock, FileText, Loader2 } from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { StatusBadge } from "@/components/ui/badge";
import { MarkdownRenderer } from "@/components/ui/markdown-renderer";
import { CopyButton } from "@/components/ui/copy-button";
import { relativeTime } from "@/lib/utils";

type PendingInfo = {
  status: "pending";
  sourceId: string;
  sourceStatus: string;
  message: string;
};

type ResolvedPage = {
  status: "ok";
  page_id: string;
  file_path: string;
  title: string;
  page_type: string;
  domain: string;
  status_label: string;
  frontmatter: Record<string, unknown>;
  markdown_body: string;
  source_unit_ids: string[];
  last_verified_at: string | null;
  created_at: string | null;
  updated_at: string | null;
};

type PageState = ResolvedPage | PendingInfo | null;

function isUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
    value,
  );
}

function getApiBase(): string {
  // Prefer the explicit user-set URL (localStorage), then the build-time
  // NEXT_PUBLIC_API_URL, then a relative path. The previous relative default
  // would silently 404 against the dashboard host (e.g. 100.72.153.12:3000)
  // because no API runs there — only the control-api on :8000 does.
  if (typeof window !== "undefined") {
    const ls = window.localStorage.getItem("wiki_api_url");
    if (ls) return ls.replace(/\/+$/, "");
  }
  const env = process.env.NEXT_PUBLIC_API_URL;
  if (env) return env.replace(/\/+$/, "");
  return "";
}

export default function WikiSlugPage({
  params,
}: {
  params: Promise<{ slug: string[] }>;
}) {
  const { slug: slugParts } = use(params);
  const router = useRouter();
  const slug = (slugParts ?? []).join("/");
  const isUuidSlug = isUuid(slug);

  const [page, setPage] = useState<PageState>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!slug) {
      setLoading(false);
      setError("Missing page identifier");
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);

    async function resolve() {
      try {
        const token = localStorage.getItem("wiki_api_token") ?? "";
        const headers = { Authorization: `Bearer ${token}` };
        const apiBase = getApiBase();
        if (isUuidSlug) {
          const r = await fetch(
            `${apiBase}/wiki/pages/${slug}`,
            { headers },
          );
          if (cancelled) return;
          if (r.ok) {
            const data = await r.json();
            setPage({ status: "ok", ...data, status_label: data.status });
          } else {
            setError("Page not found");
            setPage(null);
          }
        } else {
          const r = await fetch(
            `${apiBase}/wiki/by-file/${encodeURI(slug)}`,
            { headers },
          );
          if (cancelled) return;
          if (r.ok) {
            const data = await r.json();
            setPage({ status: "ok", ...data, status_label: data.status });
            router.replace(`/wiki/${data.page_id}`);
            return;
          }
          if (r.status === 404) {
            const body = await r.json().catch(() => ({}));
            const detail = (body && (body.detail ?? body)) || {};
            if (detail && detail.source_id) {
              setPage({
                status: "pending",
                sourceId: detail.source_id,
                sourceStatus: detail.source_status ?? "extracted",
                message:
                  detail.message ??
                  "Wiki page for this source has not been compiled yet.",
              });
            } else {
              setError("Page not found");
              setPage(null);
            }
          } else {
            setError("Page not found");
            setPage(null);
          }
        }
      } catch (err) {
        if (!cancelled) setError(String(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    resolve();
    return () => {
      cancelled = true;
    };
  }, [slug, isUuidSlug, router]);

  const headings = useMemo(() => {
    if (!page || page.status !== "ok" || !page.markdown_body) return [];
    const matches = page.markdown_body.match(/^(#{1,4})\s+(.+)$/gm) || [];
    return matches.map((m) => {
      const level = m.match(/^(#+)/)?.[1].length || 1;
      const text = m.replace(/^#+\s+/, "");
      return { level, text, id: text.toLowerCase().replace(/[^a-z0-9]+/g, "-") };
    });
  }, [page]);

  const data = page?.status === "ok" ? page : null;
  const pending = page?.status === "pending" ? page : null;

  return (
    <AppShell>
      <div className="space-y-4">
        <Link
          href="/wiki"
          className="inline-flex items-center gap-1 text-sm text-zinc-500 hover:text-zinc-700"
        >
          <ArrowLeft className="h-4 w-4" />
          Back to Wiki
        </Link>

        {loading ? (
          <div className="animate-pulse space-y-4">
            <div className="h-8 bg-zinc-100 dark:bg-zinc-800 rounded w-1/3" />
            <div className="h-32 bg-zinc-100 dark:bg-zinc-800 rounded" />
          </div>
        ) : pending ? (
          <div className="rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/30 p-6">
            <div className="flex items-center gap-2 mb-2">
              <Loader2 className="h-5 w-5 text-amber-600 animate-spin" />
              <h2 className="font-semibold text-amber-900 dark:text-amber-100">
                Wiki page not yet generated
              </h2>
            </div>
            <p className="text-sm text-amber-800 dark:text-amber-200 mb-3">
              {pending.message}
            </p>
            <div className="grid grid-cols-2 gap-3 text-xs">
              <div>
                <div className="text-amber-700 dark:text-amber-300">Source ID</div>
                <code className="text-amber-900 dark:text-amber-100">
                  {pending.sourceId}
                </code>
              </div>
              <div>
                <div className="text-amber-700 dark:text-amber-300">Source status</div>
                <code className="text-amber-900 dark:text-amber-100">
                  {pending.sourceStatus}
                </code>
              </div>
            </div>
            <p className="text-xs text-amber-700 dark:text-amber-300 mt-3">
              The compile task is queued and will produce the page shortly.
              Refresh this URL in a minute or two.
            </p>
          </div>
        ) : !data ? (
          <div className="text-zinc-400">{error ?? "Page not found"}</div>
        ) : (
          <div className="flex gap-6">
            <article className="flex-1 min-w-0 rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-950 p-6">
              <header className="mb-4 pb-4 border-b border-zinc-200 dark:border-zinc-800">
                <div className="flex items-start justify-between gap-3 mb-2">
                  <h1 className="text-2xl font-bold">{data.title}</h1>
                  <div className="flex items-center gap-2 shrink-0">
                    <StatusBadge status={data.status_label} />
                  </div>
                </div>
                <div className="flex items-center flex-wrap gap-2 text-sm text-zinc-500">
                  <span className="inline-flex items-center gap-1">
                    <FileText className="h-3 w-3" />
                    <code className="text-xs">{data.file_path}</code>
                  </span>
                  {data.domain && <span>• {data.domain}</span>}
                  <span>• {data.page_type}</span>
                </div>
                {data.frontmatter && Object.keys(data.frontmatter).length > 0 && (
                  <details className="mt-3">
                    <summary className="text-xs text-zinc-500 cursor-pointer">
                      Frontmatter
                    </summary>
                    <pre className="mt-2 text-xs bg-zinc-50 dark:bg-zinc-900 rounded p-2 overflow-x-auto">
                      {JSON.stringify(data.frontmatter, null, 2)}
                    </pre>
                  </details>
                )}
                <div className="mt-3 flex items-center gap-3 text-xs text-zinc-400">
                  <CopyButton text={data.page_id} label="Copy page_id" />
                  {data.created_at && (
                    <span className="inline-flex items-center gap-1">
                      <Clock className="h-3 w-3" />
                      Created {relativeTime(data.created_at)}
                    </span>
                  )}
                  {data.updated_at && (
                    <span>• Updated {relativeTime(data.updated_at)}</span>
                  )}
                </div>
              </header>
              <MarkdownRenderer content={data.markdown_body} />
            </article>
            {headings.length > 0 && (
              <aside className="hidden lg:block w-64 shrink-0">
                <div className="sticky top-4 rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-950 p-4">
                  <h3 className="text-xs font-semibold text-zinc-500 mb-2 uppercase tracking-wide">
                    On this page
                  </h3>
                  <ul className="space-y-1 text-sm">
                    {headings.map((h) => (
                      <li
                        key={h.id}
                        style={{ paddingLeft: `${(h.level - 1) * 12}px` }}
                      >
                        <a
                          href={`#${h.id}`}
                          className="text-zinc-600 dark:text-zinc-400 hover:text-indigo-600 dark:hover:text-indigo-400"
                        >
                          {h.text}
                        </a>
                      </li>
                    ))}
                  </ul>
                </div>
              </aside>
            )}
          </div>
        )}
      </div>
    </AppShell>
  );
}
