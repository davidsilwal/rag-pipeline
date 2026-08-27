"use client";

import {
  Users,
  ListTodo,
  AlertTriangle,
  FileStack,
  Activity,
  Clock,
} from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { StatCard } from "@/components/ui/stat-card";
import { StatusBadge, Badge } from "@/components/ui/badge";
import { useMetrics, useAlerts, useSources } from "@/lib/hooks";
import { relativeTime, stageLabel } from "@/lib/utils";

export default function DashboardPage() {
  const { data: metrics } = useMetrics();
  const { data: alerts } = useAlerts();
  const { data: sources } = useSources(undefined, 8);

  const workerCount =
    metrics?.workers_by_status?.reduce((acc, w) => acc + w.n, 0) ?? 0;
  const onlineWorkers =
    metrics?.workers_by_status?.find((w) => w.status === "online")?.n ?? 0;

  // Aggregate queue counts (guard: API may return error object instead of array)
  const qbs = Array.isArray(metrics?.queue_by_stage_status)
    ? metrics!.queue_by_stage_status
    : [];
  const totalQueued =
    qbs.filter((q) => q.status === "queued")
      .reduce((acc, q) => acc + q.n, 0);
  const totalClaimed =
    qbs.filter((q) => q.status === "claimed")
      .reduce((acc, q) => acc + q.n, 0);
  const totalDeadLetter =
    qbs.filter((q) => q.status === "dead_letter")
      .reduce((acc, q) => acc + q.n, 0);

  // Queue by stage
  const stageQueue =
    qbs.reduce(
      (acc, q) => {
        if (!acc[q.stage]) acc[q.stage] = { queued: 0, claimed: 0, failed: 0 };
        if (q.status === "queued") acc[q.stage].queued = q.n;
        if (q.status === "claimed") acc[q.stage].claimed = q.n;
        if (q.status === "failed") acc[q.stage].failed = q.n;
        return acc;
      },
      {} as Record<string, { queued: number; claimed: number; failed: number }>,
    ) ?? {};

  return (
    <AppShell>
      <div className="space-y-6">
        {/* Stat cards */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <StatCard
            label="Workers"
            value={`${onlineWorkers} / ${workerCount}`}
            icon={<Users className="h-5 w-5" />}
          />
          <StatCard
            label="Queued Tasks"
            value={totalQueued}
            icon={<ListTodo className="h-5 w-5" />}
          />
          <StatCard
            label="Running"
            value={totalClaimed}
            icon={<Activity className="h-5 w-5" />}
          />
          <StatCard
            label="Dead Letters"
            value={totalDeadLetter}
            icon={<AlertTriangle className="h-5 w-5" />}
          />
        </div>

        {/* Alerts */}
        {Array.isArray(alerts?.alerts) && (alerts?.count ?? 0) > 0 && (
          <div className="rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-900/10 p-4">
            <h3 className="flex items-center gap-2 text-sm font-semibold text-amber-800 dark:text-amber-300 mb-2">
              <AlertTriangle className="h-4 w-4" />
              Alerts ({alerts.count})
            </h3>
            <div className="space-y-1">
              {alerts.alerts.filter(Boolean).slice(0, 5).map((alert, i) => (
                <div
                  key={i}
                  className="text-xs text-amber-700 dark:text-amber-400"
                >
                  <Badge
                    variant={
                      alert.severity === "error"
                        ? "error"
                        : alert.severity === "warning"
                          ? "warning"
                          : "info"
                    }
                  >
                    {alert.kind}
                  </Badge>
                  {"worker" in alert && ` — ${alert.worker}`}
                  {"task_id" in alert && ` — ${String(alert.task_id).slice(0, 8)} (${alert.stage})`}
                  {"count" in alert && ` — ${alert.count}`}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Queue by stage */}
        <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-950 p-4">
          <h3 className="text-sm font-semibold text-zinc-700 dark:text-zinc-300 mb-3">
            Queue by Stage
          </h3>
          {Object.keys(stageQueue).length === 0 ? (
            <p className="text-sm text-zinc-400">No queue data</p>
          ) : (
            <div className="space-y-2">
              {Object.entries(stageQueue)
                .sort(([a], [b]) => {
                  const order = [
                    "discover",
                    "extract",
                    "chunk",
                    "embed",
                    "dedup",
                    "cluster",
                    "consensus",
                    "graphrag",
                    "compile",
                  ];
                  return order.indexOf(a) - order.indexOf(b);
                })
                .map(([stage, counts]) => {
                  const total = counts.queued + counts.claimed;
                  const maxW = 300;
                  const queuedW = total > 0 ? (counts.queued / total) * maxW : 0;
                  const claimedW = total > 0 ? (counts.claimed / total) * maxW : 0;
                  return (
                    <div key={stage} className="flex items-center gap-3">
                      <span className="w-24 text-xs font-medium text-zinc-600 dark:text-zinc-400">
                        {stageLabel(stage)}
                      </span>
                      <div className="flex-1 h-5 bg-zinc-100 dark:bg-zinc-800 rounded overflow-hidden flex">
                        <div
                          className="bg-blue-400 h-full transition-all"
                          style={{ width: queuedW }}
                        />
                        <div
                          className="bg-indigo-500 h-full transition-all"
                          style={{ width: claimedW }}
                        />
                      </div>
                      <span className="text-xs text-zinc-500 w-20 text-right">
                        {counts.queued}q / {counts.claimed}c
                      </span>
                    </div>
                  );
                })}
            </div>
          )}
        </div>

        {/* Recent sources */}
        <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-950 p-4">
          <h3 className="flex items-center gap-2 text-sm font-semibold text-zinc-700 dark:text-zinc-300 mb-3">
            <FileStack className="h-4 w-4" />
            Recent Sources
          </h3>
          {!sources ? (
            <div className="animate-pulse space-y-2">
              {[...Array(3)].map((_, i) => (
                <div key={i} className="h-8 bg-zinc-100 dark:bg-zinc-800 rounded" />
              ))}
            </div>
          ) : sources.length === 0 ? (
            <p className="text-sm text-zinc-400">No sources registered</p>
          ) : (
            <div className="space-y-1">
              {sources.map((s) => (
                <a
                  key={s.source_id}
                  href={`/sources/${s.source_id}`}
                  className="flex items-center justify-between px-3 py-2 rounded hover:bg-zinc-50 dark:hover:bg-zinc-900 transition-colors"
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <span className="text-sm truncate">{s.file_name}</span>
                    <StatusBadge status={s.status} />
                  </div>
                  <span className="flex items-center gap-1 text-xs text-zinc-400 shrink-0">
                    <Clock className="h-3 w-3" />
                    {relativeTime(s.updated_at || s.created_at)}
                  </span>
                </a>
              ))}
            </div>
          )}
        </div>
      </div>
    </AppShell>
  );
}
