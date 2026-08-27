import { unstable_noStore as noStore } from "next/cache";
import { AppShell } from "@/components/layout/app-shell";
import { ALL_PAGES_LIMIT } from "@/lib/api";
import type { WikiGraphResponse } from "@/lib/types";
import { WikiGraphClient } from "./wiki-graph-client";

export interface GraphScopeOption {
  key: string;
  label: string;
  count: number;
  kind: "all" | "project" | "area";
}

interface GraphPageData {
  scopes: GraphScopeOption[];
  graph: WikiGraphResponse | null;
  graphError: string | null;
  defaultScope: string | null;
  loadError: string | null;
  initialCross: boolean;
  initialMinScore: number;
  initialMode: "2d" | "3d";
}

async function fetchGraphData(
  params: Record<string, string | undefined>,
): Promise<GraphPageData> {
  // URL state so graph views are shareable: ?scope=&cross=&min=&mode=
  const scopeParam = params.scope;
  const rawMin = Number.parseFloat(params.min ?? "");
  const initialMinScore = Number.isFinite(rawMin)
    ? Math.min(0.6, Math.max(0, rawMin))
    : 0.1;
  const initialCross = params.cross === "1" || params.cross === "true";
  const initialMode: "2d" | "3d" = params.mode === "3d" ? "3d" : "2d";
  const env = process.env.NEXT_PUBLIC_API_URL;
  if (!env) {
    return {
      scopes: [],
      graph: null,
      graphError: null,
      defaultScope: null,
      loadError: "API URL not configured",
      initialCross,
      initialMinScore,
      initialMode,
    };
  }
  const base = env.replace(/\/+$/, "");
  const token = process.env.DASHBOARD_API_TOKEN ?? "";
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;

  try {
    const res = await fetch(
      `${base}/wiki/pages?limit=${ALL_PAGES_LIMIT}`,
      { cache: "no-store", headers },
    );
    if (!res.ok) {
      return {
        scopes: [],
        graph: null,
        graphError: null,
        defaultScope: null,
        loadError: `The API returned an error (HTTP ${res.status}).`,
        initialCross,
        initialMinScore,
        initialMode,
      };
    }
    const pages = (await res.json()) as { file_path: string }[];

    // Derive browsable scopes (projects + knowledge areas) from file paths.
    const projects = new Map<string, number>();
    const areas = new Map<string, number>();
    for (const p of pages) {
      const parts = (p.file_path ?? "").split("/").filter(Boolean);
      if (parts.length === 0) continue;
      if (parts[0] === "projects" && parts.length >= 2) {
        projects.set(parts[1], (projects.get(parts[1]) ?? 0) + 1);
      } else {
        areas.set(parts[0], (areas.get(parts[0]) ?? 0) + 1);
      }
    }
    const scopes: GraphScopeOption[] = [
      {
        key: "all",
        label: "Entire wiki",
        count: pages.length,
        kind: "all",
      },
      ...[...projects.entries()]
        .sort((a, b) => b[1] - a[1])
        .map(([label, count]) => ({
          key: `projects/${label}`,
          label,
          count,
          kind: "project" as const,
        })),
      ...[...areas.entries()]
        .sort((a, b) => b[1] - a[1])
        .map(([label, count]) => ({
          key: label,
          label,
          count,
          kind: "area" as const,
        })),
    ];

    const chosen =
      scopes.find((s) => s.key === scopeParam) ??
      scopes.find((s) => s.kind === "project") ??
      scopes[0] ??
      null;

    let graph: WikiGraphResponse | null = null;
    let graphError: string | null = null;
    if (chosen) {
      // Cross-project links are expensive (per-page FTS queries), so the SSR
      // fetch skips them; the client refetches with cross when the share link
      // requests it. This keeps page load fast.
      const gres = await fetch(
        `${base}/wiki/graph?scope=${encodeURIComponent(chosen.key)}&top_k=4&min_score=${initialMinScore}`,
        { cache: "no-store", headers },
      );
      if (gres.ok) {
        graph = (await gres.json()) as WikiGraphResponse;
      } else {
        const body = await gres.json().catch(() => ({}));
        graphError =
          (body as { detail?: string }).detail ??
          `The graph API returned an error (HTTP ${gres.status}).`;
      }
    }

    return {
      scopes,
      graph,
      graphError,
      defaultScope: chosen?.key ?? null,
      loadError: null,
      initialCross,
      initialMinScore,
      initialMode,
    };
  } catch {
    return {
      scopes: [],
      graph: null,
      graphError: null,
      defaultScope: null,
      loadError: "Could not reach the API server. Check NEXT_PUBLIC_API_URL.",
      initialCross,
      initialMinScore,
      initialMode,
    };
  }
}

export default async function WikiGraphPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  noStore();
  const params = await searchParams;
  const data = await fetchGraphData(params);

  return (
    <AppShell>
      <WikiGraphClient
        scopes={data.scopes}
        initialGraph={data.graph}
        initialGraphError={data.graphError}
        defaultScope={data.defaultScope}
        loadError={data.loadError}
        initialCross={data.initialCross}
        initialMinScore={data.initialMinScore}
        initialMode={data.initialMode}
      />
    </AppShell>
  );
}
