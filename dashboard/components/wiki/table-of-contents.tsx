"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ListTree } from "lucide-react";
import { cn } from "@/lib/utils";
import type { WikiHeading } from "@/lib/wiki-markdown";

/**
 * Table of contents for the wiki reader.
 *
 * The compile pipeline starts every page with `# {title}` (shown separately
 * in the article header), so the TOC lists sections (h2+) only. Two variants:
 * - Mobile (<xl): a collapsible "On this page" panel above the article.
 * - Desktop (xl+): a sticky sidebar next to the article.
 *
 * Both share the same scrollspy, which keeps the current section highlighted,
 * and clicking an entry smooth-scrolls to the heading inside the app's scroll
 * container and updates the URL hash.
 */
export function TableOfContents({ headings }: { headings: WikiHeading[] }) {
  const visible = useMemo(
    () => headings.filter((h) => h.depth >= 2),
    [headings],
  );
  const [activeId, setActiveId] = useState<string | null>(
    visible[0]?.id ?? null,
  );
  const [open, setOpen] = useState(false);
  // Suppress the scrollspy while a TOC click's smooth scroll is in flight.
  // Clicking scrolls the target heading to just below the top of the reading
  // area, which leaves the *previous* heading flush at the top — a position-
  // based scrollspy would then highlight that previous section and fight the
  // click. Suppressing until the programmatic scroll settles (or the user
  // scrolls on their own) keeps the clicked entry highlighted while it is read.
  const suppressSpy = useRef(false);

  // Shared navigation used by clicks and keyboard shortcuts.
  const navigateTo = useCallback((id: string) => {
    const el = document.getElementById(id);
    if (!el) return;
    // Keep this entry highlighted while the smooth scroll runs and the
    // section is read; the spy takes over again once the user scrolls.
    suppressSpy.current = true;
    el.scrollIntoView({ behavior: "smooth", block: "start" });
    window.history.replaceState(null, "", `#${id}`);
    setActiveId(id);
    setOpen(false); // Collapse the mobile panel once the reader navigates.
  }, []);

  // Keyboard navigation: j / ↓ move to the next section, k / ↑ to the previous
  // one. Ignored while typing in a form field or editing the page.
  useEffect(() => {
    if (visible.length < 2) return;
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.tagName === "SELECT" ||
          target.isContentEditable)
      ) {
        return;
      }
      if (e.key === "j" || e.key === "ArrowDown") {
        const idx = visible.findIndex((h) => h.id === activeId);
        if (idx >= 0 && idx < visible.length - 1) {
          e.preventDefault();
          navigateTo(visible[idx + 1].id);
        }
      } else if (e.key === "k" || e.key === "ArrowUp") {
        const idx = visible.findIndex((h) => h.id === activeId);
        if (idx > 0) {
          e.preventDefault();
          navigateTo(visible[idx - 1].id);
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [visible, activeId, navigateTo]);

  useEffect(() => {
    const elements = visible
      .map((h) => document.getElementById(h.id))
      .filter((el): el is HTMLElement => el !== null);
    if (elements.length === 0) return;

    // Scrollspy: the active section is the heading nearest the top of the
    // visible reading area. The article scrolls inside <main> (the app shell's
    // content area), so the band is the top 40% of <main> as seen on screen.
    // Positions are recomputed directly on scroll rather than via an
    // IntersectionObserver callback, whose `entries` only contain elements
    // whose state *changed* — during a smooth scroll that picks the wrong
    // (mid-animation) section.
    const scrollRoot = document.querySelector("main") as HTMLElement | null;
    const scrollTarget: EventTarget = scrollRoot ?? window;

    let raf = 0;
    const update = () => {
      raf = 0;
      const mainRect = scrollRoot?.getBoundingClientRect();
      const bandTop = mainRect ? mainRect.top : 0;
      const bandBottom = mainRect
        ? bandTop + mainRect.height * 0.4
        : window.innerHeight * 0.4;
      let topId: string | null = null;
      let top = Infinity;
      for (const el of elements) {
        const rect = el.getBoundingClientRect();
        if (rect.top >= bandTop && rect.top <= bandBottom && rect.top < top) {
          top = rect.top;
          topId = el.id;
        }
      }
      if (topId) setActiveId(topId);
    };
    const onScroll = () => {
      // Ignore the programmatic smooth scroll triggered by a TOC click; any
      // scroll after it settles (or after the user wheels/touches/types) hands
      // control back to the scrollspy.
      if (suppressSpy.current) return;
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(update);
    };
    // Release suppression once the click's smooth scroll finishes, or as soon
    // as the user scrolls on their own (wheel, touch, keyboard).
    const release = () => {
      suppressSpy.current = false;
    };

    update();
    scrollTarget.addEventListener("scroll", onScroll, { passive: true });
    scrollTarget.addEventListener("scrollend", release);
    window.addEventListener("resize", onScroll);
    window.addEventListener("wheel", release, { passive: true });
    window.addEventListener("touchstart", release, { passive: true });
    window.addEventListener("keydown", release);
    return () => {
      cancelAnimationFrame(raf);
      scrollTarget.removeEventListener("scroll", onScroll);
      scrollTarget.removeEventListener("scrollend", release);
      window.removeEventListener("resize", onScroll);
      window.removeEventListener("wheel", release);
      window.removeEventListener("touchstart", release);
      window.removeEventListener("keydown", release);
    };
  }, [visible]);

  if (visible.length === 0) return null;

  const handleClick = (e: React.MouseEvent<HTMLAnchorElement>, id: string) => {
    e.preventDefault();
    navigateTo(id);
  };

  const list = (
    <ul className="space-y-px">
      {visible.map((h) => (
        <li key={h.id}>
          <a
            href={`#${h.id}`}
            onClick={(e) => handleClick(e, h.id)}
            className={cn(
              "block border-l-2 py-1 pr-2 text-[13px] leading-snug transition-colors",
              h.depth <= 2 ? "pl-3" : h.depth === 3 ? "pl-6" : "pl-9",
              activeId === h.id
                ? "border-indigo-500 font-medium text-indigo-600 dark:text-indigo-400"
                : "border-transparent text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-300",
            )}
          >
            {h.text}
          </a>
        </li>
      ))}
    </ul>
  );

  return (
    <>
      {/* Mobile: collapsible panel above the article. */}
      <div className="xl:hidden w-full rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-950">
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          aria-controls="wiki-toc-mobile"
          className="flex w-full items-center justify-between gap-2 px-3 py-2 text-sm font-medium text-zinc-700 dark:text-zinc-300 hover:bg-zinc-50 dark:hover:bg-zinc-900 rounded-lg"
        >
          <span className="inline-flex items-center gap-2">
            <ListTree className="h-4 w-4 text-indigo-500" />
            On this page
            <span className="text-xs text-zinc-400 font-normal">
              {visible.length} section{visible.length === 1 ? "" : "s"}
            </span>
          </span>
          <ChevronDown
            className={cn(
              "h-4 w-4 text-zinc-400 transition-transform",
              open && "rotate-180",
            )}
          />
        </button>
        {open ? (
          <div id="wiki-toc-mobile" className="px-2 pb-2 max-h-72 overflow-y-auto">
            {list}
          </div>
        ) : null}
      </div>

      {/* Desktop: sticky sidebar. */}
      <nav
        aria-label="Table of contents"
        className="hidden xl:block w-56 shrink-0 sticky top-0 self-start max-h-[calc(100vh-3rem)] overflow-y-auto py-1"
      >
        <p className="text-xs font-semibold uppercase tracking-wide text-zinc-400 mb-2">
          On this page
        </p>
        {list}
        <p className="mt-3 border-t border-zinc-200 dark:border-zinc-800 pt-2 text-[11px] text-zinc-400">
          <kbd className="rounded bg-zinc-100 dark:bg-zinc-800 px-1">j</kbd>{" "}
          <kbd className="rounded bg-zinc-100 dark:bg-zinc-800 px-1">k</kbd> to
          navigate
        </p>
      </nav>
    </>
  );
}
