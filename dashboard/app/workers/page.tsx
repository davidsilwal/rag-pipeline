"use client";

import Link from "next/link";
import {
  Users,
  Plus,
  RefreshCw,
  Clock,
  Cpu,
  Layers,
} from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { Badge, StatusBadge } from "@/components/ui/badge";
import { useWorkers } from "@/lib/hooks";
import { relativeTime, cn } from "@/lib/utils";

export default function WorkersPage() {
  const { data: workers, isLoading, mutate } = useWorkers();

  return (
    <AppShell>
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-lg font-bold">
            <Users className="h-5 w-5" />
            Workers
          </h2>
          <div className="flex items-center gap-3">
            <button
              onClick={() => mutate()}
              className="flex items-center gap-1 text-sm text-zinc-500 hover:text-zinc-700"
            >
              <RefreshCw className="h-3.5 w-3.5" />
              Refresh
            </button>
            <Link
              href="/workers/onboard"
              className="flex items-center gap-1 rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-indigo-500"
            >
              <Plus className="h-4 w-4" />
              Onboard Worker
            </Link>
          </div>
        </div>

        {isLoading ? (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {[...Array(3)].map((_, i) => (
              <div
                key={i}
                className="h-48 bg-zinc-100 dark:bg-zinc-800 rounded-lg animate-pulse"
              />
            ))}
          </div>
        ) : !workers || workers.length === 0 ? (
          <div className="text-center py-12 text-zinc-400">
            <Users className="h-12 w-12 mx-auto mb-3 opacity-50" />
            <p>No workers registered</p>
            <Link
              href="/workers/onboard"
              className="mt-3 inline-block text-sm text-indigo-600 hover:text-indigo-800"
            >
              Onboard your first worker →
            </Link>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {workers.map((w) => (
              <Link
                key={w.worker_id}
                href={`/workers/${w.worker_id}`}
                className="group rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-950 p-4 hover:border-indigo-300 dark:hover:border-indigo-700 transition-colors"
              >
                <div className="flex items-start justify-between mb-3">
                  <div>
                    <h3 className="font-semibold group-hover:text-indigo-600">
                      {w.name}
                    </h3>
                    <p className="text-xs text-zinc-500 mt-0.5">
                      {w.platform}
                      {w.hostname && ` • ${w.hostname}`}
                    </p>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <div
                      className={cn(
                        "h-2.5 w-2.5 rounded-full",
                        w.status === "online"
                          ? "bg-emerald-500"
                          : w.status === "draining"
                            ? "bg-amber-500"
                            : "bg-red-500",
                      )}
                    />
                    <StatusBadge status={w.status} />
                  </div>
                </div>

                <div className="grid grid-cols-3 gap-2 text-center text-xs">
                  <div className="rounded bg-zinc-50 dark:bg-zinc-900 p-2">
                    <Cpu className="h-3 w-3 mx-auto mb-0.5 text-zinc-400" />
                    <span className="font-medium">
                      {w.running_tasks ?? 0}/{w.concurrency_max}
                    </span>
                    <p className="text-zinc-400">tasks</p>
                  </div>
                  <div className="rounded bg-zinc-50 dark:bg-zinc-900 p-2">
                    <Layers className="h-3 w-3 mx-auto mb-0.5 text-zinc-400" />
                    <span className="font-medium">
                      {w.stages_enabled?.length ?? 0}
                    </span>
                    <p className="text-zinc-400">stages</p>
                  </div>
                  <div className="rounded bg-zinc-50 dark:bg-zinc-900 p-2">
                    <Clock className="h-3 w-3 mx-auto mb-0.5 text-zinc-400" />
                    <span className="font-medium text-[10px]">
                      {w.heartbeat_age_seconds != null
                        ? `${w.heartbeat_age_seconds}s`
                        : "—"}
                    </span>
                    <p className="text-zinc-400">heartbeat</p>
                  </div>
                </div>

                {w.stages_enabled && w.stages_enabled.length > 0 && (
                  <div className="mt-3 flex flex-wrap gap-1">
                    {w.stages_enabled.map((s) => (
                      <Badge key={s} className="text-[10px]">
                        {s}
                      </Badge>
                    ))}
                  </div>
                )}

                <div className="mt-3 text-xs text-zinc-400">
                  Registered {relativeTime(w.registered_at)}
                </div>
              </Link>
            ))}
          </div>
        )}
      </div>
    </AppShell>
  );
}
