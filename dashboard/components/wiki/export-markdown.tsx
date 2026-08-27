"use client";

import { useState } from "react";
import { Download, Loader2 } from "lucide-react";

/**
 * "Export .md" button for the wiki reader.  Clicking downloads the page's
 * markdown body as a local `.md` file named after its file_path (or title).
 */
export function ExportMarkdown({
  filePath,
  title,
  markdownBody,
}: {
  filePath: string;
  title: string;
  markdownBody: string;
}) {
  const [busy, setBusy] = useState(false);

  const handleExport = () => {
    setBusy(true);
    try {
      // Derive a filename from the file_path (e.g. projects/foo/bar.md → bar.md)
      const parts = filePath.split("/").filter(Boolean);
      const filename = (parts[parts.length - 1] || title || "page") + ".md";

      const blob = new Blob([markdownBody], { type: "text/markdown;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
    } finally {
      setBusy(false);
    }
  };

  return (
    <button
      type="button"
      onClick={handleExport}
      disabled={busy}
      className="inline-flex items-center gap-1.5 rounded-md border border-zinc-300 dark:border-zinc-700 px-3 py-1.5 text-xs font-medium text-zinc-600 dark:text-zinc-300 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors disabled:opacity-50"
    >
      {busy ? (
        <Loader2 className="h-3 w-3 animate-spin" />
      ) : (
        <Download className="h-3 w-3" />
      )}
      Export .md
    </button>
  );
}
