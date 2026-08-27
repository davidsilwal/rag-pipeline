"use client";

import { useSyncExternalStore } from "react";
import { relativeTime } from "@/lib/utils";

const emptySubscribe = () => () => {};

/**
 * Hydration-safe relative timestamp.
 *
 * `formatDistanceToNow` is evaluated at render time, so a server-rendered
 * client component can disagree with the hydrated client render whenever the
 * minute flips between the two passes, producing a hydration mismatch.
 * useSyncExternalStore renders the server snapshot ("—") during hydration,
 * then re-renders with the live value after mount — no mismatch, no effect.
 */
export function RelativeTime({ iso }: { iso?: string | null }) {
  const hydrated = useSyncExternalStore(
    emptySubscribe,
    () => true,
    () => false,
  );

  if (!hydrated) return <span>—</span>;
  return <span>{relativeTime(iso)}</span>;
}
