"use client";

import { useCallback, useMemo, useRef, useState } from "react";
import Link from "next/link";
import {
  BookOpen,
  Clock,
  Download,
  FolderTree,
  Loader2,
  Search as SearchIcon,
  X,
} from "lucide-react";
import { StatusBadge, Badge } from "@/components/ui/badge";
import { RelativeTime } from "@/components/ui/relative-time";
import { truncate } from "@/lib/utils";
import {
  matchesGroup,
  parseWikiGroupParam,
  ROOT_MARK,
  wikiGroupKey,
  wikiGroupLabel,
  wikiPathParts,
  type WikiGroup,
} from "@/lib/wiki-groups";
import type { WikiPageItem } from "./page";

type SortKey = "updated_desc" | "updated_asc" | "title_asc" | "title_desc";

const INITIAL_VISIBLE = 100;
const VISIBLE_STEP = 100;

interface ProjectStats {
  name: string;
  count: number;
  subfolders: number;
  lastUpdated: string | null;
  /** Pages updated in each of the last WEEKS buckets (oldest → newest). */
  weekly: number[];
}

const WEEKS = 12;
const WEEK_MS = 7 * 24 * 60 * 60 * 1000;

interface CategoryStats {
  category: string;
  count: number;
  lastUpdated: string | null;
}

function later(a: string | null | undefined, b: string | null | undefined) {
  if (!a) return b ?? null;
  if (!b) return a;
  return new Date(a).getTime() > new Date(b).getTime() ? a : b;
}

/**
 * Tiny bar sparkline of pages updated per week over the last WEEKS weeks
 * (oldest → newest). Zero weeks render as a faint baseline so the shape of
 * activity stays readable.
 */
function ActivitySparkline({ weekly }: { weekly: number[] }) {
  const max = Math.max(...weekly, 1);
  const W = 100;
  const H = 20;
  const bw = W / weekly.length;
  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="h-5 w-full"
      role="img"
      aria-label="Pages updated per week, oldest to newest"
    >
      <title>Weekly updates (oldest → newest): {weekly.join(", ")}</title>
      {weekly.map((v, i) => {
        const h = v === 0 ? 1 : Math.max((v / max) * H, 2);
        return (
          <rect
            key={i}
            x={i * bw + 1}
            y={H - h}
            width={bw - 2}
            height={h}
            rx={1}
            className={
              v > 0
                ? "fill-indigo-400 dark:fill-indigo-500"
                : "fill-zinc-200 dark:fill-zinc-800"
            }
          />
        );
      })}
    </svg>
  );
}

export function WikiListClient({
  initialPages,
  initialError,
}: {
  initialPages: WikiPageItem[];
  initialError: string | null;
}) {
  const [query, setQuery] = useState("");
  const [filterType, setFilterType] = useState("");
  const [filterStatus, setFilterStatus] = useState("");
  const [filterDomain, setFilterDomain] = useState("");
  const [sort, setSort] = useState<SortKey>("updated_desc");
  const [visibleCount, setVisibleCount] = useState(INITIAL_VISIBLE);
  const [exporting, setExporting] = useState(false);
  // Restore a shared ?group=projects/foo link on load. AppShell defers its
  // children until after hydration, so this component only ever mounts on
  // the client and window is always available here.
  const [group, setGroup] = useState<WikiGroup | null>(() => {
    if (typeof window === "undefined") return null;
    const raw = new URLSearchParams(window.location.search).get("group");
    return raw ? parseWikiGroupParam(raw) : null;
  });
  const [projectQuery, setProjectQuery] = useState("");
  // Client-only mount (AppShell gates children) — captured once so the
  // activity sparkline has a stable "now" without calling Date.now() during
  // render.
  const [now] = useState(() => Date.now());
  const listRef = useRef<HTMLDivElement | null>(null);

  const all = useMemo(
    () => (Array.isArray(initialPages) ? initialPages : []),
    [initialPages],
  );

  const selectGroup = useCallback((g: WikiGroup | null) => {
    setGroup(g);
    setVisibleCount(INITIAL_VISIBLE);
    setProjectQuery("");
    const url = new URL(window.location.href);
    if (g) url.searchParams.set("group", wikiGroupKey(g));
    else url.searchParams.delete("group");
    window.history.replaceState(null, "", url.toString());
    if (g) {
      requestAnimationFrame(() => {
        listRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    }
  }, []);

  // Search, filter, sort, and group all run over the entire loaded wiki,
  // client-side.
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const list = all.filter((p) => {
      if (filterType && p.page_type !== filterType) return false;
      if (filterStatus && p.status !== filterStatus) return false;
      if (filterDomain && p.domain !== filterDomain) return false;
      if (group && !matchesGroup(wikiPathParts(p.file_path ?? ""), group)) {
        return false;
      }
      if (q) {
        const haystack = [
          p.title,
          p.file_path,
          p.domain ?? "",
          p.markdown_preview ?? "",
        ]
          .join(" ")
          .toLowerCase();
        if (!haystack.includes(q)) return false;
      }
      return true;
    });
    return [...list].sort((a, b) => {
      switch (sort) {
        case "title_asc":
          return (a.title || "").localeCompare(b.title || "");
        case "title_desc":
          return (b.title || "").localeCompare(a.title || "");
        case "updated_asc":
          return (
            new Date(a.updated_at ?? 0).getTime() -
            new Date(b.updated_at ?? 0).getTime()
          );
        default:
          return (
            new Date(b.updated_at ?? 0).getTime() -
            new Date(a.updated_at ?? 0).getTime()
          );
      }
    });
  }, [all, filterType, filterStatus, filterDomain, group, query, sort]);

  // Projects (folders under projects/) and non-project knowledge areas, with
  // the stats shown on the browse cards.
  const groups = useMemo(() => {
    const projMap = new Map<string, ProjectStats>();
    const catMap = new Map<string, CategoryStats>();
    for (const p of all) {
      const parts = wikiPathParts(p.file_path ?? "");
      if (parts.length === 0) continue;
      if (parts[0] === "projects" && parts.length >= 2) {
        const name = parts[1];
        const cur = projMap.get(name) ?? {
          name,
          count: 0,
          subfolders: 0,
          lastUpdated: null,
          weekly: new Array<number>(WEEKS).fill(0),
        };
        cur.count += 1;
        cur.lastUpdated = later(cur.lastUpdated, p.updated_at);
        // A page one level below project/<name>/file.md sits in a subfolder.
        if (parts.length >= 4) {
          cur.subfolders += 1;
        }
        // Activity bucket: which of the last WEEKS weeks this page was last
        // updated in (0 = this week … WEEKS-1 = oldest shown).
        if (p.updated_at) {
          const age = now - new Date(p.updated_at).getTime();
          if (age >= 0 && age < WEEK_MS * WEEKS) {
            const i = Math.floor(age / WEEK_MS);
            cur.weekly[WEEKS - 1 - i] += 1;
          }
        }
        projMap.set(name, cur);
      } else {
        const cat = parts[0];
        const cur = catMap.get(cat) ?? {
          category: cat,
          count: 0,
          lastUpdated: null,
        };
        cur.count += 1;
        cur.lastUpdated = later(cur.lastUpdated, p.updated_at);
        catMap.set(cat, cur);
      }
    }
    const projects = [...projMap.values()].sort((a, b) =>
      a.name.localeCompare(b.name),
    );
    const categories = [...catMap.values()].sort((a, b) => b.count - a.count);
    return { projects, categories };
    // `now` is captured once on mount and never changes — it is listed only
    // to satisfy exhaustive-deps.
  }, [all, now]);

  const visibleProjects = useMemo(() => {
    const pq = projectQuery.trim().toLowerCase();
    if (!pq) return groups.projects;
    return groups.projects.filter((g) => g.name.toLowerCase().includes(pq));
  }, [groups.projects, projectQuery]);

  // Next-level buckets inside the selected group: the folder's direct files
  // ("Root") plus each subfolder.
  const drilldown = useMemo(() => {
    if (!group) return null;
    if (group[group.length - 1] === ROOT_MARK) return null;
    const depth = group.length;
    const subs = new Map<
      string,
      { group: WikiGroup; count: number; lastUpdated: string | null }
    >();
    let rootCount = 0;
    let rootUpdated: string | null = null;
    for (const p of all) {
      const parts = wikiPathParts(p.file_path ?? "");
      if (!matchesGroup(parts, group)) continue;
      if (parts.length === depth + 1) {
        rootCount += 1;
        rootUpdated = later(rootUpdated, p.updated_at);
      } else if (parts.length >= depth + 2) {
        const seg = parts[depth];
        const cur = subs.get(seg) ?? {
          group: [...group, seg],
          count: 0,
          lastUpdated: null,
        };
        cur.count += 1;
        cur.lastUpdated = later(cur.lastUpdated, p.updated_at);
        subs.set(seg, cur);
      }
    }
    const list = [...subs.values()].sort((a, b) => b.count - a.count);
    return { rootCount, rootUpdated, subs: list };
  }, [all, group]);

  const types = [...new Set(all.map((p) => p.page_type))];
  const statuses = [...new Set(all.map((p) => p.status))];
  const domains = [
    ...new Set(all.map((p) => p.domain).filter((d): d is string => !!d)),
  ];

  const shown = filtered.slice(0, visibleCount);
  const hasMore = filtered.length > visibleCount;

  const selectClass =
    "rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-1.5 text-sm";

  const groupName = group
    ? group.length === 2 && group[0] === "projects"
      ? group[1]
      : wikiGroupLabel(group)
    : "";
  const countLabel = group
    ? `${filtered.length} doc${filtered.length === 1 ? "" : "s"} in ${groupName}`
    : `${filtered.length} page${filtered.length === 1 ? "" : "s"}`;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="flex items-center gap-2 text-lg font-bold">
          <BookOpen className="h-5 w-5" />
          Wiki Pages
        </h2>
        <span className="text-sm text-zinc-500">{countLabel}</span>
      </div>

      {/* Project browser — shown until a project/area is picked */}
      {!group && (groups.projects.length > 0 || groups.categories.length > 0) ? (
        <section className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white p-4 dark:bg-zinc-950">
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-zinc-700 dark:text-zinc-300">
            <FolderTree className="h-4 w-4 text-indigo-500" />
            Browse by project
          </h3>

          {groups.projects.length > 0 ? (
            <>
              <div className="relative mb-3 max-w-xs">
                <SearchIcon className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-400" />
                <input
                  type="search"
                  value={projectQuery}
                  onChange={(e) => setProjectQuery(e.target.value)}
                  placeholder="Filter projects…"
                  className="w-full rounded-md border border-zinc-300 bg-white py-1.5 pl-9 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 dark:border-zinc-700 dark:bg-zinc-900"
                />
              </div>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6">
                {visibleProjects.map((g) => (
                  <button
                    key={g.name}
                    type="button"
                    onClick={() => selectGroup(["projects", g.name])}
                    title={`Show all docs of ${g.name}`}
                    className="flex flex-col items-start gap-1 rounded-md border border-zinc-200 px-3 py-2 text-left transition-colors hover:border-indigo-300 hover:bg-indigo-50/50 dark:border-zinc-800 dark:hover:border-indigo-700 dark:hover:bg-indigo-950/40"
                  >
                    <span className="w-full truncate text-[13px] font-medium text-zinc-800 dark:text-zinc-200">
                      {g.name}
                    </span>
                    <span className="text-[11px] text-zinc-400">
                      {g.count} doc{g.count === 1 ? "" : "s"}
                      {g.subfolders > 0
                        ? ` · ${g.subfolders} in subfolder${g.subfolders === 1 ? "" : "s"}`
                        : ""}
                    </span>
                    <ActivitySparkline weekly={g.weekly} />
                    <span className="inline-flex items-center gap-1 text-[11px] text-zinc-400">
                      <Clock className="h-3 w-3" />
                      <RelativeTime iso={g.lastUpdated} />
                    </span>
                  </button>
                ))}
              </div>
              {visibleProjects.length === 0 ? (
                <p className="text-xs text-zinc-400">
                  No projects match &ldquo;{projectQuery}&rdquo;
                </p>
              ) : null}
            </>
          ) : null}

          {groups.categories.length > 0 ? (
            <>
              <p className="mb-2 mt-4 text-xs font-semibold uppercase tracking-wide text-zinc-400">
                Knowledge areas
              </p>
              <div className="flex flex-wrap gap-2">
                {groups.categories.map((c) => (
                  <button
                    key={c.category}
                    type="button"
                    onClick={() => selectGroup([c.category])}
                    className="inline-flex items-center gap-1.5 rounded-full border border-zinc-200 px-3 py-1 text-xs font-medium text-zinc-700 transition-colors hover:border-indigo-300 hover:bg-indigo-50/50 dark:border-zinc-800 dark:text-zinc-300 dark:hover:border-indigo-700 dark:hover:bg-indigo-950/40"
                  >
                    {c.category}
                    <span className="text-zinc-400">{c.count}</span>
                  </button>
                ))}
              </div>
            </>
          ) : null}
        </section>
      ) : null}

      {/* Active group breadcrumb */}
      {group ? (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-2 text-sm dark:border-indigo-900 dark:bg-indigo-950/40">
          <button
            type="button"
            onClick={() => selectGroup(null)}
            className="font-medium text-zinc-600 hover:text-indigo-600 dark:text-zinc-300"
          >
            All wiki
          </button>
          {group.map((seg, i) => (
            <span key={`${seg}-${i}`} className="inline-flex items-center gap-2">
              <span className="text-zinc-400">/</span>
              <span
                className={
                  i === group.length - 1
                    ? "font-semibold text-indigo-700 dark:text-indigo-300"
                    : "text-zinc-500"
                }
              >
                {seg === ROOT_MARK ? "Root" : seg}
              </span>
            </span>
          ))}
          <button
            type="button"
            onClick={() => selectGroup(null)}
            aria-label="Clear project filter"
            className="ml-auto inline-flex items-center gap-1 rounded-md border border-indigo-200 px-2 py-1 text-xs text-indigo-600 hover:bg-indigo-100 dark:border-indigo-800 dark:text-indigo-300 dark:hover:bg-indigo-900"
          >
            <X className="h-3 w-3" />
            Clear
          </button>
        </div>
      ) : null}

      {/* Drill-down: direct files vs subfolders inside the selected group */}
      {group && drilldown ? (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-medium uppercase tracking-wide text-zinc-400">
            In {groupName}:
          </span>
          <button
            type="button"
            onClick={() => selectGroup(group)}
            className="inline-flex items-center gap-1 rounded-full border border-indigo-300 bg-indigo-50 px-3 py-1 text-xs font-medium text-indigo-700 dark:border-indigo-700 dark:bg-indigo-950/60 dark:text-indigo-300"
          >
            All ({drilldown.rootCount + drilldown.subs.reduce((n, s) => n + s.count, 0)})
          </button>
          <button
            type="button"
            onClick={() => selectGroup([...group, ROOT_MARK])}
            className="inline-flex items-center gap-1 rounded-full border border-zinc-200 px-3 py-1 text-xs font-medium text-zinc-600 transition-colors hover:border-indigo-300 hover:bg-indigo-50/50 dark:border-zinc-800 dark:text-zinc-300 dark:hover:border-indigo-700 dark:hover:bg-indigo-950/40"
          >
            Root ({drilldown.rootCount})
          </button>
          {drilldown.subs.map((s) => (
            <button
              key={wikiGroupKey(s.group)}
              type="button"
              onClick={() => selectGroup(s.group)}
              className="inline-flex items-center gap-1 rounded-full border border-zinc-200 px-3 py-1 text-xs font-medium text-zinc-600 transition-colors hover:border-indigo-300 hover:bg-indigo-50/50 dark:border-zinc-800 dark:text-zinc-300 dark:hover:border-indigo-700 dark:hover:bg-indigo-950/40"
            >
              <FolderTree className="h-3 w-3 text-zinc-400" />
              {wikiGroupLabel(s.group)}
              <span className="text-zinc-400">{s.count}</span>
            </button>
          ))}
        </div>
      ) : null}

      {/* Export button */}
      <div className="flex items-center justify-between">
        <div />
        <button
          type="button"
          disabled={exporting}
          onClick={async () => {
            setExporting(true);
            try {
              const env = window.location.origin;
              const token =
                localStorage.getItem("wiki_api_token") ??
                localStorage.getItem("token") ??
                "";
              const headers: Record<string, string> = {};
              if (token) headers["Authorization"] = `Bearer ${token}`;
              // Pass active filters to the export endpoint so only matching
              // pages are included in the ZIP.
              const params = new URLSearchParams();
              if (group) params.set("prefix", wikiGroupKey(group));
              if (query.trim()) params.set("q", query.trim());
              if (filterType) params.set("page_type", filterType);
              if (filterStatus) params.set("status", filterStatus);
              if (filterDomain) params.set("domain", filterDomain);
              const qs = params.toString();
              const url = `${env.replace(/\/+$/, "")}/api/v1/wiki/export${qs ? `?${qs}` : ""}`;
              const res = await fetch(url, { headers });
              if (!res.ok)
                throw new Error(`Export failed (HTTP ${res.status})`);
              const blob = await res.blob();
              const blobUrl = URL.createObjectURL(blob);
              const a = document.createElement("a");
              a.href = blobUrl;
              a.download = "wiki-export.zip";
              a.click();
              URL.revokeObjectURL(blobUrl);
            } catch (e) {
              alert(
                e instanceof Error ? e.message : "Export failed",
              );
            } finally {
              setExporting(false);
            }
          }}
          className="inline-flex items-center gap-1.5 rounded-md border border-zinc-300 dark:border-zinc-700 px-3 py-1.5 text-xs font-medium text-zinc-600 dark:text-zinc-300 hover:bg-zinc-50 dark:hover:bg-zinc-800 transition-colors disabled:opacity-50"
        >
          {exporting ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Download className="h-3.5 w-3.5" />
          )}
          {exporting ? "Exporting…" : filtered.length > 0 && (query || group || filterType || filterStatus || filterDomain) ? `Export ${filtered.length} pages` : "Export all"}
        </button>
      </div>

      {/* Search + filters */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative min-w-48 flex-1">
          <SearchIcon className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-400" />
          <input
            type="search"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setVisibleCount(INITIAL_VISIBLE);
            }}
            placeholder={
              group ? `Search in ${groupName}…` : "Search titles, files, content…"
            }
            className="w-full rounded-md border border-zinc-300 bg-white py-1.5 pl-9 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 dark:border-zinc-700 dark:bg-zinc-900"
          />
        </div>
        <select
          value={filterType}
          onChange={(e) => {
            setFilterType(e.target.value);
            setVisibleCount(INITIAL_VISIBLE);
          }}
          className={selectClass}
          aria-label="Filter by type"
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
          onChange={(e) => {
            setFilterStatus(e.target.value);
            setVisibleCount(INITIAL_VISIBLE);
          }}
          className={selectClass}
          aria-label="Filter by status"
        >
          <option value="">All statuses</option>
          {statuses.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        {domains.length > 0 && (
          <select
            value={filterDomain}
            onChange={(e) => {
              setFilterDomain(e.target.value);
              setVisibleCount(INITIAL_VISIBLE);
            }}
            className={selectClass}
            aria-label="Filter by domain"
          >
            <option value="">All domains</option>
            {domains.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        )}
        <select
          value={sort}
          onChange={(e) => setSort(e.target.value as SortKey)}
          className={selectClass}
          aria-label="Sort pages"
        >
          <option value="updated_desc">Recently updated</option>
          <option value="updated_asc">Oldest first</option>
          <option value="title_asc">Title A–Z</option>
          <option value="title_desc">Title Z–A</option>
        </select>
      </div>

      {/* Load error */}
      {initialError && (
        <div className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-800 dark:bg-amber-900/20 dark:text-amber-200">
          {initialError}
        </div>
      )}

      <div ref={listRef}>
        {filtered.length === 0 ? (
          <div className="py-12 text-center text-zinc-400">
            <BookOpen className="mx-auto mb-3 h-12 w-12 opacity-50" />
            <p>
              {all.length === 0
                ? "No wiki pages found"
                : group
                  ? `No pages in ${groupName}`
                  : "No pages match your search or filters"}
            </p>
          </div>
        ) : (
          <>
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
              {shown.map((page) => (
                <Link
                  key={page.page_id}
                  href={`/wiki/${page.page_id}`}
                  className="group rounded-lg border border-zinc-200 bg-white p-4 transition-colors hover:border-indigo-300 dark:border-zinc-800 dark:bg-zinc-950 dark:hover:border-indigo-700"
                >
                  <div className="mb-2 flex items-start justify-between">
                    <h3 className="line-clamp-2 text-sm font-semibold group-hover:text-indigo-600">
                      {page.title}
                    </h3>
                    <StatusBadge status={page.status} />
                  </div>
                  {page.domain && (
                    <Badge variant="info" className="mb-2">
                      {page.domain}
                    </Badge>
                  )}
                  <p className="mb-2 text-xs text-zinc-500">
                    <Badge>{page.page_type}</Badge>
                  </p>
                  {group && group[0] === "projects" && group.length >= 2 ? (
                    <p
                      className="mb-2 truncate font-mono text-[11px] text-zinc-400"
                      title={page.file_path}
                    >
                      {wikiPathParts(page.file_path ?? "").slice(2).join("/")}
                    </p>
                  ) : null}
                  {page.markdown_preview && (
                    <p className="mt-2 line-clamp-3 text-xs text-zinc-500">
                      {truncate(page.markdown_preview, 150)}
                    </p>
                  )}
                  <div className="mt-3 flex items-center gap-1 text-xs text-zinc-400">
                    <Clock className="h-3 w-3" />
                    <RelativeTime iso={page.updated_at} />
                  </div>
                </Link>
              ))}
            </div>

            {hasMore ? (
              <div className="flex items-center justify-center gap-3 pt-2">
                <span className="text-xs text-zinc-400">
                  Showing {shown.length} of {filtered.length}
                </span>
                <button
                  type="button"
                  onClick={() => setVisibleCount((c) => c + VISIBLE_STEP)}
                  className="rounded-md border border-zinc-300 px-3 py-1.5 text-xs font-medium text-zinc-600 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
                >
                  Show more
                </button>
              </div>
            ) : null}
          </>
        )}
      </div>
    </div>
  );
}
