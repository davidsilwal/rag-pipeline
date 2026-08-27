import {
  marked,
  type Token,
  type TokenizerAndRendererExtension,
  type Tokens,
} from "marked";
import markedFootnote from "marked-footnote";

export interface WikiHeading {
  id: string;
  text: string;
  depth: number;
}

export function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function isSafeUrl(href: string): boolean {
  return /^(https?:|mailto:|#|\/)/i.test(href);
}

export function slugifyHeading(text: string): string {
  const slug = String(text ?? "")
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return slug || "section";
}

/**
 * Collect all headings (including nested ones, e.g. inside blockquotes) in
 * document order, mirroring the order the renderer will visit them so the
 * generated ids line up 1:1.
 */
function collectHeadings(markdown: string): { text: string; depth: number }[] {
  const tokens = marked.lexer(markdown, { gfm: true });
  const out: { text: string; depth: number }[] = [];
  const walk = (list: Token[]) => {
    for (const t of list) {
      if (t.type === "heading") {
        out.push({ text: t.text, depth: t.depth });
      }
      const childTokens = (t as { tokens?: unknown }).tokens;
      if (Array.isArray(childTokens)) {
        walk(childTokens as Token[]);
      }
      const items = (t as { items?: unknown }).items;
      if (Array.isArray(items)) {
        walk(items as Token[]);
      }
    }
  };
  walk(tokens);
  return out;
}

// ── Callouts (Obsidian-style `> [!TYPE] ...`) ───────────────────────────────
// The LLM compiler writes status banners like `> [!STATUS] Populated` both as
// standalone blockquotes and inline mid-paragraph. Both are rendered as
// colored badges/boxes. Class strings are literal so Tailwind's JIT picks
// them up from this source file.
const CALLOUT_STYLES: Record<
  string,
  { box: string; label: string; pill: string }
> = {
  status: {
    box: "border-l-blue-500 bg-blue-50/70 dark:bg-blue-950/30",
    label: "text-blue-600 dark:text-blue-400",
    pill: "bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300",
  },
  info: {
    box: "border-l-sky-500 bg-sky-50/70 dark:bg-sky-950/30",
    label: "text-sky-600 dark:text-sky-400",
    pill: "bg-sky-100 text-sky-700 dark:bg-sky-950 dark:text-sky-300",
  },
  warning: {
    box: "border-l-amber-500 bg-amber-50/70 dark:bg-amber-950/30",
    label: "text-amber-600 dark:text-amber-400",
    pill: "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300",
  },
  danger: {
    box: "border-l-red-500 bg-red-50/70 dark:bg-red-950/30",
    label: "text-red-600 dark:text-red-400",
    pill: "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300",
  },
  error: {
    box: "border-l-red-500 bg-red-50/70 dark:bg-red-950/30",
    label: "text-red-600 dark:text-red-400",
    pill: "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300",
  },
  success: {
    box: "border-l-emerald-500 bg-emerald-50/70 dark:bg-emerald-950/30",
    label: "text-emerald-600 dark:text-emerald-400",
    pill: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
  },
  tip: {
    box: "border-l-emerald-500 bg-emerald-50/70 dark:bg-emerald-950/30",
    label: "text-emerald-600 dark:text-emerald-400",
    pill: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
  },
  note: {
    box: "border-l-zinc-400 bg-zinc-50 dark:bg-zinc-900/40",
    label: "text-zinc-500 dark:text-zinc-400",
    pill: "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300",
  },
};

/** Strip footnote citation markers (provenance noise inside a banner). */
function stripFootnotes(text: string): string {
  return text.replace(/\[\^[^\]]+\]/g, "").replace(/\s{2,}/g, " ").trim();
}

const inlineCallout: TokenizerAndRendererExtension = {
  name: "inlineCallout",
  level: "inline",
  start(src: string) {
    const i = src.indexOf("> [!");
    return i >= 0 ? i : -1;
  },
  tokenizer(src: string) {
    const match = /^>\s*\[!([\w-]+)\]/.exec(src);
    if (!match) return undefined;
    return { type: "inlineCallout", raw: match[0], kind: match[1] };
  },
  renderer(token: Tokens.Generic) {
    const kind = String(token.kind || "note").toLowerCase();
    const s = CALLOUT_STYLES[kind] ?? CALLOUT_STYLES.note;
    return `<span class="inline-flex items-center rounded px-1.5 py-0.5 align-middle text-[10px] font-bold uppercase tracking-wide ${s.pill} ${s.label}">${escapeHtml(String(token.kind).toUpperCase())}</span>`;
  },
};

const blockCallout: TokenizerAndRendererExtension = {
  name: "callout",
  level: "block",
  start(src: string) {
    return src.match(/^\s*>\s*\[!/)?.index ?? -1;
  },
  tokenizer(src: string) {
    const match = /^(\s*>\s*\[!([\w-]+)\][^\n]*)(?:\n(?:[ \t]*>[^\n]*))*/.exec(
      src,
    );
    if (!match) return undefined;
    const first = /^\s*>\s*\[!([\w-]+)\]\s*(.*)$/.exec(
      match[0].split("\n")[0],
    );
    if (!first) return undefined;
    const body = match[0]
      .split("\n")
      .slice(1)
      .map((l) => l.replace(/^[ \t]*>[ \t]?/, ""))
      .join("\n");
    return {
      type: "callout",
      raw: match[0],
      kind: first[1],
      title: first[2],
      body,
    };
  },
  renderer(token: Tokens.Generic) {
    const kind = String(token.kind || "note").toLowerCase();
    const s = CALLOUT_STYLES[kind] ?? CALLOUT_STYLES.note;
    const title = stripFootnotes(token.title ?? "");
    const body = stripFootnotes(token.body ?? "");
    return (
      `<div class="my-4 rounded-r-lg border-l-4 px-4 py-3 ${s.box}">` +
      `<p class="mb-1 text-[11px] font-bold uppercase tracking-wider ${s.label}">${escapeHtml(String(token.kind).toUpperCase())}</p>` +
      (title
        ? `<p class="font-medium text-zinc-800 dark:text-zinc-100">${escapeHtml(title)}</p>`
        : "") +
      (body
        ? `<div class="mt-1 text-sm text-zinc-600 dark:text-zinc-300">${escapeHtml(body)}</div>`
        : "") +
      `</div>`
    );
  },
};

// ── Footnotes ───────────────────────────────────────────────────────────────
// marked-footnote turns `[^id]` refs with definitions into superscript links
// and collects a footnotes section at the end. Refs WITHOUT a definition stay
// literal text, so a second inline extension renders them as a muted
// superscript instead of the raw `[^src_2]` noise (common in chat transcripts).
let footnoteDefs = new Set<string>();
const footnoteMuted: TokenizerAndRendererExtension = {
  name: "footnoteMuted",
  level: "inline",
  start(src: string) {
    const i = src.indexOf("[^");
    return i >= 0 ? i : -1;
  },
  tokenizer(src: string) {
    const match = /^\[\^([^\]]+)\]/.exec(src);
    if (!match || footnoteDefs.has(match[1])) return undefined;
    return { type: "footnoteMuted", raw: match[0], id: match[1] };
  },
  renderer(token: Tokens.Generic) {
    return `<sup class="footnote-muted">${escapeHtml(token.id ?? "")}</sup>`;
  },
};

marked.use({ extensions: [blockCallout, inlineCallout] });
marked.use(markedFootnote());
marked.use({ extensions: [footnoteMuted] });

export interface RenderedWikiMarkdown {
  html: string;
  headings: WikiHeading[];
}

/**
 * Recover the actual page body when the compiler stored its raw LLM response
 * (a ```json fence containing `{title, frontmatter, body}`) instead of the
 * extracted markdown — roughly a fifth of compiled pages. The stored JSON is
 * often truncated (no closing fence), so besides a strict JSON.parse we also
 * recover the `"body"` string value directly from the cut-off text. Returns
 * the input unchanged for anything that isn't this shape (e.g. pages that
 * legitimately start with a code block), so nothing else is affected.
 */
export function extractWikiBody(raw: string): string {
  const src = raw ?? "";
  const trimmed = src.trim();
  if (!/^```/.test(trimmed)) return src;
  const isJsonFence =
    /^```json\b/i.test(trimmed) || /^```\s*\n?\s*\{/.test(trimmed);

  const inner = trimmed
    .replace(/^```[a-zA-Z0-9_-]*\n?/, "")
    .replace(/\n?```\s*$/, "");
  try {
    const parsed = JSON.parse(inner) as { body?: unknown };
    if (
      parsed &&
      typeof parsed === "object" &&
      typeof parsed.body === "string" &&
      parsed.body.length > 0
    ) {
      return parsed.body;
    }
  } catch {
    // Truncated JSON — recover the body string below.
  }

  if (/^```(yml|yaml)\b/i.test(trimmed)) {
    // Compiler wrapped a YAML frontmatter + body in a yml fence
    // (cross-KB consolidation output). Strip the fence and the
    // leading frontmatter block, keep the real markdown body.
    let s = inner;
    const fm = /^---\s*\r?\n[\s\S]*?\r?\n---\s*\r?\n/.exec(s);
    if (fm) s = s.slice(fm[0].length);
    // Some compilers close the yml fence right after the frontmatter
    // block (` ```yml` + frontmatter + ` ``` ` + body) — drop that too.
    s = s.replace(/^```\s*\r?\n/, "");
    s = s.trim();
    if (s.length > 0 && /^#/.test(s)) return s;
    return src;
  }
  if (!isJsonFence) return src;
  const marker = '"body": "';
  const idx = trimmed.indexOf(marker);
  if (idx < 0) return src;
  let s = trimmed.slice(idx + marker.length);
  if (s.endsWith('"')) s = s.slice(0, -1);
  return s.replace(/\\(["\\/bfnrt]|u[0-9a-fA-F]{4})/g, (_m, esc: string) => {
    switch (esc[0]) {
      case '"':
        return '"';
      case "\\":
        return "\\";
      case "/":
        return "/";
      case "b":
        return "\b";
      case "f":
        return "\f";
      case "n":
        return "\n";
      case "r":
        return "\r";
      case "t":
        return "\t";
      default:
        return String.fromCharCode(parseInt(esc.slice(1), 16));
    }
  });
}

/**
 * Render LLM-compiled wiki markdown to safe HTML at SSR time.
 *
 * - Raw HTML tags are escaped (displayed as text) instead of executed, and
 *   link/image URLs are restricted to http(s), mailto, anchors and relative
 *   paths, so prompt-injected markup in source documents can't run scripts.
 * - Headings get stable, deduplicated slug ids (e.g. `second-section-2`) so
 *   a table of contents can deep-link into the page.
 * - Footnotes (`[^src_N]` with definitions) render as superscript links with
 *   a footnotes section; undefined refs become muted superscripts.
 * - Obsidian-style callouts (`> [!STATUS] ...`) render as colored badges
 *   (inline) and boxes (block).
 */
export function renderWikiMarkdown(markdown: string): RenderedWikiMarkdown {
  const body = extractWikiBody(markdown);
  footnoteDefs = new Set(
    [...body.matchAll(/^\s*\[\^([^\]]+)\]:/gm)].map((m) => m[1]),
  );

  const rawHeadings = collectHeadings(body);
  const seen: Record<string, number> = {};
  const idFor = (text: string) => {
    const base = slugifyHeading(text);
    const n = (seen[base] = (seen[base] ?? 0) + 1);
    return n === 1 ? base : `${base}-${n}`;
  };
  const headings: WikiHeading[] = rawHeadings.map((h) => ({
    ...h,
    id: idFor(h.text),
  }));

  const renderer = new marked.Renderer();
  let headingIndex = 0;
  renderer.html = ({ text }) => escapeHtml(text ?? "");
  renderer.link = ({ href, title, text }) => {
    const safeHref = href && isSafeUrl(href) ? href : "";
    const titleAttr = title ? ` title="${escapeHtml(title)}"` : "";
    return `<a href="${escapeHtml(safeHref)}"${titleAttr}>${text}</a>`;
  };
  renderer.image = ({ href, title, text }) => {
    if (!href || !isSafeUrl(href)) {
      return escapeHtml(text ?? href ?? "");
    }
    const titleAttr = title ? ` title="${escapeHtml(title)}"` : "";
    return `<img src="${escapeHtml(href)}" alt="${escapeHtml(text ?? "")}"${titleAttr} />`;
  };
  renderer.heading = function (tok) {
    // The renderer visits headings in the same document order as the lexer
    // walk above, so headings[headingIndex] is the id for this heading.
    const id = headings[headingIndex]?.id ?? idFor(tok.text);
    headingIndex += 1;
    const inner = this.parser.parseInline(tok.tokens);
    return `<h${tok.depth} id="${escapeHtml(id)}">${inner}</h${tok.depth}>`;
  };

  const html = marked.parse(body, { gfm: true, renderer });
  return { html: html as string, headings };
}
