"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import {
  Box,
  BookOpen,
  ExternalLink,
  FolderTree,
  GitCompareArrows,
  Network,
  Search as SearchIcon,
  X,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { useApi } from "@/lib/hooks";
import { truncate } from "@/lib/utils";
import type { WikiGraphLink, WikiGraphNode, WikiGraphResponse } from "@/lib/types";
import type {
  ForceGraphMethods as ForceGraphMethods2D,
  ForceGraphProps as ForceGraphProps2D,
  LinkObject as LinkObject2D,
  NodeObject as NodeObject2D,
} from "react-force-graph-2d";
import type {
  ForceGraphMethods as ForceGraphMethods3D,
  ForceGraphProps as ForceGraphProps3D,
  LinkObject as LinkObject3D,
  NodeObject as NodeObject3D,
} from "react-force-graph-3d";

// Canvas/WebGL force graphs; loaded client-side only (no canvas on server).
// dynamic() erases the generic types, so re-apply them explicitly.
const ForceGraph2D = dynamic(() => import("react-force-graph-2d"), {
  ssr: false,
}) as unknown as React.ComponentType<
  ForceGraphProps2D<WikiGraphNode> & {
    ref?: React.MutableRefObject<ForceGraphMethods2D<WikiGraphNode> | undefined>;
  }
>;
const ForceGraph3D = dynamic(() => import("react-force-graph-3d"), {
  ssr: false,
}) as unknown as React.ComponentType<
  ForceGraphProps3D<WikiGraphNode> & {
    ref?: React.MutableRefObject<ForceGraphMethods3D<WikiGraphNode> | undefined>;
  }
>;

const PALETTE = [
  "#6366f1",
  "#8b5cf6",
  "#ec4899",
  "#f59e0b",
  "#10b981",
  "#06b6d4",
  "#3b82f6",
  "#ef4444",
  "#84cc16",
  "#f97316",
  "#14b8a6",
  "#a855f7",
];
/** Cross-project nodes/edges get a distinct color. */
const CROSS_COLOR = "#f43f5e";
/** Whole-wiki macro view: clusters colored by kind. */
const PROJECT_CLUSTER_COLOR = "#6366f1";
const AREA_CLUSTER_COLOR = "#14b8a6";

/** Scopes bigger than this are too heavy for a single force layout. */
const MAX_SCOPE = 500;
/** The "entire wiki" macro scope key. */
const ALL_SCOPE = "all";
/** Cap for rows rendered in the edge comparison table. */
const MAX_EDGE_ROWS = 250;

type Mode = "2d" | "3d";

interface ScopeOption {
  key: string;
  label: string;
  count: number;
  kind: "all" | "project" | "area";
}

type GraphNode = NodeObject2D<WikiGraphNode> & {
  val: number;
  label: string;
  deg: number;
};

export function WikiGraphClient({
  scopes,
  initialGraph,
  initialGraphError,
  defaultScope,
  loadError,
  initialCross,
  initialMinScore,
  initialMode,
}: {
  scopes: ScopeOption[];
  initialGraph: WikiGraphResponse | null;
  initialGraphError: string | null;
  defaultScope: string | null;
  loadError: string | null;
  initialCross: boolean;
  initialMinScore: number;
  initialMode: Mode;
}) {
  // URL state (?scope=&cross=&min=&mode=) makes every graph view shareable.
  // AppShell only mounts children client-side after hydration, so reading
  // window.location here is safe (no SSR mismatch).
  const readUrlParam = (key: string) =>
    new URLSearchParams(window.location.search).get(key);

  const api = useApi();
  const [scope, setScope] = useState(defaultScope ?? scopes[0]?.key ?? "");
  const [graph, setGraph] = useState<WikiGraphResponse | null>(initialGraph);
  const [error, setError] = useState<string | null>(
    initialGraphError ?? loadError ?? null,
  );
  const [loading, setLoading] = useState(false);
  const [minScore, setMinScore] = useState(() => {
    const raw = Number.parseFloat(readUrlParam("min") ?? "");
    return Number.isFinite(raw)
      ? Math.min(0.6, Math.max(0, raw))
      : initialMinScore;
  });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [compareId, setCompareId] = useState<string | null>(null);
  const [focusQuery, setFocusQuery] = useState("");
  const [mode, setMode] = useState<Mode>(() =>
    readUrlParam("mode") === "3d" ? "3d" : initialMode,
  );
  const [crossOn, setCrossOn] = useState(
    () =>
      readUrlParam("cross") === "1" || readUrlParam("cross") === "true"
        ? true
        : initialCross,
  );
  const [showEdges, setShowEdges] = useState(false);
  const [focusEdge, setFocusEdge] = useState<{ s: string; t: string } | null>(
    null,
  );
  const [edgeFilter, setEdgeFilter] = useState("");
  const [edgeSort, setEdgeSort] = useState<"score" | "source" | "target">(
    "score",
  );
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [containerSize, setContainerSize] = useState({ width: 0, height: 0 });
  const containerRef = useRef<HTMLDivElement | null>(null);
  const graph2dRef = useRef<ForceGraphMethods2D<WikiGraphNode> | undefined>(
    undefined,
  );
  const graph3dRef = useRef<ForceGraphMethods3D<WikiGraphNode> | undefined>(
    undefined,
  );

  const macroMode = scope === ALL_SCOPE;

  // Measure the canvas container (ResizeObserver fires once on observe).
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      setContainerSize({ width: el.clientWidth, height: el.clientHeight });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Keep the URL in sync with the view state so graph views stay shareable.
  const syncUrl = useCallback(
    (next: { scope?: string; cross?: boolean; min?: number; mode?: Mode }) => {
      const url = new URL(window.location.href);
      const nextScope = next.scope ?? scope;
      if (!nextScope || nextScope === ALL_SCOPE) url.searchParams.delete("scope");
      else url.searchParams.set("scope", nextScope);
      const nextCross = next.cross ?? crossOn;
      if (nextCross) url.searchParams.set("cross", "1");
      else url.searchParams.delete("cross");
      const nextMin = next.min ?? minScore;
      if (nextMin !== 0.1) url.searchParams.set("min", String(nextMin));
      else url.searchParams.delete("min");
      const nextMode = next.mode ?? mode;
      if (nextMode === "3d") url.searchParams.set("mode", "3d");
      else url.searchParams.delete("mode");
      window.history.replaceState(null, "", url.toString());
    },
    [scope, crossOn, minScore, mode],
  );

  const loadScope = useCallback(
    async (key: string, withCross: boolean) => {
      setScope(key);
      setSelectedId(null);
      setCompareId(null);
      setFocusEdge(null);
      setLoading(true);
      setError(null);
      syncUrl({ scope: key, cross: withCross });
      try {
        // The macro view is cluster-level, so it uses tighter edge settings
        // and never needs the cross-project toggle.
        const g =
          key === ALL_SCOPE
            ? await api.getWikiGraph(ALL_SCOPE, 3, 0.05, false, 5)
            : await api.getWikiGraph(key, 4, 0.1, withCross, 5);
        setGraph(g);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to load graph");
        setGraph({ scope: key, nodes: [], links: [] });
      } finally {
        setLoading(false);
      }
    },
    [api, syncUrl],
  );

  const toggleCross = useCallback(
    (on: boolean) => {
      setCrossOn(on);
      syncUrl({ cross: on });
      if (scope && scope !== ALL_SCOPE) void loadScope(scope, on);
    },
    [loadScope, scope, syncUrl],
  );

  // Cross-project links are expensive (per-page FTS queries), so the SSR
  // initial fetch skips them; a share link with cross=1 refetches on mount.
  const didInitCross = useRef(false);
  useEffect(() => {
    if (didInitCross.current) return;
    didInitCross.current = true;
    if (initialCross && scope && scope !== ALL_SCOPE) {
      // Intentional: fulfill a share link's cross=1 after the fast SSR render.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      void loadScope(scope, true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Fit the layout to the viewport whenever a new graph (or mode) arrives.
  useEffect(() => {
    if (!graph || graph.nodes.length === 0) return;
    const t = setTimeout(() => {
      if (mode === "3d") graph3dRef.current?.zoomToFit(600, 60);
      else graph2dRef.current?.zoomToFit(600, 60);
    }, 350);
    return () => clearTimeout(t);
  }, [graph, mode]);

  const { nodes, links, subfolders, crossLinks } = useMemo(() => {
    if (!graph) {
      return {
        nodes: [],
        links: [],
        subfolders: new Set<string>(),
        crossLinks: 0,
      };
    }
    const degree = new Map<string, number>();
    for (const l of graph.links) {
      degree.set(l.source, (degree.get(l.source) ?? 0) + 1);
      degree.set(l.target, (degree.get(l.target) ?? 0) + 1);
    }
    const nodes: GraphNode[] = graph.nodes.map((n) => {
      const deg = degree.get(n.id) ?? 0;
      return {
        ...n,
        val: n.cluster
          ? Math.min(10 + Math.log2((n.count ?? 2)) * 4, 34)
          : Math.min(4 + deg * 0.9, 16),
        label: n.title || n.file_path.split("/").pop() || n.id,
        deg,
      };
    });
    // Fresh copies for the renderer (the graph lib mutates link source/target
    // in place); in-scope edges respect the slider, cross edges always show.
    const links: (WikiGraphLink & {
      source: string;
      target: string;
    })[] = [];
    let crossLinks = 0;
    for (const l of graph.links) {
      if (l.cross) {
        links.push({ ...l });
        crossLinks += 1;
      } else if (l.score >= minScore) {
        links.push({ ...l });
      }
    }
    const subfolders = new Set(
      graph.nodes.map((n) => (n.cluster ? (n.kind ?? "cluster") : n.subfolder || "root")),
    );
    return { nodes, links, subfolders, crossLinks };
  }, [graph, minScore]);

  const colorFor = useCallback((subfolder: string) => {
    const key = subfolder || "root";
    let h = 7;
    for (const c of key) h = (h * 31 + c.charCodeAt(0)) >>> 0;
    return PALETTE[h % PALETTE.length];
  }, []);

  const neighborIds = useMemo(() => {
    if (!selectedId) return new Set<string>();
    const set = new Set<string>([selectedId]);
    for (const l of links) {
      if (l.source === selectedId) set.add(l.target as string);
      if (l.target === selectedId) set.add(l.source as string);
    }
    return set;
  }, [links, selectedId]);

  const nodeColor = useCallback(
    (n: NodeObject2D<WikiGraphNode>) => {
      const gn = n as GraphNode;
      if (gn.cross) return CROSS_COLOR;
      if (gn.cluster) return gn.kind === "project" ? PROJECT_CLUSTER_COLOR : AREA_CLUSTER_COLOR;
      const base = colorFor(gn.subfolder ?? "");
      if (
        selectedId &&
        n.id !== selectedId &&
        !neighborIds.has(n.id as string)
      ) {
        return "rgba(140,140,150,0.18)";
      }
      return base;
    },
    [colorFor, selectedId, neighborIds],
  );

  const linkColor = useCallback((l: LinkObject2D<WikiGraphNode>) => {
    const score = (l as { score?: number }).score ?? 0;
    if ((l as { cross?: boolean }).cross) {
      return `rgba(244,63,94,${0.2 + score * 0.8})`;
    }
    return `rgba(99,102,241,${0.15 + score * 0.85})`;
  }, []);

  const linkWidth = useCallback((l: LinkObject2D<WikiGraphNode>) => {
    return Math.max(0.5, ((l as { score?: number }).score ?? 0) * 3);
  }, []);

  const nodeLabel = useCallback((n: NodeObject2D<WikiGraphNode>) => {
    const gn = n as GraphNode;
    return gn.cluster ? `${gn.label} — ${gn.count} pages` : gn.label;
  }, []);

  // Macro mode is clusters — labels stay on so you can read the map;
  // regular scopes cap labels to keep smaller graphs clean.
  const showLabels = macroMode || (nodes.length > 0 && nodes.length <= 150);

  const nodeCanvasObject = useCallback(
    (
      node: NodeObject2D<WikiGraphNode>,
      ctx: CanvasRenderingContext2D,
      globalScale: number,
    ) => {
      const n = node as GraphNode;
      const x = n.x ?? 0;
      const y = n.y ?? 0;
      ctx.beginPath();
      ctx.arc(x, y, n.val, 0, 2 * Math.PI);
      ctx.fillStyle = nodeColor(node);
      ctx.fill();
      const focusedEndpoint =
        focusEdge && (n.id === focusEdge.s || n.id === focusEdge.t);
      if (n.id === selectedId || focusedEndpoint) {
        ctx.strokeStyle = focusedEndpoint
          ? "rgba(251,191,36,0.95)"
          : "rgba(255,255,255,0.95)";
        ctx.lineWidth = 2 / globalScale;
        ctx.stroke();
      }
      if (n.id === hoveredId) {
        ctx.strokeStyle = "rgba(255,255,255,0.65)";
        ctx.lineWidth = 1.5 / globalScale;
        ctx.beginPath();
        ctx.arc(x, y, n.val + 3, 0, 2 * Math.PI);
        ctx.stroke();
      }
      if (showLabels && globalScale > (macroMode ? 0.15 : 0.35)) {
        const label =
          n.label.length > (macroMode ? 20 : 22)
            ? `${n.label.slice(0, macroMode ? 20 : 22)}…`
            : n.label;
        ctx.font = `${(macroMode ? 10.5 : 11) / globalScale}px Inter, system-ui, sans-serif`;
        const isMuted =
          selectedId &&
          n.id !== selectedId &&
          !neighborIds.has(n.id as string);
        ctx.fillStyle = isMuted
          ? "rgba(113,113,122,0.35)"
          : n.id === selectedId
            ? "rgba(24,24,27,0.95)"
            : "rgba(113,113,122,0.85)";
        ctx.fillText(label, x + n.val + 3 / globalScale, y + 3 / globalScale);
      }
    },
    [
      nodeColor,
      selectedId,
      showLabels,
      hoveredId,
      focusEdge,
      neighborIds,
      macroMode,
    ],
  );

  // Draw each edge as a line plus a shared-terms label — shown for edges
  // touching the selected node, or all edges when the graph is small.
  const showAllEdgeLabels = links.length > 0 && links.length <= 40;
  const linkCanvasObject = useCallback(
    (
      link: LinkObject2D<WikiGraphNode>,
      ctx: CanvasRenderingContext2D,
      globalScale: number,
    ) => {
      const l = link as LinkObject2D<WikiGraphNode> & {
        terms?: string[];
        cross?: boolean;
        score?: number;
      };
      const s = l.source as unknown as { x?: number; y?: number; id?: string | number };
      const t = l.target as unknown as { x?: number; y?: number; id?: string | number };
      if (
        typeof s.x !== "number" ||
        typeof s.y !== "number" ||
        typeof t.x !== "number" ||
        typeof t.y !== "number"
      )
        return;

      ctx.beginPath();
      ctx.moveTo(s.x, s.y);
      ctx.lineTo(t.x, t.y);
      ctx.strokeStyle = linkColor(l);
      ctx.lineWidth = linkWidth(l);
      ctx.stroke();

      const incident =
        selectedId && (s.id === selectedId || t.id === selectedId);
      const focused =
        focusEdge &&
        ((s.id === focusEdge.s && t.id === focusEdge.t) ||
          (s.id === focusEdge.t && t.id === focusEdge.s));
      if (focused) {
        ctx.beginPath();
        ctx.moveTo(s.x, s.y);
        ctx.lineTo(t.x, t.y);
        ctx.strokeStyle = "rgba(251,191,36,0.35)";
        ctx.lineWidth = linkWidth(l) + 6 / globalScale;
        ctx.stroke();
      }
      const terms = l.terms ?? [];
      if (terms.length === 0 || !(showAllEdgeLabels || incident || focused))
        return;

      const mx = (s.x + t.x) / 2;
      const my = (s.y + t.y) / 2;
      const label = terms.slice(0, 3).join(" · ");
      const pad = 3 / globalScale;
      const h = 12 / globalScale;
      ctx.font = `${9.5 / globalScale}px Inter, system-ui, sans-serif`;
      const w = ctx.measureText(label).width;
      ctx.fillStyle = "rgba(255,255,255,0.92)";
      ctx.strokeStyle = "rgba(24,24,27,0.12)";
      ctx.lineWidth = 1 / globalScale;
      ctx.beginPath();
      ctx.roundRect(mx - w / 2 - pad, my - h / 2, w + 2 * pad, h, 3 / globalScale);
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = "rgba(63,63,70,0.95)";
      ctx.fillText(label, mx - w / 2, my + 3.5 / globalScale);
    },
    [linkColor, linkWidth, selectedId, showAllEdgeLabels, focusEdge],
  );

  const onNodeClick = useCallback((node: NodeObject2D<WikiGraphNode>) => {
    setSelectedId(node.id as string);
    setCompareId(null);
    setFocusEdge(null);
  }, []);
  const onBackgroundClick = useCallback(() => {
    setSelectedId(null);
    setCompareId(null);
    setFocusEdge(null);
  }, []);

  const focusNode = useCallback(() => {
    const q = focusQuery.trim().toLowerCase();
    if (!q || !graph) return;
    const hit = graph.nodes.find(
      (n) =>
        n.title?.toLowerCase().includes(q) ||
        n.file_path?.toLowerCase().includes(q),
    );
    if (!hit) return;
    setSelectedId(hit.id);
    const gn = nodes.find((n) => n.id === hit.id);
    if (gn && gn.x !== undefined && gn.y !== undefined) {
      if (mode === "3d") {
        graph3dRef.current?.cameraPosition(
          { x: gn.x, y: gn.y, z: 260 },
          undefined,
          500,
        );
      } else {
        graph2dRef.current?.centerAt(gn.x, gn.y, 500);
        graph2dRef.current?.zoom(2.2, 500);
      }
    }
  }, [focusQuery, graph, nodes, mode]);

  const selectedNode = useMemo(
    () => graph?.nodes.find((n) => n.id === selectedId) ?? null,
    [graph, selectedId],
  );
  const related = useMemo(() => {
    if (!selectedId || !graph) return [];
    const rel: {
      node: WikiGraphNode;
      score: number;
      cross?: boolean;
      terms?: string[];
    }[] = [];
    for (const l of graph.links as WikiGraphLink[]) {
      if (l.source === selectedId) {
        const t = graph.nodes.find((n) => n.id === l.target);
        if (t) rel.push({ node: t, score: l.score, cross: l.cross, terms: l.terms });
      } else if (l.target === selectedId) {
        const s = graph.nodes.find((n) => n.id === l.source);
        if (s) rel.push({ node: s, score: l.score, cross: l.cross, terms: l.terms });
      }
    }
    return rel.sort((a, b) => b.score - a.score).slice(0, 10);
  }, [selectedId, graph]);

  // ── Similarity comparison (side-by-side shared terms) ────────────────────
  const compareNode = useMemo(
    () =>
      compareId && compareId !== selectedId
        ? (graph?.nodes.find((n) => n.id === compareId) ?? null)
        : null,
    [compareId, selectedId, graph],
  );
  const compareEdge = useMemo(() => {
    if (!compareNode || !graph || !selectedId) return null;
    return (
      graph.links.find(
        (l) =>
          (l.source === selectedId && l.target === compareNode.id) ||
          (l.source === compareNode.id && l.target === selectedId),
      ) ?? null
    );
  }, [compareNode, graph, selectedId]);
  const compareShared = useMemo(() => {
    const weights = compareEdge?.term_weights ?? [];
    if (weights.length > 0) return weights.map((w) => w.term);
    return compareEdge?.terms ?? [];
  }, [compareEdge]);
  const compareSharedSet = useMemo(
    () => new Set(compareShared),
    [compareShared],
  );

  // Jump to an edge in the graph: highlight it, select its source, and center
  // the view on the edge midpoint.
  const focusEdgeInGraph = useCallback(
    (s: string, t: string) => {
      setFocusEdge({ s, t });
      setSelectedId(s);
      const a = nodes.find((n) => n.id === s);
      const b = nodes.find((n) => n.id === t);
      if (
        a &&
        b &&
        a.x !== undefined &&
        a.y !== undefined &&
        b.x !== undefined &&
        b.y !== undefined
      ) {
        const mx = (a.x + b.x) / 2;
        const my = (a.y + b.y) / 2;
        if (mode === "3d") {
          graph3dRef.current?.cameraPosition(
            { x: mx, y: my, z: 240 },
            undefined,
            500,
          );
        } else {
          graph2dRef.current?.centerAt(mx, my, 500);
          graph2dRef.current?.zoom(1.6, 500);
        }
      }
    },
    [nodes, mode],
  );

  // The edge comparison table: every link resolved to endpoint titles, sorted
  // and filtered for comparison, with score bars and shared terms.
  const edgeRows = useMemo(() => {
    if (!graph) return [];
    const titleOf = (id: string) => {
      const n = graph.nodes.find((node) => node.id === id);
      return n ? n.title || n.file_path.split("/").pop() || id : id;
    };
    const rows = graph.links.map((l) => ({
      s: l.source,
      t: l.target,
      sTitle: titleOf(l.source),
      tTitle: titleOf(l.target),
      score: l.score,
      cross: !!l.cross,
      terms: l.terms ?? [],
    }));
    const q = edgeFilter.trim().toLowerCase();
    const filtered = q
      ? rows.filter(
          (r) =>
            r.sTitle.toLowerCase().includes(q) ||
            r.tTitle.toLowerCase().includes(q) ||
            r.terms.some((term) => term.toLowerCase().includes(q)),
        )
      : rows;
    const sorted = [...filtered].sort((a, b) => {
      if (edgeSort === "source")
        return a.sTitle.localeCompare(b.sTitle) || b.score - a.score;
      if (edgeSort === "target")
        return a.tTitle.localeCompare(b.tTitle) || b.score - a.score;
      return b.score - a.score;
    });
    return sorted;
  }, [graph, edgeFilter, edgeSort]);

  const visibleEdgeRows = edgeRows.slice(0, MAX_EDGE_ROWS);

  const selectClass =
    "rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-1.5 text-sm";

  const legendColor = (s: string) =>
    macroMode
      ? s === "project"
        ? PROJECT_CLUSTER_COLOR
        : AREA_CLUSTER_COLOR
      : colorFor(s);

  const sharedGraphProps = {
    graphData: { nodes, links },
    width: containerSize.width,
    height: containerSize.height,
    nodeColor: nodeColor as (n: NodeObject3D<WikiGraphNode>) => string,
    nodeVal: (n: NodeObject3D<WikiGraphNode>) => (n as GraphNode).val,
    nodeLabel: nodeLabel as (n: NodeObject3D<WikiGraphNode>) => string,
    linkColor: linkColor as (l: LinkObject3D<WikiGraphNode>) => string,
    linkWidth: linkWidth as (l: LinkObject3D<WikiGraphNode>) => number,
    onNodeClick: onNodeClick as (n: NodeObject3D<WikiGraphNode>) => void,
    onNodeHover: (n: NodeObject3D<WikiGraphNode> | null) =>
      setHoveredId(n ? String(n.id) : null),
    onBackgroundClick,
    cooldownTicks: 120,
    nodeRelSize: 4,
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="flex items-center gap-2 text-lg font-bold">
          <Network className="h-5 w-5 text-indigo-500" />
          Wiki Graph
        </h2>
        <span className="text-sm text-zinc-500">
          {graph
            ? `${graph.nodes.length} node${graph.nodes.length === 1 ? "" : "s"} · ${links.length} edge${links.length === 1 ? "" : "s"}${crossLinks > 0 ? ` · ${crossLinks} cross` : ""}`
            : "—"}
        </span>
      </div>

      {/* Controls */}
      <div className="flex flex-wrap items-center gap-3">
        <select
          value={scope}
          onChange={(e) => loadScope(e.target.value, crossOn)}
          className={selectClass}
          aria-label="Graph scope"
        >
          {scopes.find((s) => s.kind === "all") ? (
            <option value={ALL_SCOPE}>
              Entire wiki (
              {scopes.find((s) => s.kind === "all")?.count ?? ""})
            </option>
          ) : null}
          <optgroup label="Projects">
            {scopes
              .filter((s) => s.kind === "project")
              .map((s) => (
                <option
                  key={s.key}
                  value={s.key}
                  disabled={s.count > MAX_SCOPE}
                >
                  {s.label} ({s.count})
                </option>
              ))}
          </optgroup>
          <optgroup label="Knowledge areas">
            {scopes
              .filter((s) => s.kind === "area")
              .map((s) => (
                <option
                  key={s.key}
                  value={s.key}
                  disabled={s.count > MAX_SCOPE}
                >
                  {s.label} ({s.count})
                </option>
              ))}
          </optgroup>
        </select>

        <label className="flex items-center gap-2 text-sm text-zinc-600 dark:text-zinc-300">
          Similarity ≥
          <input
            type="range"
            min={0}
            max={0.6}
            step={0.05}
            value={minScore}
            onChange={(e) => {
              const v = Number(e.target.value);
              setMinScore(v);
              syncUrl({ min: v });
            }}
            className="accent-indigo-500"
          />
          <span className="w-9 text-xs tabular-nums text-zinc-500">
            {minScore.toFixed(2)}
          </span>
        </label>

        {!macroMode ? (
          <label className="flex cursor-pointer items-center gap-1.5 text-sm text-zinc-600 dark:text-zinc-300">
            <input
              type="checkbox"
              checked={crossOn}
              onChange={(e) => toggleCross(e.target.checked)}
              className="accent-rose-500"
            />
            Cross-project links
          </label>
        ) : null}

        <form
          onSubmit={(e) => {
            e.preventDefault();
            focusNode();
          }}
          className="relative"
        >
          <SearchIcon className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-400" />
          <input
            value={focusQuery}
            onChange={(e) => setFocusQuery(e.target.value)}
            placeholder={macroMode ? "Find a project…" : "Find a page…"}
            className="w-56 rounded-md border border-zinc-300 bg-white py-1.5 pl-9 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 dark:border-zinc-700 dark:bg-zinc-900"
          />
        </form>

        {/* Edge comparison toggle */}
        <button
          type="button"
          onClick={() => setShowEdges((v) => !v)}
          aria-pressed={showEdges}
          className={`flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-xs font-medium transition-colors ${
            showEdges
              ? "border-indigo-300 bg-indigo-50 text-indigo-700 dark:border-indigo-700 dark:bg-indigo-950/50 dark:text-indigo-300"
              : "border-zinc-300 text-zinc-600 hover:bg-zinc-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
          }`}
        >
          <GitCompareArrows className="h-3.5 w-3.5" />
          Compare edges
          {edgeRows.length > 0 ? (
            <span className="rounded-full bg-zinc-200 px-1.5 py-0.5 text-[10px] font-semibold tabular-nums dark:bg-zinc-800">
              {edgeRows.length}
            </span>
          ) : null}
        </button>

        {/* 2D / 3D toggle */}
        <div className="ml-auto flex overflow-hidden rounded-md border border-zinc-300 dark:border-zinc-700">
          <button
            type="button"
            onClick={() => {
              setMode("2d");
              syncUrl({ mode: "2d" });
            }}
            className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium transition-colors ${
              mode === "2d"
                ? "bg-indigo-600 text-white"
                : "bg-white text-zinc-600 hover:bg-zinc-50 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:bg-zinc-800"
            }`}
          >
            <Network className="h-3.5 w-3.5" />
            2D
          </button>
          <button
            type="button"
            onClick={() => {
              setMode("3d");
              syncUrl({ mode: "3d" });
            }}
            className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium transition-colors ${
              mode === "3d"
                ? "bg-indigo-600 text-white"
                : "bg-white text-zinc-600 hover:bg-zinc-50 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:bg-zinc-800"
            }`}
          >
            <Box className="h-3.5 w-3.5" />
            3D
          </button>
        </div>
      </div>

      {/* Legend — macro mode gets counts, a size scale and the edge range */}
      {macroMode ? (
        <div className="flex flex-wrap items-center gap-x-6 gap-y-3 text-xs text-zinc-500">
          <div className="flex items-center gap-3">
            {[
              { label: "project", color: PROJECT_CLUSTER_COLOR },
              { label: "area", color: AREA_CLUSTER_COLOR },
            ].map(({ label, color }) => (
              <span key={label} className="inline-flex items-center gap-1.5">
                <span
                  className="h-2.5 w-2.5 rounded-full"
                  style={{ backgroundColor: color }}
                />
                {label}
                <span className="tabular-nums text-zinc-400">
                  {graph?.nodes.filter((n) => n.cluster && n.kind === label)
                    .length ?? 0}
                </span>
              </span>
            ))}
          </div>
          <div className="flex items-center gap-2">
            <span className="text-zinc-400">size = pages</span>
            {[
              { d: 10, label: "~5" },
              { d: 22, label: "~100" },
              { d: 34, label: "500+" },
            ].map(({ d, label }) => (
              <span key={label} className="inline-flex items-center gap-1">
                <span
                  className="rounded-full"
                  style={{
                    width: d,
                    height: d,
                    backgroundColor: "rgba(99,102,241,0.55)",
                  }}
                />
                {label}
              </span>
            ))}
          </div>
          <div className="flex items-center gap-2">
            <span className="text-zinc-400">edge similarity</span>
            <span
              className="h-2 w-24 rounded-full"
              style={{
                background:
                  "linear-gradient(90deg, rgba(99,102,241,0.15), rgba(99,102,241,0.95))",
              }}
            />
            <span className="tabular-nums text-zinc-400">0.05 → 0.8</span>
          </div>
        </div>
      ) : subfolders.size > 1 || crossLinks > 0 ? (
        <div className="flex flex-wrap items-center gap-3 text-xs text-zinc-500">
          {[...subfolders].sort().map((s) => (
            <span key={s} className="inline-flex items-center gap-1.5">
              <span
                className="h-2.5 w-2.5 rounded-full"
                style={{ backgroundColor: legendColor(s) }}
              />
              {s}
            </span>
          ))}
          {crossLinks > 0 ? (
            <span className="inline-flex items-center gap-1.5">
              <span
                className="h-2.5 w-2.5 rounded-full"
                style={{ backgroundColor: CROSS_COLOR }}
              />
              cross-project
            </span>
          ) : null}
        </div>
      ) : null}

      <div className="flex flex-col gap-4 lg:flex-row">
        <div
          ref={containerRef}
          className="relative h-[560px] flex-1 overflow-hidden rounded-lg border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-950"
        >
          {loading ? (
            <div className="absolute inset-0 z-10 flex items-center justify-center bg-white/60 dark:bg-zinc-950/60">
              <div className="animate-pulse text-sm text-zinc-400">
                Loading graph…
              </div>
            </div>
          ) : null}
          {error ? (
            <div className="absolute inset-x-3 top-3 z-10 mx-auto max-w-lg rounded-md border border-amber-200 bg-amber-50 px-4 py-2 text-xs text-amber-800 dark:border-amber-800 dark:bg-amber-900/20 dark:text-amber-200">
              {error}
            </div>
          ) : null}
          {!loading && graph && graph.nodes.length === 0 ? (
            <div className="absolute inset-0 flex items-center justify-center text-sm text-zinc-400">
              No pages in this scope
            </div>
          ) : null}
          {!loading &&
          !error &&
          graph &&
          graph.nodes.length > 0 &&
          containerSize.width > 0 ? (
            mode === "3d" ? (
              <ForceGraph3D
                {...sharedGraphProps}
                ref={graph3dRef}
                backgroundColor="#1b1b2f"
              />
            ) : (
              <ForceGraph2D
                {...sharedGraphProps}
                ref={graph2dRef}
                nodeCanvasObject={nodeCanvasObject}
                nodePointerAreaPaint={(node, color, ctx) => {
                  const n = node as GraphNode;
                  // Clusters are large targets — widen their hit area.
                  ctx.beginPath();
                  ctx.arc(
                    n.x ?? 0,
                    n.y ?? 0,
                    n.val + (macroMode ? 6 : 2),
                    0,
                    2 * Math.PI,
                  );
                  ctx.fillStyle = color;
                  ctx.fill();
                }}
                linkCanvasObject={linkCanvasObject}
              />
            )
          ) : null}
          {!loading &&
          graph &&
          graph.nodes.length > 0 &&
          links.length === 0 ? (
            <div className="absolute inset-x-3 bottom-3 z-10 mx-auto w-fit rounded-md bg-zinc-100 px-3 py-1 text-xs text-zinc-500 dark:bg-zinc-900">
              No edges at the current similarity threshold — lower the slider.
            </div>
          ) : null}
        </div>

        {/* Selected-node side panel (like openwiki's side-by-side reader) */}
        {selectedNode ? (
          <aside className="w-full shrink-0 space-y-3 rounded-lg border border-zinc-200 bg-white p-4 lg:w-80 dark:border-zinc-800 dark:bg-zinc-950">
            <div className="flex items-start justify-between gap-2">
              <h3 className="text-sm font-semibold leading-snug">
                {selectedNode.title}
              </h3>
              <button
                type="button"
                onClick={() => setSelectedId(null)}
                aria-label="Close panel"
                className="text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {selectedNode.cluster ? (
                <Badge variant="success">
                  {selectedNode.count ?? 0} pages
                </Badge>
              ) : (
                <Badge>{selectedNode.page_type}</Badge>
              )}
              {selectedNode.subfolder ? (
                <Badge variant="info">{selectedNode.subfolder}</Badge>
              ) : null}
              {selectedNode.cross ? (
                <Badge variant="error">cross-project</Badge>
              ) : null}
            </div>
            <p className="break-all font-mono text-[11px] text-zinc-400">
              {selectedNode.file_path}
            </p>
            {selectedNode.preview ? (
              <p className="line-clamp-5 text-xs text-zinc-500">
                {truncate(selectedNode.preview, 220)}
              </p>
            ) : null}
            {selectedNode.cluster ? (
              <button
                type="button"
                onClick={() => loadScope(selectedNode.id, false)}
                className="inline-flex items-center gap-1 rounded-md bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-indigo-700"
              >
                Explore cluster
                <ExternalLink className="h-3 w-3" />
              </button>
            ) : (
              <Link
                href={`/wiki/${selectedNode.id}`}
                className="inline-flex items-center gap-1 rounded-md bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-indigo-700"
              >
                Open page
                <ExternalLink className="h-3 w-3" />
              </Link>
            )}
            {compareNode ? (
              <div className="rounded-lg border border-zinc-200 bg-zinc-50/60 p-3 dark:border-zinc-800 dark:bg-zinc-900/40">
                <div className="mb-2 flex items-center justify-between gap-2">
                  <p className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-zinc-400">
                    <GitCompareArrows className="h-3 w-3" />
                    Similarity details
                  </p>
                  <button
                    type="button"
                    onClick={() => setCompareId(null)}
                    aria-label="Close comparison"
                    className="text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300"
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                </div>
                <p className="mb-3 text-center text-xs text-zinc-500">
                  <span className="font-semibold tabular-nums text-indigo-600 dark:text-indigo-400">
                    {(compareEdge?.score ?? 0).toFixed(2)}
                  </span>{" "}
                  similarity · {compareShared.length} shared terms
                </p>
                <div className="grid grid-cols-2 gap-3">
                  {[
                    { node: selectedNode, label: "A" },
                    { node: compareNode, label: "B" },
                  ].map(({ node, label }) => (
                    <div key={label} className="min-w-0">
                      <p
                        className="mb-1 truncate text-[11px] font-semibold text-zinc-600 dark:text-zinc-300"
                        title={node.title}
                      >
                        {label} · {node.title}
                      </p>
                      <ul className="space-y-1">
                        {(node.top_terms ?? []).slice(0, 6).map((t) => {
                          const shared = compareSharedSet.has(t.term);
                          return (
                            <li
                              key={t.term}
                              className="flex items-center gap-1.5"
                            >
                              <span className="h-1 w-10 shrink-0 overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-800">
                                <span
                                  className={`block h-full rounded-full ${shared ? "bg-indigo-500" : "bg-zinc-400/70"}`}
                                  style={{
                                    width: `${Math.round(
                                      Math.min(1, t.weight) * 100,
                                    )}%`,
                                  }}
                                />
                              </span>
                              <span
                                className={`truncate font-mono text-[10px] ${shared ? "font-semibold text-indigo-600 dark:text-indigo-400" : "text-zinc-500 dark:text-zinc-400"}`}
                              >
                                {t.term}
                              </span>
                            </li>
                          );
                        })}
                      </ul>
                    </div>
                  ))}
                </div>
                <p className="mt-2 text-[10px] text-zinc-400">
                  <span className="font-semibold text-indigo-500">indigo</span>{" "}
                  = shared vocabulary, weighted by the weaker side
                </p>
              </div>
            ) : null}
            {related.length > 0 ? (
              <div className="border-t border-zinc-200 pt-3 dark:border-zinc-800">
                <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-zinc-400">
                  <FolderTree className="h-3 w-3" />
                  Related docs
                </p>
                <ul className="space-y-1">
                  {related.map(({ node, score, cross, terms }) => (
                    <li key={node.id}>
                      {node.cluster ? (
                        <button
                          type="button"
                          onClick={() => loadScope(node.id, false)}
                          className="block w-full truncate text-left text-[13px] text-zinc-700 transition-colors hover:text-indigo-600 dark:text-zinc-300 dark:hover:text-indigo-400"
                          title={`${node.title} (similarity ${score.toFixed(2)})`}
                        >
                          {node.title}
                          <span className="ml-1 text-[11px] text-zinc-400">
                            ({node.count ?? 0})
                          </span>
                        </button>
                      ) : (
                        <div className="group flex items-center gap-1">
                          <Link
                            href={`/wiki/${node.id}`}
                            className="block min-w-0 flex-1 truncate text-[13px] text-zinc-700 transition-colors hover:text-indigo-600 dark:text-zinc-300 dark:hover:text-indigo-400"
                            title={`${node.title} (similarity ${score.toFixed(2)})${terms?.length ? ` · ${terms.join(" · ")}` : ""}${cross ? " · cross-project" : ""}`}
                          >
                            <span className={cross ? "text-rose-500" : undefined}>
                              {cross ? "↗ " : ""}
                              {node.title}
                            </span>
                          </Link>
                          <button
                            type="button"
                            onClick={() => setCompareId(node.id)}
                            aria-label={`Compare with ${node.title}`}
                            title="Compare shared terms"
                            className="shrink-0 rounded p-0.5 text-zinc-300 opacity-0 transition-opacity hover:bg-indigo-50 hover:text-indigo-600 group-hover:opacity-100 dark:text-zinc-600 dark:hover:bg-indigo-950 dark:hover:text-indigo-400"
                          >
                            <GitCompareArrows className="h-3.5 w-3.5" />
                          </button>
                        </div>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </aside>
        ) : null}
      </div>

      {/* Edge comparison view */}
      {showEdges ? (
        <div className="rounded-lg border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-950">
          <div className="flex flex-wrap items-center gap-3 border-b border-zinc-200 px-4 py-3 dark:border-zinc-800">
            <h3 className="flex items-center gap-1.5 text-sm font-semibold">
              <GitCompareArrows className="h-4 w-4 text-indigo-500" />
              Edge comparison
              {graph ? (
                <span className="text-xs font-normal text-zinc-400">
                  {graph.links.length} total
                </span>
              ) : null}
            </h3>
            <select
              value={edgeSort}
              onChange={(e) =>
                setEdgeSort(e.target.value as "score" | "source" | "target")
              }
              className={selectClass}
              aria-label="Sort edges"
            >
              <option value="score">Similarity (high → low)</option>
              <option value="source">Source A–Z</option>
              <option value="target">Target A–Z</option>
            </select>
            <input
              value={edgeFilter}
              onChange={(e) => setEdgeFilter(e.target.value)}
              placeholder="Filter by page or shared term…"
              className="w-60 rounded-md border border-zinc-300 bg-white py-1.5 pl-3 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 dark:border-zinc-700 dark:bg-zinc-900"
            />
            {focusEdge ? (
              <button
                type="button"
                onClick={() => setFocusEdge(null)}
                className="ml-auto text-xs font-medium text-indigo-600 hover:text-indigo-800 dark:text-indigo-400 dark:hover:text-indigo-300"
              >
                Clear highlight
              </button>
            ) : null}
          </div>
          {edgeRows.length === 0 ? (
            <p className="px-4 py-8 text-center text-sm text-zinc-400">
              {graph && graph.links.length === 0
                ? "No edges in this graph — lower the similarity threshold."
                : "No edges match the filter."}
            </p>
          ) : (
            <>
              <div className="max-h-96 overflow-auto">
                <table className="w-full text-left text-xs">
                  <thead className="sticky top-0 bg-zinc-50 text-zinc-500 dark:bg-zinc-900">
                    <tr>
                      <th className="px-4 py-2 font-medium">Source</th>
                      <th className="px-2 py-2 font-medium">Similarity</th>
                      <th className="px-2 py-2 font-medium">Target</th>
                      <th className="px-4 py-2 font-medium">Shared terms</th>
                    </tr>
                  </thead>
                  <tbody>
                    {visibleEdgeRows.map((r) => {
                      const focused =
                        focusEdge &&
                        ((focusEdge.s === r.s && focusEdge.t === r.t) ||
                          (focusEdge.s === r.t && focusEdge.t === r.s));
                      return (
                        <tr
                          key={`${r.s}→${r.t}`}
                          onClick={() => focusEdgeInGraph(r.s, r.t)}
                          className={`cursor-pointer border-t border-zinc-100 dark:border-zinc-800 ${
                            focused
                              ? "bg-amber-50 dark:bg-amber-950/20"
                              : "hover:bg-zinc-50 dark:hover:bg-zinc-900"
                          }`}
                          title="Click to highlight this edge in the graph"
                        >
                          <td className="max-w-56 truncate px-4 py-2 font-medium text-zinc-700 dark:text-zinc-300">
                            {r.sTitle}
                          </td>
                          <td className="px-2 py-2">
                            <div className="flex items-center gap-2">
                              <div className="h-1.5 w-14 overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-800">
                                <div
                                  className={`h-full rounded-full ${r.cross ? "bg-rose-500" : "bg-indigo-500"}`}
                                  style={{
                                    width: `${Math.round(
                                      Math.min(1, r.score) * 100,
                                    )}%`,
                                  }}
                                />
                              </div>
                              <span className="tabular-nums text-zinc-500">
                                {r.score.toFixed(2)}
                              </span>
                            </div>
                          </td>
                          <td className="max-w-56 truncate px-2 py-2 text-zinc-700 dark:text-zinc-300">
                            {r.cross ? (
                              <span className="text-rose-500">↗ </span>
                            ) : null}
                            {r.tTitle}
                          </td>
                          <td className="px-4 py-2">
                            <div className="flex flex-wrap items-center gap-1">
                              {r.terms.slice(0, 4).map((t) => (
                                <span
                                  key={t}
                                  className="rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400"
                                >
                                  {t}
                                </span>
                              ))}
                              {r.terms.length > 4 ? (
                                <span className="text-[10px] text-zinc-400">
                                  +{r.terms.length - 4}
                                </span>
                              ) : null}
                              {r.cross ? <Badge variant="error">cross</Badge> : null}
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              {edgeRows.length > MAX_EDGE_ROWS ? (
                <p className="border-t border-zinc-200 px-4 py-2 text-[11px] text-zinc-400 dark:border-zinc-800">
                  Showing {MAX_EDGE_ROWS} of {edgeRows.length} edges — refine
                  the filter to narrow down.
                </p>
              ) : null}
            </>
          )}
        </div>
      ) : null}

      <p className="text-xs text-zinc-400">
        <BookOpen className="mr-1 inline h-3 w-3" />
        Nodes are wiki pages; edges connect the most content-similar pairs
        (TF-IDF over the markdown bodies), labeled with their shared terms.
        <span className="font-medium text-indigo-600 dark:text-indigo-400">
          {" "}Entire wiki
        </span>{" "}
        clusters pages into one node per project/area — click a cluster to
        drill in. Enable{" "}
        <span className="font-medium text-rose-500">cross-project links</span>{" "}
        to also surface related pages found by full-text searching the whole
        wiki&apos;s chunks. Drag to move, scroll to zoom, click a node for
        details — or open{" "}
        <span className="font-medium text-zinc-500">Compare edges</span> to
        rank every connection by similarity and click a row to jump to it in
        the graph.
      </p>
    </div>
  );
}
