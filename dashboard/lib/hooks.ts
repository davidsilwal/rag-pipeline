"use client";

import useSWR from "swr";
import { useAuth } from "@/lib/auth";
import { getApiClient } from "@/lib/api";

const FALLBACK_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";

export function useApi() {
  const { token, apiUrl } = useAuth();
  return getApiClient(apiUrl || FALLBACK_URL, token);
}

function asArray<T>(data: T | T[] | undefined | null): T[] {
  if (Array.isArray(data)) return data;
  return [];
}

export function useHealth() {
  const api = useApi();
  return useSWR("health", () => api.getHealth(), {
    refreshInterval: 15_000,
    revalidateOnFocus: true,
  });
}

export function useMetrics() {
  const api = useApi();
  return useSWR("metrics", () => api.getMetrics(), {
    refreshInterval: 15_000,
  });
}

export function useAlerts() {
  const api = useApi();
  return useSWR("alerts", () => api.getAlerts(), {
    refreshInterval: 15_000,
  });
}

export function useSources(status?: string, limit?: number) {
  const api = useApi();
  const key = `sources:${status || ""}:${limit || ""}`;
  const swr = useSWR(key, () => api.listSources({ status, limit }), {
    refreshInterval: 30_000,
  });
  return { ...swr, data: asArray(swr.data) };
}

export function useSource(id: string | null) {
  const api = useApi();
  return useSWR(id ? `source:${id}` : null, () => api.getSource(id!));
}

export function useUnits(sourceId?: string, limit?: number) {
  const api = useApi();
  const key = `units:${sourceId || ""}:${limit || ""}`;
  const swr = useSWR(key, () => api.listUnits({ source_id: sourceId, limit }), {
    refreshInterval: 30_000,
  });
  return { ...swr, data: asArray(swr.data) };
}

export function useWikiPages(limit?: number) {
  const api = useApi();
  const swr = useSWR(`wiki-pages:${limit || ""}`, () => api.listWikiPages(limit), {
    refreshInterval: 60_000,
  });
  return { ...swr, data: asArray(swr.data) };
}

export function useWikiPage(id: string | null) {
  const api = useApi();
  return useSWR(id ? `wiki-page:${id}` : null, () => api.getWikiPage(id!));
}

export function useWikiChunks(pageId?: string, limit?: number) {
  const api = useApi();
  const swr = useSWR(
    pageId ? `wiki-chunks:${pageId}:${limit || ""}` : null,
    () => api.listWikiChunks({ page_id: pageId, limit }),
  );
  return { ...swr, data: asArray(swr.data) };
}

export function useWorkers() {
  const api = useApi();
  const swr = useSWR("workers", () => api.listWorkers(), {
    refreshInterval: 15_000,
  });
  return { ...swr, data: asArray(swr.data) };
}

export function useWorker(id: string | null) {
  const api = useApi();
  return useSWR(id ? `worker:${id}` : null, () => api.getWorker(id!));
}

export function useTasks(params?: {
  stage?: string;
  status?: string;
  worker_id?: string;
  limit?: number;
  offset?: number;
}) {
  const api = useApi();
  const key = `tasks:${JSON.stringify(params || {})}`;
  const swr = useSWR(key, () => api.listTasks(params), {
    refreshInterval: 10_000,
  });
  const raw = swr.data;
  const data = raw && typeof raw === "object" && "tasks" in raw ? raw.tasks : asArray(raw);
  const total = raw && typeof raw === "object" && "total" in raw ? raw.total : (data?.length ?? 0);
  return { ...swr, data, total };
}

export function useJobs() {
  const api = useApi();
  const swr = useSWR("jobs", () => api.listJobs(), {
    refreshInterval: 60_000,
  });
  return { ...swr, data: asArray(swr.data) };
}
