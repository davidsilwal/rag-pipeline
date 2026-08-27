import Link from "next/link";
import { unstable_noStore as noStore } from "next/cache";
import { ArrowLeft, RefreshCw } from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { WikiPageContentServer } from "./wiki-page-content-server";
import { RefreshButton } from "./refresh-button";

interface WikiApiPage {
  page_id: string;
  file_path: string;
  title: string;
  page_type: string;
  domain?: string | null;
  status: string;
  frontmatter?: Record<string, unknown>;
  markdown_body: string;
  source_unit_ids?: string[];
  git_commit_sha?: string | null;
  last_verified_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

interface WikiDetail {
  source_id?: string;
  source_status?: string;
  message?: string;
}

export interface WikiSibling {
  page_id: string;
  file_path: string;
  title: string;
  updated_at?: string | null;
}

// Sibling pages in the same folder as the current page (used by the
// "More in …" sidebar on the reader).
async function fetchSiblingPages(
  base: string,
  prefix: string,
  token: string,
  excludeId: string,
): Promise<WikiSibling[]> {
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  try {
    const res = await fetch(
      `${base}/wiki/pages?limit=50&prefix=${encodeURIComponent(prefix)}`,
      { cache: "no-store", headers },
    );
    if (!res.ok) return [];
    const pages = (await res.json()) as WikiSibling[];
    return pages.filter((p) => p.page_id !== excludeId);
  } catch {
    return [];
  }
}

function isUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
    value,
  );
}

async function tryFetch(
  base: string,
  url: string,
  token: string,
): Promise<{ ok: true; page: WikiApiPage } | { ok: false; detail: WikiDetail | null }> {
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  try {
    const res = await fetch(url, { cache: "no-store", headers });
    if (res.ok) {
      const data = (await res.json()) as WikiApiPage;
      return { ok: true, page: data };
    }
    if (res.status === 404) {
      const body = (await res.json().catch(() => ({}))) as {
        detail?: WikiDetail;
      };
      return { ok: false, detail: body.detail ?? null };
    }
    return { ok: false, detail: null };
  } catch {
    return { ok: false, detail: null };
  }
}

async function fetchWikiPage(
  slug: string,
  isUuidSlug: boolean,
  token: string,
): Promise<{ ok: true; page: WikiApiPage } | { ok: false; detail: WikiDetail | null }> {
  const env = process.env.NEXT_PUBLIC_API_URL;
  if (!env) return { ok: false, detail: null };
  const base = env.replace(/\/+$/, "");

  if (isUuidSlug) {
    const byPage = await tryFetch(
      base,
      `${base}/wiki/pages/${encodeURIComponent(slug)}`,
      token,
    );
    if (byPage.ok) return byPage;
    if (byPage.detail && (byPage.detail as WikiDetail).source_id) {
      return byPage;
    }
    const bySource = await tryFetch(
      base,
      `${base}/wiki/by-source-id/${encodeURIComponent(slug)}`,
      token,
    );
    if (bySource.ok) return bySource;
    if (bySource.detail && (bySource.detail as WikiDetail).source_id) {
      return bySource;
    }
    return {
      ok: false,
      detail:
        byPage.detail && (byPage.detail as WikiDetail).message
          ? byPage.detail
          : bySource.detail && (bySource.detail as WikiDetail).message
            ? bySource.detail
            : { message: `No wiki page for ${slug}` },
    };
  }
  return tryFetch(
    base,
    `${base}/wiki/by-file/${encodeURIComponent(slug)}`,
    token,
  );
}

export default async function WikiSlugPage({
  params,
}: {
  params: Promise<{ slug: string[] }>;
}) {
  noStore();

  const { slug: slugParts } = await params;
  const slug = (slugParts ?? []).join("/");
  const isUuidSlug = isUuid(slug);

  const result = await fetchWikiPage(
    slug,
    isUuidSlug,
    process.env.DASHBOARD_API_TOKEN ?? "",
  );

  // Siblings = pages in the same folder (e.g. projects/mozambique/).
  let siblings: WikiSibling[] = [];
  if (result.ok) {
    const parts = (result.page.file_path ?? "").split("/").filter(Boolean);
    if (parts.length >= 2) {
      const prefix = `${parts.slice(0, -1).join("/")}/`;
      siblings = await fetchSiblingPages(
        process.env.NEXT_PUBLIC_API_URL ?? "",
        prefix,
        process.env.DASHBOARD_API_TOKEN ?? "",
        result.page.page_id,
      );
    }
  }

  const apiBase = process.env.NEXT_PUBLIC_API_URL ?? "";

  return (
    <AppShell>
      <div className="space-y-4">
        <Link
          href="/wiki"
          className="inline-flex items-center gap-1 text-sm text-zinc-500 hover:text-zinc-700"
        >
          <ArrowLeft className="h-4 w-4" />
          Back to Wiki
        </Link>

        {!apiBase ? (
          <div className="rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/30 p-6">
            <h2 className="font-semibold text-amber-900 dark:text-amber-100 mb-2">
              API URL not configured
            </h2>
            <p className="text-sm text-amber-800 dark:text-amber-200 mb-3">
              The dashboard&apos;s <code className="text-xs">NEXT_PUBLIC_API_URL</code>{" "}
              environment variable is not set. The control-api typically lives
              on <code className="text-xs">http://100.72.153.12:8000/api/v1</code>.
            </p>
            <p className="text-xs text-amber-700 dark:text-amber-300">
              Set it in the build environment (or .env.local) and rebuild the
              dashboard. This page cannot fetch wiki data without it.
            </p>
          </div>
        ) : !result.ok ? (
          <PendingOrMissing detail={result.detail} slug={slug} />
        ) : (
          <WikiPageContentServer page={result.page} siblings={siblings} />
        )}
      </div>
    </AppShell>
  );
}

function PendingOrMissing({
  detail,
  slug,
}: {
  detail: WikiDetail | null;
  slug: string;
}) {
  if (detail && detail.source_id) {
    const sourceFailed =
      detail.source_status === "error" || detail.source_status === "quarantine";
    return (
      <div className="rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/30 p-6">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="font-semibold text-amber-900 dark:text-amber-100 mb-2">
              Wiki page not yet generated
            </h2>
            <p className="text-sm text-amber-800 dark:text-amber-200 mb-3">
              {detail.message ??
                "Wiki page for this source has not been compiled yet."}
            </p>
          </div>
          <RefreshButton />
        </div>
        <div className="grid grid-cols-2 gap-3 text-xs">
          <div>
            <div className="text-amber-700 dark:text-amber-300">Source ID</div>
            <code className="text-amber-900 dark:text-amber-100 break-all">
              {detail.source_id}
            </code>
          </div>
          <div>
            <div className="text-amber-700 dark:text-amber-300">Source status</div>
            <code className="text-amber-900 dark:text-amber-100">
              {detail.source_status ?? "extracted"}
            </code>
          </div>
        </div>
        <p className="text-xs text-amber-700 dark:text-amber-300 mt-3">
          {sourceFailed
            ? "The source did not compile into a wiki page. Check the source status and pipeline tasks, then re-run the compile stage."
            : "The compile task is queued and will produce the page shortly."}
        </p>
        <p className="text-xs text-amber-700 dark:text-amber-300 mt-1">
          File: <code className="text-xs">{slug}</code>
        </p>
      </div>
    );
  }
  return (
    <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-950 p-8 text-center">
      <RefreshCw className="h-8 w-8 mx-auto mb-3 text-zinc-300" />
      <div className="font-medium text-zinc-600 dark:text-zinc-300 mb-1">
        Page not found
      </div>
      <div className="text-xs text-zinc-400">
        No wiki page or source for {slug}
      </div>
    </div>
  );
}
