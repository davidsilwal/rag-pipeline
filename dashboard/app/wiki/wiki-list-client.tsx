"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { BookOpen, Clock } from "lucide-react";
import { StatusBadge, Badge } from "@/components/ui/badge";
import { relativeTime, truncate } from "@/lib/utils";
import { useApi } from "@/lib/hooks";
import type { WikiPageItem } from "./page";

export function WikiListClient({
  initialPages,
  initialLimit,
}: {
  initialPages: WikiPageItem[];
  initialLimit: number;
}) {
  const api = useApi();
  const [pages, setPages] = useState<WikiPageItem[]>(initialPages);
  const [limit, setLimit] = useState(initialLimit);
  const [filterType, setFilterType] = useState("");
  const [filterStatus, setFilterStatus] = useState("");

  // Refresh from the API on the client when the user changes the limit.
  // (Filter status is purely client-side; no refetch needed.)
  const filtered = useMemo(() => {
    return (pages || []).filter((p) => {
      if (filterType && p.page_type !== filterType) return false;
      if (filterStatus && p.status !== filterStatus) return false;
      return true;
    });
  }, [pages, filterType, filterStatus]);

  const types = [...new Set((Array.isArray(pages) ? pages : []).map((p) => p.page_type))];
  const statuses = [...new Set((Array.isArray(pages) ? pages : []).map((p) => p.status))];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="flex items-center gap-2 text-lg font-bold">
          <BookOpen className="h-5 w-5" />
          Wiki Pages
        </h2>
        <span className="text-sm text-zinc-500">
          {filtered.length} pages
        </span>
      </div>

      <div className="flex items-center gap-3">
        <select
          value={filterType}
          onChange={(e) => setFilterType(e.target.value)}
          className="rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-1.5 text-sm"
        >
          <option value="">All types</option>
          {types.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
        <select
          value={filterStatus}
          onChange={(e) => setFilterStatus(e.target.value)}
          className="rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-1.5 text-sm"
        >
          <option value="">All statuses</option>
          {statuses.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <select
          value={limit}
          onChange={async (e) => {
            const v = parseInt(e.target.value, 10);
            setLimit(v);
            // Refresh from the API; the useApi client sets Authorization
            // from localStorage. The SSR pre-fetch uses DASHBOARD_API_TOKEN,
            // so a fresh user with empty localStorage still sees the
            // initial render but the dropdown refresh needs auth.
            try {
              const res = await api.listWikiPages(v);
              const arr = Array.isArray(res) ? res : [];
              setPages(arr as unknown as WikiPageItem[]);
            } catch {
              /* ignore — keep the SSR-loaded list */
            }
          }}
          className="rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-1.5 text-sm"
        >
          <option value={25}>25</option>
          <option value={50}>50</option>
          <option value={100}>100</option>
        </select>
      </div>

      {filtered.length === 0 ? (
        <div className="text-center py-12 text-zinc-400">
          <BookOpen className="h-12 w-12 mx-auto mb-3 opacity-50" />
          <p>No wiki pages found</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {filtered.map((page) => (
            <Link
              key={page.page_id}
              href={`/wiki/${page.page_id}`}
              className="group rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-950 p-4 hover:border-indigo-300 dark:hover:border-indigo-700 transition-colors"
            >
              <div className="flex items-start justify-between mb-2">
                <h3 className="font-semibold text-sm group-hover:text-indigo-600 line-clamp-2">
                  {page.title}
                </h3>
                <StatusBadge status={page.status} />
              </div>
              {page.domain && (
                <Badge variant="info" className="mb-2">
                  {page.domain}
                </Badge>
              )}
              <p className="text-xs text-zinc-500 mb-2">
                <Badge>{page.page_type}</Badge>
              </p>
              {page.markdown_preview && (
                <p className="text-xs text-zinc-500 line-clamp-3 mt-2">
                  {truncate(page.markdown_preview, 150)}
                </p>
              )}
              <div className="flex items-center gap-1 mt-3 text-xs text-zinc-400">
                <Clock className="h-3 w-3" />
                {relativeTime(page.updated_at)}
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
