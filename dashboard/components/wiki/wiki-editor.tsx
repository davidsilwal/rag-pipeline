"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Check, Pencil, X } from "lucide-react";
import { useApi } from "@/lib/hooks";

/**
 * Inline wiki editor: an "Edit" button that opens a modal with a title field
 * and a markdown textarea. Saving PATCHes the page via the control API and
 * refreshes the server-rendered view. Manual edits are overwritten the next
 * time the source is recompiled by the pipeline.
 */
export function WikiEditor({
  pageId,
  title,
  markdownBody,
  filePath,
}: {
  pageId: string;
  title: string;
  markdownBody: string;
  filePath: string;
}) {
  const api = useApi();
  const router = useRouter();
  const [editing, setEditing] = useState(false);
  const [draftTitle, setDraftTitle] = useState(title);
  const [draftBody, setDraftBody] = useState(markdownBody);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const startEditing = () => {
    setDraftTitle(title);
    setDraftBody(markdownBody);
    setError(null);
    setEditing(true);
  };

  const cancel = () => {
    if (saving) return;
    setEditing(false);
    setError(null);
  };

  const save = async () => {
    if (saving) return;
    setSaving(true);
    setError(null);
    try {
      await api.updateWikiPage(pageId, {
        markdown_body: draftBody,
        title: draftTitle.trim() || title,
      });
      // Re-run the server component so the freshly saved markdown renders.
      router.refresh();
      setEditing(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save the page.");
    } finally {
      setSaving(false);
    }
  };

  // Escape closes the editor.
  useEffect(() => {
    if (!editing) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") cancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editing, saving]);

  return (
    <>
      <button
        type="button"
        onClick={startEditing}
        className="inline-flex items-center gap-1.5 rounded-md border border-zinc-300 dark:border-zinc-700 px-3 py-1.5 text-xs font-medium text-zinc-600 dark:text-zinc-300 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors"
      >
        <Pencil className="h-3 w-3" />
        Edit
      </button>

      {editing ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 sm:p-8">
          <div
            className="absolute inset-0 bg-black/50"
            onClick={cancel}
            aria-hidden="true"
          />
          <div className="relative z-10 flex h-[85vh] w-full max-w-3xl flex-col rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-950 shadow-xl">
            <div className="flex items-center justify-between gap-3 border-b border-zinc-200 dark:border-zinc-800 px-4 py-3">
              <div className="min-w-0">
                <h2 className="text-sm font-semibold text-zinc-800 dark:text-zinc-100">
                  Edit wiki page
                </h2>
                <code className="block truncate text-[11px] text-zinc-400">
                  {filePath}
                </code>
              </div>
              <button
                type="button"
                onClick={cancel}
                aria-label="Close editor"
                className="rounded-md p-1.5 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-600 dark:hover:bg-zinc-800 dark:hover:text-zinc-300"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="flex min-h-0 flex-1 flex-col gap-3 p-4">
              <label className="block">
                <span className="mb-1 block text-xs font-medium text-zinc-500">
                  Title
                </span>
                <input
                  type="text"
                  value={draftTitle}
                  onChange={(e) => setDraftTitle(e.target.value)}
                  className="w-full rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                />
              </label>

              <label className="flex min-h-0 flex-1 flex-col">
                <span className="mb-1 block text-xs font-medium text-zinc-500">
                  Markdown
                </span>
                <textarea
                  value={draftBody}
                  onChange={(e) => setDraftBody(e.target.value)}
                  spellCheck={false}
                  className="min-h-0 w-full flex-1 resize-none rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-2 font-mono text-xs leading-relaxed focus:outline-none focus:ring-2 focus:ring-indigo-500"
                />
              </label>

              {error ? (
                <p className="rounded-md border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-950/30 px-3 py-2 text-xs text-red-700 dark:text-red-300">
                  {error}
                </p>
              ) : null}
            </div>

            <div className="flex items-center gap-2 border-t border-zinc-200 dark:border-zinc-800 px-4 py-3">
              <button
                type="button"
                onClick={save}
                disabled={saving}
                className="inline-flex items-center gap-1.5 rounded-md bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-500 disabled:opacity-50"
              >
                <Check className="h-3 w-3" />
                {saving ? "Saving…" : "Save"}
              </button>
              <button
                type="button"
                onClick={cancel}
                disabled={saving}
                className="inline-flex items-center gap-1.5 rounded-md border border-zinc-300 dark:border-zinc-700 px-3 py-1.5 text-xs font-medium text-zinc-600 dark:text-zinc-300 hover:bg-zinc-100 dark:hover:bg-zinc-800 disabled:opacity-50"
              >
                <X className="h-3 w-3" />
                Cancel
              </button>
              <p className="ml-auto text-[11px] text-zinc-400">
                Manual edits are overwritten when the source is recompiled.
              </p>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
