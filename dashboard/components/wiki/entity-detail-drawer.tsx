"use client";

import { useEffect, useCallback } from "react";
import { X, Users, GitBranch, BookOpen } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type {
  GraphragEntity,
  GraphragRelationship,
  GraphragCommunity,
} from "@/lib/types";

const ENTITY_COLORS: Record<string, string> = {
  person: "#3b82f6",
  org: "#a855f7",
  concept: "#6366f1",
  location: "#10b981",
  technology: "#f59e0b",
  event: "#f43f5e",
};

function entityTypeColor(t: string) {
  return ENTITY_COLORS[t] ?? "#71717a";
}

interface Props {
  entity: GraphragEntity | null;
  relationships: GraphragRelationship[];
  communities: GraphragCommunity[];
  onClose: () => void;
}

/**
 * Slide-in drawer that shows full details for a single entity: its metadata,
 * all relationships (both directions), and community memberships.
 */
export function EntityDetailDrawer({
  entity,
  relationships,
  communities,
  onClose,
}: Props) {
  // Escape closes the drawer
  const onKey = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    },
    [onClose],
  );

  useEffect(() => {
    if (!entity) return;
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [entity, onKey]);

  if (!entity) return null;

  const entityRels = relationships.filter(
    (r) =>
      r.source.toLowerCase() === entity.name.toLowerCase() ||
      r.target.toLowerCase() === entity.name.toLowerCase(),
  );

  const entityCommunities = communities.filter((c) =>
    c.member_entities.some(
      (m) => m.toLowerCase() === entity.name.toLowerCase(),
    ),
  );

  // Group relationships by type
  const outgoing = entityRels.filter(
    (r) => r.source.toLowerCase() === entity.name.toLowerCase(),
  );
  const incoming = entityRels.filter(
    (r) => r.target.toLowerCase() === entity.name.toLowerCase(),
  );

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-40 bg-black/40"
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Drawer */}
      <div className="fixed inset-y-0 right-0 z-50 flex w-full max-w-md flex-col border-l border-zinc-200 bg-white shadow-xl dark:border-zinc-800 dark:bg-zinc-950">
        {/* Header */}
        <div className="flex items-start justify-between gap-3 border-b border-zinc-200 px-5 py-4 dark:border-zinc-800">
          <div className="min-w-0">
            <h2 className="text-base font-bold leading-snug">{entity.name}</h2>
            <div className="mt-1 flex items-center gap-2">
              <span
                className="inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-semibold text-white"
                style={{ backgroundColor: entityTypeColor(entity.entity_type) }}
              >
                {entity.entity_type}
              </span>
              <span className="text-xs text-zinc-400">
                mentioned {entity.frequency}×
              </span>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="shrink-0 rounded-md p-1.5 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-600 dark:hover:bg-zinc-800 dark:hover:text-zinc-300"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-5">
          {/* Description */}
          {entity.description && (
            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-zinc-400 mb-1.5">
                Description
              </p>
              <p className="text-sm text-zinc-600 dark:text-zinc-400">
                {entity.description}
              </p>
            </div>
          )}

          {/* Outgoing relationships */}
          {outgoing.length > 0 && (
            <div>
              <p className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-zinc-400 mb-2">
                <GitBranch className="h-3 w-3" />
                Outgoing ({outgoing.length})
              </p>
              <ul className="space-y-1.5">
                {outgoing.map((r) => (
                  <li
                    key={r.rel_id}
                    className="flex items-start gap-2 rounded-md bg-zinc-50 px-3 py-2 dark:bg-zinc-900/50"
                  >
                    <span className="mt-0.5 text-zinc-400">→</span>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-1.5">
                        <span className="text-sm font-medium">{r.target}</span>
                        <span
                          className="inline-flex items-center rounded px-1 py-0.5 text-[9px] font-medium text-white"
                          style={{ backgroundColor: entityTypeColor(r.target_type) }}
                        >
                          {r.target_type}
                        </span>
                      </div>
                      <div className="mt-0.5 flex items-center gap-2">
                        <Badge>{r.relationship_type}</Badge>
                        {r.description && (
                          <span className="text-[11px] text-zinc-500 truncate">
                            {r.description}
                          </span>
                        )}
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Incoming relationships */}
          {incoming.length > 0 && (
            <div>
              <p className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-zinc-400 mb-2">
                <GitBranch className="h-3 w-3 rotate-180" />
                Incoming ({incoming.length})
              </p>
              <ul className="space-y-1.5">
                {incoming.map((r) => (
                  <li
                    key={r.rel_id}
                    className="flex items-start gap-2 rounded-md bg-zinc-50 px-3 py-2 dark:bg-zinc-900/50"
                  >
                    <span className="mt-0.5 text-zinc-400">←</span>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-1.5">
                        <span className="text-sm font-medium">{r.source}</span>
                        <span
                          className="inline-flex items-center rounded px-1 py-0.5 text-[9px] font-medium text-white"
                          style={{ backgroundColor: entityTypeColor(r.source_type) }}
                        >
                          {r.source_type}
                        </span>
                      </div>
                      <div className="mt-0.5 flex items-center gap-2">
                        <Badge>{r.relationship_type}</Badge>
                        {r.description && (
                          <span className="text-[11px] text-zinc-500 truncate">
                            {r.description}
                          </span>
                        )}
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {entityRels.length === 0 && (
            <p className="text-sm text-zinc-400">No relationships found.</p>
          )}

          {/* Communities */}
          {entityCommunities.length > 0 && (
            <div>
              <p className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-zinc-400 mb-2">
                <BookOpen className="h-3 w-3" />
                Communities ({entityCommunities.length})
              </p>
              <div className="space-y-2">
                {entityCommunities.map((c) => (
                  <div
                    key={c.community_id}
                    className="rounded-md border border-zinc-200 bg-zinc-50/60 p-3 dark:border-zinc-800 dark:bg-zinc-900/40"
                  >
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-sm font-medium text-zinc-700 dark:text-zinc-300">
                        {c.title}
                      </span>
                      <span className="text-[10px] text-zinc-400">Level {c.level}</span>
                    </div>
                    {c.summary && (
                      <p className="text-xs text-zinc-500 mb-2 line-clamp-2">
                        {c.summary}
                      </p>
                    )}
                    <div className="flex flex-wrap gap-1">
                      {c.member_entities.slice(0, 8).map((m) => (
                        <span
                          key={m}
                          className={`rounded px-1.5 py-0.5 text-[10px] ${
                            m.toLowerCase() === entity.name.toLowerCase()
                              ? "bg-indigo-100 font-semibold text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300"
                              : "bg-zinc-100 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400"
                          }`}
                        >
                          {m}
                        </span>
                      ))}
                      {c.member_entities.length > 8 && (
                        <span className="text-[10px] text-zinc-400">
                          +{c.member_entities.length - 8}
                        </span>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {entityCommunities.length === 0 && entityRels.length > 0 && (
            <p className="text-sm text-zinc-400">
              Not a member of any community.
            </p>
          )}
        </div>

        {/* Footer */}
        <div className="border-t border-zinc-200 px-5 py-3 dark:border-zinc-800">
          <div className="flex items-center gap-2 text-[11px] text-zinc-400">
            <Users className="h-3 w-3" />
            <span>{entityRels.length} relationship{entityRels.length === 1 ? "" : "s"}</span>
            <span>·</span>
            <BookOpen className="h-3 w-3" />
            <span>{entityCommunities.length} communit{entityCommunities.length === 1 ? "y" : "ies"}</span>
          </div>
        </div>
      </div>
    </>
  );
}
