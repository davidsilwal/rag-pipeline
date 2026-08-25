import { Suspense } from "react";
import Link from "next/link";
import { unstable_noStore as noStore } from "next/cache";
import { ArrowLeft, FileText } from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { WikiPageContent } from "./wiki-page-content";

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
  created_at?: string | null;
  updated_at?: string | null;
}

interface WikiDetail {
  source_id?: string;
  source_status?: string;
  message?: string;
}

function isUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
    value,
  );
}

async function fetchWikiPage(
  slug: string,
  isUuidSlug: boolean,
): Promise<{ ok: true; page: WikiApiPage } | { ok: false; detail: WikiDetail | null }> {
  const env = process.env.NEXT_PUBLIC_API_URL;
  if (!env) {
    return { ok: false, detail: null };
  }
  const base = env.replace(/\/+$/, "");
  const url = isUuidSlug
    ? `${base}/wiki/pages/${encodeURIComponent(slug)}`
    : `${base}/wiki/by-file/${encodeURIComponent(slug)}`;
  // The control-api requires a token (require_any_token accepts either
  // the admin API_TOKEN or a worker UUID). For server-side fetches the
  // browser's localStorage isn't reachable, so we forward the build-time
  // DASHBOARD_API_TOKEN. The user can override it via .env.local.
  const token = process.env.DASHBOARD_API_TOKEN ?? "";
  const headers: Record<string, string> = {};
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }
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

export default async function WikiSlugPage({
  params,
}: {
  params: Promise<{ slug: string[] }>;
}) {
  // Server component: this runs on the server, so the API base URL
  // configured at build time is always available. The user's localStorage
  // is irrelevant here, fixing the "Page not found on every wiki page"
  // regression that hit fresh browsers and the 401 that followed when
  // require_any_token received a missing/empty Bearer.
  noStore();

  const { slug: slugParts } = await params;
  const slug = (slugParts ?? []).join("/");
  const isUuidSlug = isUuid(slug);

  const result = await fetchWikiPage(slug, isUuidSlug);

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
              The dashboard's <code className="text-xs">NEXT_PUBLIC_API_URL</code>{" "}
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
          <Suspense
            fallback={
              <div className="animate-pulse space-y-4">
                <div className="h-8 bg-zinc-100 dark:bg-zinc-800 rounded w-1/3" />
                <div className="h-32 bg-zinc-100 dark:bg-zinc-800 rounded" />
              </div>
            }
          >
            <div className="flex gap-6">
              <WikiPageContent page={result.page} />
            </div>
          </Suspense>
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
    return (
      <div className="rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/30 p-6">
        <h2 className="font-semibold text-amber-900 dark:text-amber-100 mb-2">
          Wiki page not yet generated
        </h2>
        <p className="text-sm text-amber-800 dark:text-amber-200 mb-3">
          {detail.message ??
            "Wiki page for this source has not been compiled yet."}
        </p>
        <div className="grid grid-cols-2 gap-3 text-xs">
          <div>
            <div className="text-amber-700 dark:text-amber-300">Source ID</div>
            <code className="text-amber-900 dark:text-amber-100">
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
          The compile task is queued and will produce the page shortly.
          Refresh this URL in a minute or two.
        </p>
        <p className="text-xs text-amber-700 dark:text-amber-300 mt-1">
          File: <code className="text-xs">{slug}</code>
        </p>
      </div>
    );
  }
  return (
    <div className="text-zinc-400">
      <div className="font-medium mb-1">Page not found</div>
      <div className="text-xs">No wiki page or source for {slug}</div>
    </div>
  );
}
