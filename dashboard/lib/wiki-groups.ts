/**
 * Browse groups for the wiki.
 *
 * The wiki stores pages under a folder-per-project layout, e.g.
 *   projects/agentconnect-simulator/domain-events.md
 *   projects/mozambique/docs/onboarding.md
 *   references/some-topic.md
 *
 * A group is a *path prefix* (list of path segments) — `projects/mozambique`
 * selects every page under that folder. Selecting a subfolder just extends
 * the prefix (`projects/mozambique/docs`), so drill-down works at any depth.
 * A trailing ROOT_MARK segment narrows to pages sitting directly in a folder
 * (ignoring deeper subfolders).
 *
 * These helpers are shared by the wiki list (project browser, drill-down,
 * URL param) and the wiki detail page (link back to the containing folder),
 * so the grouping can never disagree.
 */

/** A browse group = path-prefix segments, e.g. ["projects", "mozambique"]. */
export type WikiGroup = string[];

/** Sentinel last-segment meaning "only pages directly in this folder". */
export const ROOT_MARK = "*";

/** Splits a file path into segments, e.g. projects/mozambique/foo.md. */
export function wikiPathParts(filePath: string): string[] {
  return filePath.split("/").filter(Boolean);
}

/** URL-safe key for a group, e.g. `projects/mozambique`. */
export function wikiGroupKey(group: WikiGroup): string {
  return group.join("/");
}

/** Parses a `?group=` URL value back into a group (or null when empty). */
export function parseWikiGroupParam(value: string): WikiGroup | null {
  const parts = value.split("/").filter(Boolean);
  return parts.length > 0 ? parts : null;
}

/** Human label for the last segment ("Root" for the root-only sentinel). */
export function wikiGroupLabel(group: WikiGroup): string {
  const last = group[group.length - 1];
  return last === ROOT_MARK ? "Root" : (last ?? "");
}

/** Whether a page's path segments belong to the group (prefix match). */
export function matchesGroup(parts: string[], group: WikiGroup): boolean {
  if (group.length === 0) return true;
  if (group[group.length - 1] === ROOT_MARK) {
    const prefix = group.slice(0, -1);
    return (
      parts.length === prefix.length + 1 &&
      prefix.every((s, i) => parts[i] === s)
    );
  }
  return (
    parts.length >= group.length && group.every((s, i) => parts[i] === s)
  );
}

/** Containing folder of a file, e.g. its group when browsed by folder. */
export function containingGroup(filePath: string): WikiGroup | null {
  const parts = wikiPathParts(filePath);
  return parts.length >= 2 ? parts.slice(0, -1) : null;
}
