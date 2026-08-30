"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  GitBranch,
  RefreshCw,
  Search as SearchIcon,
  Users,
  Network,
  BookOpen,
  Filter,
} from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { Badge } from "@/components/ui/badge";
import { KnowledgeGraphViz } from "@/components/wiki/knowledge-graph-viz";
import { EntityDetailDrawer } from "@/components/wiki/entity-detail-drawer";
import { useApi } from "@/lib/hooks";
import { truncate } from "@/lib/utils";
import type {
  GraphragEntity,
  GraphragRelationship,
  GraphragCommunity,
  GraphragStats,
  GraphragProgress,
} from "@/lib/types";

type Tab = "graph" | "topics" | "entities" | "relationships" | "communities";

const ENTITY_COLORS: Record<string, string> = {
  person: "bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300",
  org: "bg-purple-100 text-purple-700 dark:bg-purple-950 dark:text-purple-300",
  concept: "bg-indigo-100 text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300",
  location: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
  technology: "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300",
  event: "bg-rose-100 text-rose-700 dark:bg-rose-950 dark:text-rose-300",
};

function entityTypeColor(t: string) {
  return ENTITY_COLORS[t] ?? "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300";
}

const PAGE_SIZE = 50;

export default function KnowledgeGraphPage() {
  const api = useApi();
  const [tab, setTab] = useState<Tab>("graph");
  const [stats, setStats] = useState<GraphragStats | null>(null);
  const [progress, setProgress] = useState<GraphragProgress | null>(null);

  // Per-tab data (lazy loaded)
  const [entities, setEntities] = useState<GraphragEntity[]>([]);
  const [entityPage, setEntityPage] = useState(0);
  const [entityTotal, setEntityTotal] = useState(0);
  const [relationships, setRelationships] = useState<GraphragRelationship[]>([]);
  const [relPage, setRelPage] = useState(0);
  const [relTotal, setRelTotal] = useState(0);
  const [communities, setCommunities] = useState<GraphragCommunity[]>([]);
  const [commPage, setCommPage] = useState(0);
  const [commTotal, setCommTotal] = useState(0);

  // Topics (cluster correlation graph)
  const [clusterGraph, setClusterGraph] = useState<{
    nodes: {
      cluster_id: string;
      topic_name: string;
      top_keywords: string[];
      unit_count: number;
      consensus_score: number;
    }[];
    edges: { source: string; target: string; weight: number }[];
  } | null>(null);

  const [loading, setLoading] = useState(true); // initial stats load
  const [tabLoading, setTabLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [drawerEntity, setDrawerEntity] = useState<GraphragEntity | null>(null);

  // Track which tabs have been loaded to avoid re-fetching
  const loadedTabs = useRef(new Set<Tab>());

  // ── Load stats + progress only (fast) ──────────────────────────────────
  const fetchStatsAndProgress = useCallback(async () => {
    return Promise.allSettled([
      api.getGraphragStats(),
      api.getGraphragProgress(),
    ]);
  }, [api]);

  const loadStatsAndProgress = useCallback(async () => {
    setLoading(true);
    try {
      const [s, p] = await fetchStatsAndProgress();
      if (s.status === "fulfilled") setStats(s.value);
      if (p.status === "fulfilled") setProgress(p.value);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, [fetchStatsAndProgress]);

  useEffect(() => {
    let active = true;
    (async () => {
      await Promise.resolve();
      if (!active) return;
      setLoading(true);
      try {
        const [s, p] = await fetchStatsAndProgress();
        if (!active) return;
        if (s.status === "fulfilled") setStats(s.value);
        if (p.status === "fulfilled") setProgress(p.value);
      } catch {
        // ignore
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, [fetchStatsAndProgress]);

  // ── Per-tab loaders ────────────────────────────────────────────────────
  const loadEntities = useCallback(
    async (page = 0, searchQ?: string, typeF?: string) => {
      setTabLoading(true);
      try {
        const [e] = await Promise.allSettled([
          api.listGraphragEntities({
            limit: PAGE_SIZE,
            offset: page * PAGE_SIZE,
            search: searchQ || undefined,
            entity_type: typeF || undefined,
          }),
        ]);
        // listGraphragEntities currently returns GraphragEntity[] (no total)
        // so we estimate total from stats or the length
        if (e.status === "fulfilled") {
          const data = e.value as unknown as GraphragEntity[];
          setEntities(data);
          if (stats && !searchQ && !typeF) {
            setEntityTotal(stats.entities);
          } else {
            setEntityTotal(data.length === PAGE_SIZE ? (page + 2) * PAGE_SIZE : (page + 1) * PAGE_SIZE);
          }
        }
      } catch {
        setEntities([]);
      } finally {
        setTabLoading(false);
      }
    },
    [api, stats],
  );

  const loadRelationships = useCallback(
    async (page = 0) => {
      setTabLoading(true);
      try {
        const [r] = await Promise.allSettled([
          api.listGraphragRelationships({
            limit: PAGE_SIZE,
            offset: page * PAGE_SIZE,
          }),
        ]);
        if (r.status === "fulfilled") {
          const data = r.value as unknown as GraphragRelationship[];
          setRelationships(data);
          if (stats) {
            setRelTotal(stats.relationships);
          } else {
            setRelTotal(data.length === PAGE_SIZE ? (page + 2) * PAGE_SIZE : (page + 1) * PAGE_SIZE);
          }
        }
      } catch {
        setRelationships([]);
      } finally {
        setTabLoading(false);
      }
    },
    [api, stats],
  );

  const loadCommunities = useCallback(
    async (page = 0) => {
      setTabLoading(true);
      try {
        const [c] = await Promise.allSettled([
          api.listGraphragCommunities(PAGE_SIZE, page * PAGE_SIZE),
        ]);
        if (c.status === "fulfilled") {
          const data = c.value as unknown as GraphragCommunity[];
          setCommunities(data);
          if (stats) {
            setCommTotal(stats.communities);
          } else {
            setCommTotal(data.length === PAGE_SIZE ? (page + 2) * PAGE_SIZE : (page + 1) * PAGE_SIZE);
          }
        }
      } catch {
        setCommunities([]);
      } finally {
        setTabLoading(false);
      }
    },
    [api, stats],
  );

  const loadClusterGraph = useCallback(async () => {
    setTabLoading(true);
    try {
      const [g] = await Promise.allSettled([api.getClusterGraph()]);
      if (g.status === "fulfilled") setClusterGraph(g.value);
    } catch {
      setClusterGraph(null);
    } finally {
      setTabLoading(false);
    }
  }, [api]);

  // ── Load data when a tab is first selected ─────────────────────────────
  const switchTab = useCallback(
    (t: Tab) => {
      setTab(t);
      // Always re-fetch graph tab to pick up new data
      if (t === "graph") {
        // Graph needs all data — load stats + entities + rels + communities
        setTabLoading(true);
        Promise.allSettled([
          api.listGraphragEntities({ limit: 200 }),
          api.listGraphragRelationships({ limit: 200 }),
          api.listGraphragCommunities(100),
        ]).then(([e, r, c]) => {
          if (e.status === "fulfilled") setEntities(e.value as unknown as GraphragEntity[]);
          if (r.status === "fulfilled") setRelationships(r.value as unknown as GraphragRelationship[]);
          if (c.status === "fulfilled") setCommunities(c.value as unknown as GraphragCommunity[]);
          setTabLoading(false);
        });
      } else if (!loadedTabs.current.has(t)) {
        loadedTabs.current.add(t);
        if (t === "topics") loadClusterGraph();
        if (t === "entities") loadEntities(0, search, typeFilter);
        if (t === "relationships") loadRelationships(0);
        if (t === "communities") loadCommunities(0);
      }
    },
    [api, loadEntities, loadRelationships, loadCommunities, loadClusterGraph, search, typeFilter],
  );

  // ── Filter changes (reload current tab) ────────────────────────────────
  useEffect(() => {
    if (tab !== "entities") return;
    let active = true;
    (async () => {
      await Promise.resolve();
      if (!active) return;
      setEntityPage(0);
      loadedTabs.current.delete("entities");
      setTabLoading(true);
      try {
        const [e] = await Promise.allSettled([
          api.listGraphragEntities({
            limit: PAGE_SIZE,
            offset: 0,
            search: search || undefined,
            entity_type: typeFilter || undefined,
          }),
        ]);
        if (!active) return;
        if (e.status === "fulfilled") {
          const data = e.value as unknown as GraphragEntity[];
          setEntities(data);
          if (stats && !search && !typeFilter) {
            setEntityTotal(stats.entities);
          } else {
            setEntityTotal(
              data.length === PAGE_SIZE ? 2 * PAGE_SIZE : PAGE_SIZE,
            );
          }
        }
      } catch {
        if (active) setEntities([]);
      } finally {
        if (active) setTabLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, [api, search, typeFilter, tab, stats]);

  // ── Auto-poll progress ─────────────────────────────────────────────────
  useEffect(() => {
    if (!progress) return;
    if (progress.task_status !== "queued" && progress.task_status !== "claimed") return;
    const id = setInterval(() => {
      loadStatsAndProgress();
    }, 5000);
    return () => clearInterval(id);
  }, [progress, loadStatsAndProgress]);

  const isEmpty = !loading && !tabLoading && stats && stats.entities === 0;

  return (
    <AppShell>
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-lg font-bold">
            <GitBranch className="h-5 w-5 text-indigo-500" />
            Knowledge Graph
          </h2>
          <button
            type="button"
            onClick={() => {
              loadedTabs.current.clear();
              loadStatsAndProgress();
              switchTab(tab);
            }}
            className="flex items-center gap-1 text-sm text-zinc-500 hover:text-zinc-700"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </button>
        </div>

        {/* Stats cards — always visible */}
        {stats && (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <StatCard icon={<GitBranch className="h-4 w-4" />} label="Entities" value={stats.entities} />
            <StatCard icon={<Network className="h-4 w-4" />} label="Relationships" value={stats.relationships} />
            <StatCard icon={<BookOpen className="h-4 w-4" />} label="Communities" value={stats.communities} />
            <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-950 p-4">
              <p className="text-xs text-zinc-400 mb-2">By type</p>
              <div className="flex flex-wrap gap-1">
                {stats.by_type.map((t) => (
                  <span key={t.type} className={`inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium ${entityTypeColor(t.type)}`}>
                    {t.type} <span className="ml-1 text-zinc-500">{t.count}</span>
                  </span>
                ))}
                {stats.by_type.length === 0 && <span className="text-xs text-zinc-400">—</span>}
              </div>
            </div>
          </div>
        )}

        {/* Progress bar */}
        {progress && progress.total > 0 && (
          <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-950 p-4">
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm font-medium text-zinc-700 dark:text-zinc-300">
                GraphRAG Processing
              </span>
              <span className="text-xs text-zinc-500">
                {progress.processed} / {progress.total} sources
                {progress.task_status === "queued" && " (queued)"}
                {progress.task_status === "claimed" && " (running…)"}
              </span>
            </div>
            <div className="h-2 w-full overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-800">
              <div
                className="h-full rounded-full bg-indigo-500 transition-all duration-500"
                style={{ width: `${progress.total > 0 ? (progress.processed / progress.total) * 100 : 0}%` }}
              />
            </div>
            {progress.task_status === "queued" && progress.task_attempts > 0 && (
              <p className="mt-1.5 text-[11px] text-zinc-400">
                Waiting to be picked up by a worker…
              </p>
            )}
          </div>
        )}

        {/* Empty state */}
        {isEmpty && (
          <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-950 p-12 text-center">
            <GitBranch className="mx-auto mb-3 h-10 w-10 text-zinc-300" />
            <p className="font-medium text-zinc-600 dark:text-zinc-300 mb-1">
              No knowledge graph data yet
            </p>
            <p className="text-sm text-zinc-400 max-w-md mx-auto">
              The graphrag pipeline stage extracts entities, relationships, and communities
              from your wiki pages using an LLM. Run the graphrag stage on your sources
              to populate the knowledge graph.
            </p>
          </div>
        )}

        {/* Tabs — always show if we have data */}
        {stats && stats.entities > 0 && (
          <>
            <div className="flex gap-1 border-b border-zinc-200 dark:border-zinc-800">
              {([
                ["graph", "Graph", 0],
                ["topics", "Topics", clusterGraph?.nodes?.length ?? 0],
                ["entities", "Entities", stats.entities],
                ["relationships", "Relationships", stats.relationships],
                ["communities", "Communities", stats.communities],
              ] as const).map(([key, label, count]) => (
                <button
                  key={key}
                  type="button"
                  onClick={() => switchTab(key)}
                  className={`flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
                    tab === key
                      ? "border-indigo-500 text-indigo-600 dark:text-indigo-400"
                      : "border-transparent text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300"
                  }`}
                >
                  {label}
                  <span className="rounded-full bg-zinc-200 dark:bg-zinc-800 px-1.5 py-0.5 text-[10px] tabular-nums">
                    {count}
                  </span>
                </button>
              ))}
              {tabLoading && (
                <span className="ml-2 self-center">
                  <RefreshCw className="h-3.5 w-3.5 animate-spin text-zinc-400" />
                </span>
              )}
            </div>

            {/* Graph visualization tab */}
            {tab === "graph" && (
              <KnowledgeGraphViz
                entities={entities}
                relationships={relationships}
                communities={communities}
              />
            )}

            {/* Topics tab — cluster correlation graph */}
            {tab === "topics" && <ClusterGraphViz graph={clusterGraph} />}

            {/* Entities tab */}
            {tab === "entities" && (
              <div className="space-y-3">
                <div className="flex items-center gap-3">
                  <div className="relative flex-1 max-w-xs">
                    <SearchIcon className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-400" />
                    <input
                      type="search"
                      value={search}
                      onChange={(e) => setSearch(e.target.value)}
                      placeholder="Search entities…"
                      className="w-full rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 py-1.5 pl-9 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                    />
                  </div>
                  <div className="flex items-center gap-1.5">
                    <Filter className="h-3.5 w-3.5 text-zinc-400" />
                    <select
                      value={typeFilter}
                      onChange={(e) => setTypeFilter(e.target.value)}
                      className="rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-2 py-1.5 text-sm"
                    >
                      <option value="">All types</option>
                      <option value="person">Person</option>
                      <option value="org">Organization</option>
                      <option value="concept">Concept</option>
                      <option value="location">Location</option>
                      <option value="technology">Technology</option>
                      <option value="event">Event</option>
                    </select>
                  </div>
                </div>
                <div className="grid grid-cols-1 gap-2 md:grid-cols-2 lg:grid-cols-3">
                  {entities.map((e) => (
                    <button
                      key={e.entity_id}
                      type="button"
                      onClick={() => setDrawerEntity(e)}
                      className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-950 p-3 text-left transition-colors hover:border-indigo-300 dark:hover:border-indigo-700"
                    >
                      <div className="flex items-start justify-between gap-2 mb-1">
                        <h3 className="text-sm font-semibold text-indigo-600 dark:text-indigo-400">{e.name}</h3>
                        <span className={`shrink-0 inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium ${entityTypeColor(e.entity_type)}`}>
                          {e.entity_type}
                        </span>
                      </div>
                      {e.description && (
                        <p className="text-xs text-zinc-500 line-clamp-2 mb-1.5">
                          {truncate(e.description, 150)}
                        </p>
                      )}
                      <div className="flex items-center gap-1 text-[11px] text-zinc-400">
                        <Users className="h-3 w-3" />
                        mentioned {e.frequency}×
                      </div>
                    </button>
                  ))}
                </div>
                {entities.length === 0 && !tabLoading && (
                  <p className="py-8 text-center text-sm text-zinc-400">
                    No entities match your search
                  </p>
                )}
                <Pagination
                  page={entityPage}
                  total={entityTotal}
                  pageSize={PAGE_SIZE}
                  onPageChange={(p) => {
                    setEntityPage(p);
                    loadEntities(p, search, typeFilter);
                  }}
                />
              </div>
            )}

            {/* Relationships tab */}
            {tab === "relationships" && (
              <div className="space-y-3">
                <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 overflow-hidden">
                  <table className="w-full text-sm">
                    <thead className="bg-zinc-50 dark:bg-zinc-900">
                      <tr>
                        <th className="px-4 py-2 text-left font-medium text-zinc-600 dark:text-zinc-400">Source</th>
                        <th className="px-4 py-2 text-left font-medium text-zinc-600 dark:text-zinc-400">Relationship</th>
                        <th className="px-4 py-2 text-left font-medium text-zinc-600 dark:text-zinc-400">Target</th>
                        <th className="px-4 py-2 text-left font-medium text-zinc-600 dark:text-zinc-400">Description</th>
                        <th className="px-4 py-2 text-right font-medium text-zinc-600 dark:text-zinc-400">Weight</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
                      {relationships.map((r) => (
                        <tr key={r.rel_id} className="hover:bg-zinc-50 dark:hover:bg-zinc-900/50">
                          <td className="px-4 py-2.5">
                            <button
                              type="button"
                              onClick={() => {
                                const found = entities.find((e) => e.name === r.source);
                                if (found) setDrawerEntity(found);
                              }}
                              className="font-medium text-indigo-600 hover:underline dark:text-indigo-400"
                            >
                              {r.source}
                            </button>
                            <span className={`ml-1.5 inline-flex items-center rounded px-1 py-0.5 text-[9px] font-medium ${entityTypeColor(r.source_type)}`}>
                              {r.source_type}
                            </span>
                          </td>
                          <td className="px-4 py-2.5">
                            <Badge>{r.relationship_type}</Badge>
                          </td>
                          <td className="px-4 py-2.5">
                            <button
                              type="button"
                              onClick={() => {
                                const found = entities.find((e) => e.name === r.target);
                                if (found) setDrawerEntity(found);
                              }}
                              className="font-medium text-indigo-600 hover:underline dark:text-indigo-400"
                            >
                              {r.target}
                            </button>
                            <span className={`ml-1.5 inline-flex items-center rounded px-1 py-0.5 text-[9px] font-medium ${entityTypeColor(r.target_type)}`}>
                              {r.target_type}
                            </span>
                          </td>
                          <td className="px-4 py-2.5 text-xs text-zinc-500 max-w-xs truncate">
                            {r.description || "—"}
                          </td>
                          <td className="px-4 py-2.5 text-right">
                            <div className="flex items-center justify-end gap-1.5">
                              <div className="h-1.5 w-12 overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-800">
                                <div
                                  className="h-full rounded-full bg-indigo-500"
                                  style={{ width: `${Math.min(100, r.weight * 100)}%` }}
                                />
                              </div>
                              <span className="text-xs tabular-nums text-zinc-500">
                                {r.weight.toFixed(1)}
                              </span>
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {relationships.length === 0 && !tabLoading && (
                  <p className="py-8 text-center text-sm text-zinc-400">
                    No relationships found
                  </p>
                )}
                <Pagination
                  page={relPage}
                  total={relTotal}
                  pageSize={PAGE_SIZE}
                  onPageChange={(p) => {
                    setRelPage(p);
                    loadRelationships(p);
                  }}
                />
              </div>
            )}

            {/* Communities tab */}
            {tab === "communities" && (
              <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                {communities.map((c) => (
                  <div
                    key={c.community_id}
                    className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-950 p-4"
                  >
                    <div className="flex items-start justify-between gap-2 mb-2">
                      <h3 className="text-sm font-semibold">{c.title}</h3>
                      <Badge variant="info">Level {c.level}</Badge>
                    </div>
                    {c.summary && (
                      <p className="text-xs text-zinc-500 mb-3 line-clamp-3">
                        {truncate(c.summary, 300)}
                      </p>
                    )}
                    {c.member_entities.length > 0 && (
                      <div>
                        <p className="text-[11px] font-medium text-zinc-400 mb-1">
                          Members ({c.member_entities.length})
                        </p>
                        <div className="flex flex-wrap gap-1">
                          {c.member_entities.slice(0, 8).map((m) => (
                            <span
                              key={m}
                              className="rounded bg-zinc-100 dark:bg-zinc-800 px-1.5 py-0.5 text-[10px] text-zinc-600 dark:text-zinc-400"
                            >
                              {m}
                            </span>
                          ))}
                          {c.member_entities.length > 8 && (
                            <span className="text-[10px] text-zinc-400">
                              +{c.member_entities.length - 8} more
                            </span>
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                ))}
                {communities.length === 0 && !tabLoading && (
                  <p className="col-span-full py-8 text-center text-sm text-zinc-400">
                    No communities found
                  </p>
                )}
                <div className="col-span-full">
                  <Pagination
                    page={commPage}
                    total={commTotal}
                    pageSize={PAGE_SIZE}
                    onPageChange={(p) => {
                      setCommPage(p);
                      loadCommunities(p);
                    }}
                  />
                </div>
              </div>
            )}
          </>
        )}
      </div>

      {/* Entity detail drawer */}
      <EntityDetailDrawer
        entity={drawerEntity}
        relationships={relationships}
        communities={communities}
        onClose={() => setDrawerEntity(null)}
      />
    </AppShell>
  );
}

function ClusterGraphViz({
  graph,
}: {
  graph: {
    nodes: {
      cluster_id: string;
      topic_name: string;
      top_keywords: string[];
      unit_count: number;
      consensus_score: number;
    }[];
    edges: { source: string; target: string; weight: number }[];
  } | null;
}) {
  const [selectedId, setSelectedId] = useState<string | null>(null);

  if (!graph || graph.nodes.length === 0) {
    return (
      <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-950 p-12 text-center">
        <Network className="mx-auto mb-3 h-10 w-10 text-zinc-300" />
        <p className="font-medium text-zinc-600 dark:text-zinc-300 mb-1">
          No topic clusters yet
        </p>
        <p className="text-sm text-zinc-400 max-w-md mx-auto">
          Run the cluster stage to group wiki content into topics. Clusters
          sharing source documents appear as connected nodes here.
        </p>
      </div>
    );
  }

  const nodes = graph.nodes;
  const links = graph.edges.map((e) => ({
    source: e.source,
    target: e.target,
    weight: e.weight,
  }));
  const selected = nodes.find((n) => n.cluster_id === selectedId) ?? null;
  const neighbors = new Set<string>();
  if (selectedId) {
    neighbors.add(selectedId);
    for (const l of links) {
      if (l.source === selectedId) neighbors.add(l.target as string);
      if (l.target === selectedId) neighbors.add(l.source as string);
    }
  }

  // Static circle packing: clusters arranged in a ring, sized by unit count,
  // edges drawn between clusters that share source documents.
  const R = 150;
  const cx = 240;
  const cy = 220;
  const byId = new Map(nodes.map((n) => [n.cluster_id, n]));

  return (
    <div className="flex flex-col gap-4 lg:flex-row">
      <div className="relative h-[560px] flex-1 overflow-hidden rounded-lg border border-zinc-200 bg-white dark:border-zinc-950 dark:border-zinc-800">
        <svg viewBox="0 0 480 440" className="h-full w-full">
          {/* Edges */}
          {links.map((l, i) => {
            const a = byId.get(l.source as string);
            const b = byId.get(l.target as string);
            if (!a || !b) return null;
            const ia = nodes.indexOf(a);
            const ib = nodes.indexOf(b);
            const angA = (ia / nodes.length) * 2 * Math.PI - Math.PI / 2;
            const angB = (ib / nodes.length) * 2 * Math.PI - Math.PI / 2;
            const x1 = cx + R * Math.cos(angA);
            const y1 = cy + R * Math.sin(angA);
            const x2 = cx + R * Math.cos(angB);
            const y2 = cy + R * Math.sin(angB);
            const active =
              !selectedId ||
              l.source === selectedId ||
              l.target === selectedId;
            return (
              <line
                key={i}
                x1={x1}
                y1={y1}
                x2={x2}
                y2={y2}
                stroke={active ? "#818cf8" : "#e4e4e7"}
                strokeOpacity={active ? 0.15 + Math.min(0.5, l.weight / 8) : 0.08}
                strokeWidth={active ? 0.5 + Math.min(3, l.weight / 2) : 0.4}
              />
            );
          })}
          {/* Nodes */}
          {nodes.map((n, i) => {
            const ang = (i / nodes.length) * 2 * Math.PI - Math.PI / 2;
            const x = cx + R * Math.cos(ang);
            const y = cy + R * Math.sin(ang);
            const r = Math.max(6, Math.min(26, 6 + Math.sqrt(n.unit_count)));
            const dimmed = selectedId && !neighbors.has(n.cluster_id);
            return (
              <g key={n.cluster_id} onClick={() => setSelectedId(selectedId === n.cluster_id ? null : n.cluster_id)} className="cursor-pointer">
                <circle
                  cx={x}
                  cy={y}
                  r={r}
                  fill={dimmed ? "#e4e4e7" : "#818cf8"}
                  fillOpacity={dimmed ? 0.4 : 0.85}
                  stroke="#fff"
                  strokeWidth={selectedId === n.cluster_id ? 3 : 1.5}
                />
                <text
                  x={x}
                  y={y + r + 12}
                  textAnchor="middle"
                  fontSize="10"
                  fill={dimmed ? "#d4d4d8" : "#71717a"}
                  className="select-none"
                >
                  {n.topic_name.length > 22 ? `${n.topic_name.slice(0, 20)}…` : n.topic_name}
                </text>
              </g>
            );
          })}
        </svg>
        <div className="absolute bottom-2 left-2 rounded bg-white/80 px-2 py-1 text-[10px] text-zinc-500 dark:bg-zinc-950/80">
          {nodes.length} topics · {links.length} shared-source edges
        </div>
      </div>

      {/* Selected topic side panel */}
      {selected && (
        <aside className="w-full shrink-0 space-y-3 rounded-lg border border-zinc-200 bg-white p-4 lg:w-80 dark:border-zinc-800 dark:bg-zinc-950">
          <div className="flex items-start justify-between gap-2">
            <h3 className="text-sm font-semibold leading-snug">{selected.topic_name}</h3>
            <button
              type="button"
              onClick={() => setSelectedId(null)}
              className="text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300"
            >
              ✕
            </button>
          </div>
          <div className="flex flex-wrap gap-1.5">
            <span className="inline-flex items-center rounded bg-indigo-100 px-1.5 py-0.5 text-[10px] font-medium text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300">
              {selected.unit_count} units
            </span>
            <span className="inline-flex items-center rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400">
              consensus {selected.consensus_score.toFixed(2)}
            </span>
          </div>
          {selected.top_keywords.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {selected.top_keywords.map((k) => (
                <span
                  key={k}
                  className="rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400"
                >
                  {k}
                </span>
              ))}
            </div>
          )}
          <div className="border-t border-zinc-200 pt-3 dark:border-zinc-800">
            <p className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-zinc-400">
              Connected topics ({neighbors.size - 1})
            </p>
            <ul className="space-y-1">
              {nodes
                .filter((n) => n.cluster_id !== selectedId && neighbors.has(n.cluster_id))
                .map((n) => (
                  <li key={n.cluster_id} className="text-[13px] text-zinc-600 dark:text-zinc-400">
                    {n.topic_name}
                  </li>
                ))}
            </ul>
          </div>
        </aside>
      )}
    </div>
  );
}

function StatCard({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode;
  label: string;
  value: number;
}) {
  return (
    <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-950 p-4">
      <div className="flex items-center gap-2 text-zinc-400 mb-1">
        {icon}
        <span className="text-xs">{label}</span>
      </div>
      <p className="text-2xl font-bold tabular-nums">{value}</p>
    </div>
  );
}

function Pagination({
  page,
  total,
  pageSize,
  onPageChange,
}: {
  page: number;
  total: number;
  pageSize: number;
  onPageChange: (page: number) => void;
}) {
  const totalPages = Math.ceil(total / pageSize);
  if (totalPages <= 1) return null;

  return (
    <div className="flex items-center justify-between">
      <span className="text-xs text-zinc-500">
        Page {page + 1} of {totalPages} ({total} total)
      </span>
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => onPageChange(Math.max(0, page - 1))}
          disabled={page === 0}
          className="rounded-md border border-zinc-300 dark:border-zinc-700 px-3 py-1 text-xs font-medium text-zinc-600 dark:text-zinc-300 hover:bg-zinc-50 dark:hover:bg-zinc-800 disabled:opacity-50"
        >
          ← Prev
        </button>
        <button
          type="button"
          onClick={() => onPageChange(Math.min(totalPages - 1, page + 1))}
          disabled={page >= totalPages - 1}
          className="rounded-md border border-zinc-300 dark:border-zinc-700 px-3 py-1 text-xs font-medium text-zinc-600 dark:text-zinc-300 hover:bg-zinc-50 dark:hover:bg-zinc-800 disabled:opacity-50"
        >
          Next →
        </button>
      </div>
    </div>
  );
}
