"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import type {
  ForceGraphMethods as ForceGraphMethods2D,
  ForceGraphProps as ForceGraphProps2D,
  LinkObject as LinkObject2D,
  NodeObject as NodeObject2D,
} from "react-force-graph-2d";
import type {
  GraphragEntity,
  GraphragRelationship,
  GraphragCommunity,
} from "@/lib/types";

// Canvas force graph — client-side only (no canvas on server).
const ForceGraph2D = dynamic(() => import("react-force-graph-2d"), {
  ssr: false,
}) as unknown as React.ComponentType<
  ForceGraphProps2D<GraphNode> & {
    ref?: React.MutableRefObject<ForceGraphMethods2D<GraphNode> | undefined>;
  }
>;

const ENTITY_COLORS: Record<string, string> = {
  person: "#3b82f6",
  org: "#a855f7",
  concept: "#6366f1",
  location: "#10b981",
  technology: "#f59e0b",
  event: "#f43f5e",
};

interface GraphNode {
  id: string;
  name: string;
  entity_type: string;
  description: string;
  frequency: number;
  val: number;
  color: string;
  label: string;
  x?: number;
  y?: number;
}

interface GraphLink {
  source: string;
  target: string;
  relationship_type: string;
  description: string;
  weight: number;
}

interface Props {
  entities: GraphragEntity[];
  relationships: GraphragRelationship[];
  communities: GraphragCommunity[];
}

export function KnowledgeGraphViz({ entities, relationships, communities }: Props) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [containerSize, setContainerSize] = useState({ width: 0, height: 0 });
  const containerRef = useRef<HTMLDivElement | null>(null);
  const graphRef = useRef<ForceGraphMethods2D<GraphNode> | undefined>(undefined);

  // ResizeObserver to measure the canvas container
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      setContainerSize({ width: el.clientWidth, height: el.clientHeight });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Fit layout to viewport when data changes
  useEffect(() => {
    if (entities.length === 0) return;
    const t = setTimeout(() => {
      graphRef.current?.zoomToFit(400, 60);
    }, 350);
    return () => clearTimeout(t);
  }, [entities, relationships]);

  // Build graph data
  const { nodes, links } = useMemo(() => {
    // Build degree map for sizing
    const degree = new Map<string, number>();
    for (const r of relationships) {
      degree.set(r.source, (degree.get(r.source) ?? 0) + 1);
      degree.set(r.target, (degree.get(r.target) ?? 0) + 1);
    }

    const nodes: GraphNode[] = entities.map((e) => {
      const deg = degree.get(e.name) ?? 0;
      return {
        id: e.entity_id,
        name: e.name,
        entity_type: e.entity_type,
        description: e.description,
        frequency: e.frequency,
        val: Math.min(4 + deg * 1.5 + Math.log2(e.frequency + 1), 20),
        color: ENTITY_COLORS[e.entity_type] ?? "#71717a",
        label: e.name,
      };
    });

    // Build a name→id lookup for linking
    const nameToId = new Map<string, string>();
    for (const n of nodes) {
      nameToId.set(n.name.toLowerCase(), n.id);
    }

    const links: GraphLink[] = relationships
      .filter((r) => {
        const sid = nameToId.get(r.source.toLowerCase());
        const tid = nameToId.get(r.target.toLowerCase());
        return sid && tid;
      })
      .map((r) => ({
        source: nameToId.get(r.source.toLowerCase())!,
        target: nameToId.get(r.target.toLowerCase())!,
        relationship_type: r.relationship_type,
        description: r.description,
        weight: r.weight,
      }));

    return { nodes, links };
  }, [entities, relationships]);

  // Neighbor set for the selected node
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
    (n: NodeObject2D<GraphNode>) => {
      const gn = n as unknown as GraphNode;
      if (selectedId && n.id !== selectedId && !neighborIds.has(n.id as string)) {
        return "rgba(140,140,150,0.18)";
      }
      return gn.color;
    },
    [selectedId, neighborIds],
  );

  const linkColor = useCallback(
    (l: LinkObject2D<GraphNode>) => {
      const weight = (l as unknown as GraphLink).weight ?? 0.5;
      const isIncident =
        selectedId &&
        ((l as unknown as GraphLink).source === selectedId ||
          (l as unknown as GraphLink).target === selectedId);
      if (isIncident) {
        return `rgba(99,102,241,${0.4 + weight * 0.6})`;
      }
      return `rgba(140,140,150,${0.1 + weight * 0.3})`;
    },
    [selectedId],
  );

  const linkWidth = useCallback((l: LinkObject2D<GraphNode>) => {
    return Math.max(0.5, ((l as unknown as GraphLink).weight ?? 0.5) * 2.5);
  }, []);

  const nodeLabel = useCallback((n: NodeObject2D<GraphNode>) => {
    const gn = n as unknown as GraphNode;
    return `${gn.name} (${gn.entity_type})`;
  }, []);

  const showLabels = nodes.length <= 80;

  const nodeCanvasObject = useCallback(
    (node: NodeObject2D<GraphNode>, ctx: CanvasRenderingContext2D, globalScale: number) => {
      const n = node as unknown as GraphNode;
      const x = n.x ?? 0;
      const y = n.y ?? 0;

      // Draw node circle
      ctx.beginPath();
      ctx.arc(x, y, n.val, 0, 2 * Math.PI);
      ctx.fillStyle = nodeColor(node);
      ctx.fill();

      // Highlight ring for selected/hovered
      if (n.id === selectedId) {
        ctx.strokeStyle = "rgba(255,255,255,0.95)";
        ctx.lineWidth = 2 / globalScale;
        ctx.beginPath();
        ctx.arc(x, y, n.val + 2, 0, 2 * Math.PI);
        ctx.stroke();
      }
      if (n.id === hoveredId) {
        ctx.strokeStyle = "rgba(255,255,255,0.6)";
        ctx.lineWidth = 1.5 / globalScale;
        ctx.beginPath();
        ctx.arc(x, y, n.val + 4, 0, 2 * Math.PI);
        ctx.stroke();
      }

      // Label
      if (showLabels && globalScale > 0.4) {
        const label = n.label.length > 24 ? `${n.label.slice(0, 22)}…` : n.label;
        ctx.font = `${11 / globalScale}px Inter, system-ui, sans-serif`;
        const isMuted =
          selectedId && n.id !== selectedId && !neighborIds.has(n.id as string);
        ctx.fillStyle = isMuted
          ? "rgba(113,113,122,0.3)"
          : "rgba(113,113,122,0.9)";
        ctx.fillText(label, x + n.val + 3 / globalScale, y + 3 / globalScale);
      }
    },
    [nodeColor, selectedId, hoveredId, neighborIds, showLabels],
  );

  const onNodeClick = useCallback((node: NodeObject2D<GraphNode>) => {
    setSelectedId((prev) => (prev === (node as unknown as GraphNode).id ? null : (node as unknown as GraphNode).id));
  }, []);

  const onBackgroundClick = useCallback(() => {
    setSelectedId(null);
  }, []);

  // Selected entity details
  const selectedEntity = useMemo(
    () => entities.find((e) => e.entity_id === selectedId) ?? null,
    [entities, selectedId],
  );

  const selectedRelationships = useMemo(() => {
    if (!selectedEntity) return [];
    return relationships.filter(
      (r) =>
        r.source.toLowerCase() === selectedEntity.name.toLowerCase() ||
        r.target.toLowerCase() === selectedEntity.name.toLowerCase(),
    );
  }, [relationships, selectedEntity]);

  const selectedCommunities = useMemo(() => {
    if (!selectedEntity) return [];
    return communities.filter((c) =>
      c.member_entities.some(
        (m) => m.toLowerCase() === selectedEntity.name.toLowerCase(),
      ),
    );
  }, [communities, selectedEntity]);

  const sharedGraphProps = {
    graphData: { nodes, links },
    width: containerSize.width,
    height: containerSize.height,
    nodeColor: nodeColor as (n: NodeObject2D<GraphNode>) => string,
    nodeVal: (n: NodeObject2D<GraphNode>) => (n as unknown as GraphNode).val,
    nodeLabel: nodeLabel as (n: NodeObject2D<GraphNode>) => string,
    linkColor: linkColor as (l: LinkObject2D<GraphNode>) => string,
    linkWidth: linkWidth as (l: LinkObject2D<GraphNode>) => number,
    onNodeClick: onNodeClick as (n: NodeObject2D<GraphNode>) => void,
    onNodeHover: (n: NodeObject2D<GraphNode> | null) =>
      setHoveredId(n ? String(n.id) : null),
    onBackgroundClick,
    cooldownTicks: 100,
    nodeRelSize: 4,
  };

  if (entities.length === 0) {
    return (
      <div className="flex h-[400px] items-center justify-center text-sm text-zinc-400">
        No entities to visualize. Run the graphrag stage first.
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4 lg:flex-row">
      <div
        ref={containerRef}
        className="relative h-[560px] flex-1 overflow-hidden rounded-lg border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-950"
      >
        {containerSize.width > 0 && (
          <ForceGraph2D
            {...sharedGraphProps}
            ref={graphRef}
            nodeCanvasObject={nodeCanvasObject}
            nodePointerAreaPaint={(node, color, ctx) => {
              const n = node as unknown as GraphNode;
              ctx.beginPath();
              ctx.arc(n.x ?? 0, n.y ?? 0, n.val + 2, 0, 2 * Math.PI);
              ctx.fillStyle = color;
              ctx.fill();
            }}
          />
        )}
        <div className="absolute bottom-2 left-2 rounded bg-white/80 px-2 py-1 text-[10px] text-zinc-500 dark:bg-zinc-950/80">
          {nodes.length} nodes · {links.length} edges
        </div>
      </div>

      {/* Selected entity side panel */}
      {selectedEntity && (
        <aside className="w-full shrink-0 space-y-3 rounded-lg border border-zinc-200 bg-white p-4 lg:w-80 dark:border-zinc-800 dark:bg-zinc-950">
          <div className="flex items-start justify-between gap-2">
            <h3 className="text-sm font-semibold leading-snug">
              {selectedEntity.name}
            </h3>
            <button
              type="button"
              onClick={() => setSelectedId(null)}
              className="text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300"
            >
              ✕
            </button>
          </div>
          <div className="flex flex-wrap gap-1.5">
            <span
              className={`inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium ${ENTITY_COLORS[selectedEntity.entity_type] ? "text-white" : ""}`}
              style={{ backgroundColor: ENTITY_COLORS[selectedEntity.entity_type] ?? "#71717a" }}
            >
              {selectedEntity.entity_type}
            </span>
            <span className="inline-flex items-center rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400">
              mentioned {selectedEntity.frequency}×
            </span>
          </div>
          {selectedEntity.description && (
            <p className="text-xs text-zinc-500">{selectedEntity.description}</p>
          )}
          {selectedRelationships.length > 0 && (
            <div className="border-t border-zinc-200 pt-3 dark:border-zinc-800">
              <p className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-zinc-400">
                Relationships ({selectedRelationships.length})
              </p>
              <ul className="space-y-1.5">
                {selectedRelationships.map((r) => {
                  const other =
                    r.source.toLowerCase() === selectedEntity.name.toLowerCase()
                      ? r.target
                      : r.source;
                  const direction =
                    r.source.toLowerCase() === selectedEntity.name.toLowerCase()
                      ? "→"
                      : "←";
                  return (
                    <li
                      key={r.rel_id}
                      className="text-[13px] text-zinc-600 dark:text-zinc-400"
                    >
                      <span className="text-zinc-400">{direction}</span>{" "}
                      <span className="font-medium">{other}</span>{" "}
                      <span className="text-zinc-400">({r.relationship_type})</span>
                    </li>
                  );
                })}
              </ul>
            </div>
          )}
          {selectedCommunities.length > 0 && (
            <div className="border-t border-zinc-200 pt-3 dark:border-zinc-800">
              <p className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-zinc-400">
                Communities ({selectedCommunities.length})
              </p>
              <ul className="space-y-2">
                {selectedCommunities.map((c) => (
                  <li
                    key={c.community_id}
                    className="rounded-md bg-zinc-50 p-2 dark:bg-zinc-900/50"
                  >
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-[13px] font-medium text-zinc-700 dark:text-zinc-300">
                        {c.title}
                      </span>
                      <span className="text-[10px] text-zinc-400">
                        L{c.level}
                      </span>
                    </div>
                    {c.summary && (
                      <p className="text-[11px] text-zinc-500 line-clamp-2">
                        {c.summary}
                      </p>
                    )}
                    <div className="mt-1 flex flex-wrap gap-1">
                      {c.member_entities.slice(0, 5).map((m) => (
                        <span
                          key={m}
                          className={`rounded px-1 py-0.5 text-[9px] ${
                            m.toLowerCase() === selectedEntity.name.toLowerCase()
                              ? "bg-indigo-100 font-semibold text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300"
                              : "bg-zinc-100 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400"
                          }`}
                        >
                          {m}
                        </span>
                      ))}
                      {c.member_entities.length > 5 && (
                        <span className="text-[9px] text-zinc-400">
                          +{c.member_entities.length - 5}
                        </span>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </aside>
      )}
    </div>
  );
}


