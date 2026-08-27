"use client";

import { useEffect } from "react";

/**
 * Renders ```mermaid fenced blocks in the wiki article to SVG diagrams.
 * Mermaid is heavy (~1.5 MB), so it's dynamically imported only when the
 * page actually contains mermaid blocks. Each `<pre><code class="language-mermaid">`
 * is replaced by a rendered diagram; on failure the raw source is shown.
 */
export function MermaidRenderer() {
  useEffect(() => {
    const blocks = Array.from(
      document.querySelectorAll("code.language-mermaid"),
    );
    if (blocks.length === 0) return;
    let cancelled = false;

    (async () => {
      const { default: mermaid } = await import("mermaid");
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: "strict",
        theme: "default",
        fontFamily:
          "Inter, ui-sans-serif, system-ui, -apple-system, sans-serif",
      });
      for (let i = 0; i < blocks.length; i++) {
        if (cancelled) return;
        const code = blocks[i];
        const pre = code.closest("pre");
        if (!pre) continue;
        const container = document.createElement("div");
        container.className =
          "mermaid-container my-4 flex justify-center overflow-x-auto rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-950";
        pre.replaceWith(container);
        try {
          const { svg } = await mermaid.render(
            `mermaid-${i}`,
            code.textContent ?? "",
          );
          container.innerHTML = svg;
        } catch {
          // Unparseable diagram — show the raw source instead of nothing.
          const fallback = document.createElement("pre");
          fallback.className =
            "w-full overflow-x-auto rounded bg-zinc-50 p-3 text-xs text-zinc-600 dark:bg-zinc-900 dark:text-zinc-300";
          fallback.textContent = code.textContent ?? "";
          container.replaceWith(fallback);
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  return null;
}
