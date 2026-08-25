import Link from "next/link";
import { unstable_noStore as noStore } from "next/cache";
import { AppShell } from "@/components/layout/app-shell";
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

async function fetchWikiPages(limit: number): Promise<WikiPageItem[]> {
  const env = process.env.NEXT_PUBLIC_API_URL;
  if (!env) return [];
  const token = process.env.DASHBOARD_API_TOKEN ?? "";
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  try {
    const res = await fetch(`${env.replace(/\/+$/, "")}/wiki/pages?limit=${limit}`, {
      cache: "no-store",
      headers,
    });
    if (!res.ok) return [];
    return (await res.json()) as WikiPageItem[];
  } catch {
    return [];
  }
}

export default async function WikiListPage({
  searchParams,
}: {
  searchParams?: Promise<{ limit?: string }>;
}) {
  noStore();
  const params = (await searchParams) ?? {};
  const limitRaw = parseInt(params.limit ?? "50", 10);
  const limit = [25, 50, 100].includes(limitRaw) ? limitRaw : 50;
  const pages = await fetchWikiPages(limit);

  return (
    <AppShell>
      <WikiListClient initialPages={pages} initialLimit={limit} />
    </AppShell>
  );
}
