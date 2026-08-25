"use client";

import { use, useMemo } from "react";
import Link from "next/link";
import { ArrowLeft, Clock, GitCommit } from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { StatusBadge } from "@/components/ui/badge";
import { MarkdownRenderer } from "@/components/ui/markdown-renderer";
import { CopyButton } from "@/components/ui/copy-button";
import { useWikiPage } from "@/lib/hooks";
import { relativeTime, formatDate } from "@/lib/utils";

export default function WikiDetailPage({
  params,
}: {
  params: Promise<{ pageId: string }>;
}) {
  const { pageId } = use(params);
  const { data: page, isLoading } = useWikiPage(pageId);

  // Extract headings for TOC
  const headings = useMemo(() => {
    if (!page?.markdown_body) return [];
    const matches = page.markdown_body.match(/^(#{1,4})\s+(.+)$/gm) || [];
    return matches.map((m) => {
      const level = m.match(/^(#+)/)?.[1].length || 1;
      const text = m.replace(/^#+\s+/, "");
      return { level, text, id: text.toLowerCase().replace(/[^a-z0-9]+/g, "-") };
    });
  }, [page?.markdown_body]);

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

        {isLoading ? (
          <div className="animate-pulse space-y-4">
            <div className="h-10 bg-zinc-100 dark:bg-zinc-800 rounded w-1/2" />
            <div className="h-64 bg-zinc-100 dark:bg-zinc-800 rounded" />
          </div>
        ) : !page ? (
          <div className="text-zinc-400">Page not found</div>
        ) : (
          <div className="flex gap-6">
            {/* Main content */}
            <div className="flex-1 min-w-0">
              <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-950 p-6">
                <div className="flex items-start justify-between mb-4">
                  <div>
                    <h1 className="text-2xl font-bold">{page.title}</h1>
                    <div className="flex items-center gap-3 mt-2 text-sm text-zinc-500">
                      <StatusBadge status={page.status} />
                      <span>{page.page_type}</span>
                      {page.domain && <span>• {page.domain}</span>}
                    </div>
                  </div>
                </div>

                {/* Frontmatter */}
                {page.frontmatter &&
                  Object.keys(page.frontmatter).length > 0 && (
                    <details className="mb-4">
                      <summary className="text-xs text-zinc-400 cursor-pointer hover:text-zinc-600">
                        Frontmatter
                      </summary>
                      <pre className="mt-2 text-xs bg-zinc-50 dark:bg-zinc-900 p-3 rounded overflow-x-auto max-h-32">
                        {JSON.stringify(page.frontmatter, null, 2)}
                      </pre>
                    </details>
                  )}

                {/* Markdown body */}
                <MarkdownRenderer content={page.markdown_body || ""} />
              </div>

              {/* Footer metadata */}
              <div className="mt-4 flex flex-wrap items-center gap-4 text-xs text-zinc-400">
                {page.git_commit_sha && (
                  <span className="flex items-center gap-1">
                    <GitCommit className="h-3 w-3" />
                    {page.git_commit_sha.slice(0, 8)}
                  </span>
                )}
                <span className="flex items-center gap-1">
                  <Clock className="h-3 w-3" />
                  Updated {relativeTime(page.updated_at)}
                </span>
                {page.created_at && (
                  <span>Created {formatDate(page.created_at)}</span>
                )}
                <CopyButton text={page.page_id} />
              </div>
            </div>

            {/* TOC sidebar */}
            {headings.length > 0 && (
              <nav className="hidden xl:block w-56 shrink-0">
                <div className="sticky top-6 rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-950 p-4">
                  <h4 className="text-xs font-semibold text-zinc-400 uppercase mb-3">
                    Table of Contents
                  </h4>
                  <ul className="space-y-1">
                    {headings.map((h, i) => (
                      <li
                        key={i}
                        style={{ paddingLeft: `${(h.level - 1) * 12}px` }}
                      >
                        <a
                          href={`#${h.id}`}
                          className="text-xs text-zinc-500 hover:text-indigo-600 line-clamp-1"
                        >
                          {h.text}
                        </a>
                      </li>
                    ))}
                  </ul>
                </div>
              </nav>
            )}
          </div>
        )}
      </div>
    </AppShell>
  );
}
