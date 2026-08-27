import Link from "next/link";
import {
  Clock,
  FileText,
  Files,
  FolderTree,
  GitBranch,
  Layers,
} from "lucide-react";
import type { WikiSibling } from "./page";
import { StatusBadge } from "@/components/ui/badge";
import { CopyButton } from "@/components/ui/copy-button";
import { MermaidRenderer } from "@/components/wiki/mermaid-renderer";
import { ReadingProgress } from "@/components/wiki/reading-progress";
import { TableOfContents } from "@/components/wiki/table-of-contents";
import { ExportMarkdown } from "@/components/wiki/export-markdown";
import { WikiEditor } from "@/components/wiki/wiki-editor";
import { relativeTime } from "@/lib/utils";
import {
  containingGroup,
  wikiGroupKey,
  wikiPathParts,
} from "@/lib/wiki-groups";
import {
  extractWikiBody,
  renderWikiMarkdown,
} from "@/lib/wiki-markdown";

export interface WikiPageContentServerProps {
  page: {
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
  };
}

export function WikiPageContentServer({
  page,
  siblings = [],
}: WikiPageContentServerProps & { siblings?: WikiSibling[] }) {
  // Server-side markdown render: marked() converts the LLM-generated
  // markdown to HTML at SSR time so the table, code blocks, footnotes,
  // headings, etc. all render in the initial server response — no
  // client-side hydration delay, no "loading" state, and the page
  // works even with JavaScript disabled. Raw HTML and javascript: URLs
  // in the markdown are neutralized (see renderWikiMarkdown).
  const { html, headings } = renderWikiMarkdown(page.markdown_body ?? "");
  const parts = wikiPathParts(page.file_path ?? "");
  const projectName =
    parts[0] === "projects" && parts.length >= 2 ? parts[1] : null;
  const folderLabel = parts.length >= 2 ? parts[parts.length - 2] : null;

  return (
    <div className="flex w-full flex-col gap-6 xl:flex-row xl:items-start">
      <aside className="flex w-full flex-col gap-6 xl:w-56 xl:shrink-0">
        <TableOfContents headings={headings} />
        {folderLabel && siblings.length > 0 ? (
          <SiblingDocs siblings={siblings} folderLabel={folderLabel} />
        ) : null}
      </aside>
      <div className="flex-1 min-w-0">
        <ReadingProgress />
        <article className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-950 p-6">
          <header className="mb-4 pb-4 border-b border-zinc-200 dark:border-zinc-800">
            <div className="flex items-start justify-between gap-3 mb-2">
              <h1 className="text-2xl font-bold">{page.title}</h1>
              <div className="flex items-center gap-2 shrink-0">
                <StatusBadge status={page.status} />
                <ExportMarkdown
                  filePath={page.file_path}
                  title={page.title}
                  markdownBody={extractWikiBody(page.markdown_body ?? "")}
                />
                <WikiEditor
                  pageId={page.page_id}
                  title={page.title}
                  markdownBody={extractWikiBody(page.markdown_body ?? "")}
                  filePath={page.file_path}
                />
              </div>
            </div>
          <div className="flex items-center flex-wrap gap-2 text-sm text-zinc-500">
            {projectName ? <ProjectChip name={projectName} /> : null}
            {page.file_path ? (
              <GroupLink filePath={page.file_path} />
            ) : null}
            {page.domain ? <span>• {page.domain}</span> : null}
            <span>• {page.page_type}</span>
          </div>
          {page.frontmatter && Object.keys(page.frontmatter).length > 0 ? (
            <details className="mt-3">
              <summary className="text-xs text-zinc-500 cursor-pointer">
                Frontmatter
              </summary>
              <pre className="mt-2 text-xs bg-zinc-50 dark:bg-zinc-900 rounded p-2 overflow-x-auto">
                {JSON.stringify(page.frontmatter, null, 2)}
              </pre>
            </details>
          ) : null}
          <div className="mt-3 flex items-center gap-3 text-xs text-zinc-400">
            <CopyButton text={page.page_id} label="Copy page_id" />
            {page.created_at ? (
              <span className="inline-flex items-center gap-1">
                <Clock className="h-3 w-3" />
                Created {relativeTime(page.created_at)}
              </span>
            ) : null}
            {page.updated_at ? (
              <span>• Updated {relativeTime(page.updated_at)}</span>
            ) : null}
          </div>
        </header>
        <div
          className="prose prose-zinc dark:prose-invert max-w-none"
          dangerouslySetInnerHTML={{ __html: html }}
        />
        <MermaidRenderer />
        {page.source_unit_ids?.length ||
        page.git_commit_sha ||
        page.last_verified_at ? (
          <footer className="mt-8 pt-4 border-t border-zinc-200 dark:border-zinc-800 text-xs text-zinc-400 space-y-1">
            {page.source_unit_ids && page.source_unit_ids.length > 0 ? (
              <p className="inline-flex items-center gap-1.5">
                <Layers className="h-3 w-3" />
                Compiled from {page.source_unit_ids.length} source unit
                {page.source_unit_ids.length === 1 ? "" : "s"}
              </p>
            ) : null}
            {page.git_commit_sha ? (
              <p className="inline-flex items-center gap-1.5">
                <GitBranch className="h-3 w-3" />
                <code>{page.git_commit_sha.slice(0, 12)}</code>
              </p>
            ) : null}
            {page.last_verified_at ? (
              <p>Last verified {relativeTime(page.last_verified_at)}</p>
            ) : null}
          </footer>
        ) : null}
        </article>
      </div>
    </div>
  );
}

/**
 * Sidebar list of sibling docs — other pages in the same folder as the
 * current page (e.g. other docs under projects/mozambique/).
 */
function SiblingDocs({
  siblings,
  folderLabel,
}: {
  siblings: WikiSibling[];
  folderLabel: string;
}) {
  return (
    <div className="w-full rounded-lg border border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-950">
      <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-zinc-400">
        <Files className="h-3 w-3" />
        More in {folderLabel}
      </p>
      <ul className="space-y-1">
        {siblings.slice(0, 12).map((s) => (
          <li key={s.page_id}>
            <Link
              href={`/wiki/${s.page_id}`}
              title={s.file_path}
              className="block truncate text-[13px] text-zinc-700 transition-colors hover:text-indigo-600 dark:text-zinc-300 dark:hover:text-indigo-400"
            >
              {s.title || s.file_path}
            </Link>
          </li>
        ))}
      </ul>
      {siblings.length > 12 ? (
        <p className="mt-2 text-[11px] text-zinc-400">
          +{siblings.length - 12} more
        </p>
      ) : null}
    </div>
  );
}

/**
 * Prominent project chip in the reader header — links to the wiki list
 * filtered to this page's project (e.g. `projects/mozambique/...` shows
 * `mozambique` and links to `/wiki?group=projects/mozambique`).
 */
function ProjectChip({ name }: { name: string }) {
  return (
    <Link
      href={`/wiki?group=${encodeURIComponent(`projects/${name}`)}`}
      title={`View all docs of project ${name}`}
      className="inline-flex items-center gap-1 rounded-full border border-indigo-200 bg-indigo-50 px-2 py-0.5 text-xs font-medium text-indigo-700 transition-colors hover:bg-indigo-100 dark:border-indigo-800 dark:bg-indigo-950/40 dark:text-indigo-300 dark:hover:bg-indigo-900"
    >
      <FolderTree className="h-3 w-3" />
      {name}
    </Link>
  );
}

/**
 * File path shown as a link back to the wiki list filtered to the folder
 * containing this page (e.g. `projects/agentconnect-simulator/domain-events.md`
 * links to `/wiki?group=projects/agentconnect-simulator`). Pages without a
 * containing folder render as plain text.
 */
function GroupLink({ filePath }: { filePath: string }) {
  const group = containingGroup(filePath);
  if (!group) {
    return (
      <span className="inline-flex items-center gap-1">
        <FileText className="h-3 w-3" />
        <code className="text-xs">{filePath}</code>
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1">
      <FileText className="h-3 w-3" />
      <Link
        href={`/wiki?group=${encodeURIComponent(wikiGroupKey(group))}`}
        title={`View all docs in ${wikiGroupKey(group)}`}
        className="hover:text-indigo-600 dark:hover:text-indigo-400"
      >
        <code className="text-xs">{filePath}</code>
      </Link>
    </span>
  );
}
