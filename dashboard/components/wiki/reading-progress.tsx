"use client";

import { useEffect, useState } from "react";

/**
 * Thin sticky progress bar that fills as the reader scrolls through the wiki
 * article. The article scrolls inside <main> (the app shell's content area),
 * so progress is measured against that container and falls back to the window
 * if <main> isn't present.
 */
export function ReadingProgress() {
  const [progress, setProgress] = useState(0);

  useEffect(() => {
    const root = document.querySelector("main") as HTMLElement | null;
    const target: EventTarget = root ?? window;

    let raf = 0;
    const update = () => {
      raf = 0;
      if (root) {
        const max = root.scrollHeight - root.clientHeight;
        setProgress(max > 0 ? Math.min(1, root.scrollTop / max) : 0);
      } else {
        const max =
          document.documentElement.scrollHeight - window.innerHeight;
        setProgress(max > 0 ? Math.min(1, window.scrollY / max) : 0);
      }
    };
    const onScroll = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(update);
    };

    update();
    target.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      cancelAnimationFrame(raf);
      target.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, []);

  return (
    <div
      role="progressbar"
      aria-label="Reading progress"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(progress * 100)}
      className="sticky top-0 z-10 mb-4 h-0.5 w-full overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-800"
    >
      <div
        className="h-full rounded-full bg-indigo-500 transition-[width] duration-75 ease-linear"
        style={{ width: `${Math.round(progress * 100)}%` }}
      />
    </div>
  );
}
