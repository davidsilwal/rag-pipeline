"use client";

import { useHealth } from "@/lib/hooks";
import { cn } from "@/lib/utils";
import { Wifi, WifiOff } from "lucide-react";

export function Header() {
  const { data: health } = useHealth();

  return (
    <header className="flex items-center justify-between px-6 py-3 border-b border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-950">
      <div className="flex items-center gap-3">
        <h1 className="text-sm font-semibold text-zinc-600 dark:text-zinc-400">
          LLM Markdown Wiki
        </h1>
      </div>

      <div className="flex items-center gap-4 text-xs">
        {health ? (
          <>
            <span
              className={cn(
                "flex items-center gap-1",
                health.postgres
                  ? "text-emerald-600"
                  : "text-red-600",
              )}
            >
              {health.postgres ? (
                <Wifi className="h-3 w-3" />
              ) : (
                <WifiOff className="h-3 w-3" />
              )}
              PG
            </span>
            <span
              className={cn(
                "flex items-center gap-1",
                health.redis ? "text-emerald-600" : "text-red-600",
              )}
            >
              {health.redis ? (
                <Wifi className="h-3 w-3" />
              ) : (
                <WifiOff className="h-3 w-3" />
              )}
              Redis
            </span>
            <span className="text-zinc-500">
              💾 {health.disk_space_gb} GB
            </span>
            <span
              className={cn(
                "px-2 py-0.5 rounded-full text-xs font-medium",
                health.status === "ok"
                  ? "bg-emerald-100 text-emerald-700"
                  : "bg-amber-100 text-amber-700",
              )}
            >
              {health.status}
            </span>
          </>
        ) : (
          <span className="text-zinc-400">Loading...</span>
        )}
      </div>
    </header>
  );
}
