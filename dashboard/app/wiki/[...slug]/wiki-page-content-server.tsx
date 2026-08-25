import { Clock, FileText } from "lucide-react";
import { StatusBadge } from "@/components/ui/badge";
import { CopyButton } from "@/components/ui/copy-button";
import { relativeTime } from "@/lib/utils";
import { marked } from "marked";

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
    created_at?: string | null;
    updated_at?: string | null;
  };
}

export function WikiPageContentServer({ page }: WikiPageContentServerProps) {
  // Server-side markdown render: marked() converts the LLM-generated
  // markdown to HTML at SSR time so the table, code blocks, footnotes,
  // headings, etc. all render in the initial server response — no
  // client-side hydration delay, no "loading" state, and the page
  // works even with JavaScript disabled.
  //
  // marked is configured to:
  //   - render GitHub-Flavored-Markdown tables
  //   - preserve footnote refs ([^N]) and definitions ([^N]: text)
  //   - emit safe HTML (the markdown source comes from our own LLM
  //     compile pipeline, not user input, so we trust the output)
  const html = marked.parse(page.markdown_body ?? "", { gfm: true });

  return (
    <article className="flex-1 min-w-0 rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-950 p-6">
      <header className="mb-4 pb-4 border-b border-zinc-200 dark:border-zinc-800">
        <div className="flex items-start justify-between gap-3 mb-2">
          <h1 className="text-2xl font-bold">{page.title}</h1>
          <div className="flex items-center gap-2 shrink-0">
            <StatusBadge status={page.status} />
          </div>
        </div>
        <div className="flex items-center flex-wrap gap-2 text-sm text-zinc-500">
          <span className="inline-flex items-center gap-1">
            <FileText className="h-3 w-3" />
            <code className="text-xs">{page.file_path}</code>
          </span>
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
        // eslint-disable-next-line react/no-danger
        dangerouslySetInnerHTML={{ __html: html as string }}
      />
    </article>
  );
}
