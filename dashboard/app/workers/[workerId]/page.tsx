"use client";

import { use } from "react";
import Link from "next/link";
import { ArrowLeft, Clock, Cpu, Layers, Wifi } from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { StatusBadge, Badge } from "@/components/ui/badge";
import { CopyButton } from "@/components/ui/copy-button";
import { useWorker } from "@/lib/hooks";
import { relativeTime, cn } from "@/lib/utils";

export default function WorkerDetailPage({
  params,
}: {
  params: Promise<{ workerId: string }>;
}) {
  const { workerId } = use(params);
  const { data: worker, isLoading } = useWorker(workerId);

  return (
    <AppShell>
      <div className="space-y-6 max-w-3xl">
        <Link
          href="/workers"
          className="inline-flex items-center gap-1 text-sm text-zinc-500 hover:text-zinc-700"
        >
          <ArrowLeft className="h-4 w-4" />
          Back to Workers
        </Link>

        {isLoading ? (
          <div className="animate-pulse space-y-4">
            <div className="h-8 bg-zinc-100 dark:bg-zinc-800 rounded w-1/3" />
            <div className="h-32 bg-zinc-100 dark:bg-zinc-800 rounded" />
          </div>
        ) : !worker ? (
          <div className="text-zinc-400">Worker not found</div>
        ) : (
          <>
            <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-950 p-6">
              <div className="flex items-start justify-between mb-4">
                <div>
                  <h2 className="text-xl font-bold flex items-center gap-2">
                    {worker.name}
                    <div
                      className={cn(
                        "h-3 w-3 rounded-full",
                        worker.status === "online"
                          ? "bg-emerald-500"
                          : worker.status === "draining"
                            ? "bg-amber-500"
                            : "bg-red-500",
                      )}
                    />
                  </h2>
                  <p className="text-sm text-zinc-500 mt-1">
                    {worker.platform}
                    {worker.hostname && ` • ${worker.hostname}`}
                    {worker.version && ` • v${worker.version}`}
                  </p>
                </div>
                <StatusBadge status={worker.status} />
              </div>

              <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                <div>
                  <span className="text-zinc-400 text-xs">Running Tasks</span>
                  <p className="font-medium flex items-center gap-1">
                    <Cpu className="h-3 w-3" />
                    {worker.running_tasks ?? 0} / {worker.concurrency_max}
                  </p>
                </div>
                <div>
                  <span className="text-zinc-400 text-xs">Heartbeat Age</span>
                  <p className="font-medium flex items-center gap-1">
                    <Clock className="h-3 w-3" />
                    {worker.heartbeat_age_seconds != null
                      ? `${worker.heartbeat_age_seconds}s`
                      : "—"}
                  </p>
                </div>
                <div>
                  <span className="text-zinc-400 text-xs">IP</span>
                  <p className="font-medium">{worker.ip || "—"}</p>
                </div>
                <div>
                  <span className="text-zinc-400 text-xs">Registered</span>
                  <p className="font-medium">
                    {relativeTime(worker.registered_at)}
                  </p>
                </div>
              </div>

              <div className="mt-4 space-y-2 text-sm">
                <div className="flex items-center gap-2">
                  <span className="text-zinc-400 w-20 shrink-0">Worker ID</span>
                  <code className="font-mono text-xs bg-zinc-100 dark:bg-zinc-800 px-2 py-0.5 rounded">
                    {worker.worker_id}
                  </code>
                  <CopyButton text={worker.worker_id} />
                </div>
              </div>
            </div>

            {/* Stages */}
            <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-950 p-4">
              <h3 className="flex items-center gap-2 text-sm font-semibold mb-3">
                <Layers className="h-4 w-4" />
                Enabled Stages
              </h3>
              {worker.stages_enabled && worker.stages_enabled.length > 0 ? (
                <div className="flex flex-wrap gap-2">
                  {worker.stages_enabled.map((s) => (
                    <Badge key={s}>{s}</Badge>
                  ))}
                </div>
              ) : (
                <p className="text-sm text-zinc-400">No stages configured</p>
              )}
            </div>

            {/* Capabilities */}
            {worker.capabilities &&
              Object.keys(worker.capabilities).length > 0 && (
                <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-950 p-4">
                  <h3 className="flex items-center gap-2 text-sm font-semibold mb-3">
                    <Wifi className="h-4 w-4" />
                    Capabilities
                  </h3>
                  <pre className="text-xs font-mono bg-zinc-50 dark:bg-zinc-900 p-3 rounded overflow-x-auto">
                    {JSON.stringify(worker.capabilities, null, 2)}
                  </pre>
                </div>
              )}
          </>
        )}
      </div>
    </AppShell>
  );
}
