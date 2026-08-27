import { unstable_noStore as noStore } from "next/cache";
import { AppShell } from "@/components/layout/app-shell";
import { ALL_PAGES_LIMIT } from "@/lib/api";
import { WikiListClient } from "./wiki-list-client";

export interface WikiPageItem {
  page_id: string;
  file_path: string;
  title: string;
  page_type: string;
  domain?: string | null;
  status: string;
  updated_at?: string | null;
  markdown_preview?: string | null;
}

interface WikiListResult {
  pages: WikiPageItem[];
  error: string | null;
}

async function fetchWikiPages(): Promise<WikiListResult> {
  const env = process.env.NEXT_PUBLIC_API_URL;
  if (!env) {
    return { pages: [], error: "API URL not configured" };
  }
  const token = process.env.DASHBOARD_API_TOKEN ?? "";
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  try {
    const res = await fetch(
      `${env.replace(/\/+$/, "")}/wiki/pages?limit=${ALL_PAGES_LIMIT}`,
      { cache: "no-store", headers },
    );
    if (!res.ok) {
      return {
        pages: [],
        error:
          res.status === 401 || res.status === 403
            ? "The API rejected the dashboard token (401/403). Check DASHBOARD_API_TOKEN."
            : `The API returned an error (HTTP ${res.status}).`,
      };
    }
    return { pages: (await res.json()) as WikiPageItem[], error: null };
  } catch {
    return {
      pages: [],
      error: "Could not reach the API server. Check NEXT_PUBLIC_API_URL.",
    };
  }
}

export default async function WikiListPage() {
  noStore();
  const { pages, error } = await fetchWikiPages();

  return (
    <AppShell>
      <WikiListClient initialPages={pages} initialError={error} />
    </AppShell>
  );
}
