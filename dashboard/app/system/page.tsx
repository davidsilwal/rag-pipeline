"use client";

import {
  Activity,
  RefreshCw,
  AlertTriangle,
  HardDrive,
  Wifi,
  WifiOff,
} from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { StatusBadge, Badge } from "@/components/ui/badge";
import { StatCard } from "@/components/ui/stat-card";
import { useHealth, useMetrics, useAlerts } from "@/lib/hooks";
import { cn, stageLabel } from "@/lib/utils";

export default function SystemPage() {
  const { data: health, mutate: refreshHealth } = useHealth();
  const { data: metrics, mutate: refreshMetrics } = useMetrics();
  const { data: alerts, mutate: refreshAlerts } = useAlerts();

  const refresh = () => {
    refreshHealth();
    refreshMetrics();
    refreshAlerts();
  };

  return (
    <AppShell>
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-lg font-bold">
            <Activity className="h-5 w-5" />
            System
          </h2>
          <button
            onClick={refresh}
            className="flex items-center gap-1 text-sm text-zinc-500 hover:text-zinc-700"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            Refresh
          </button>
        </div>

        {/* Health indicators */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <StatCard
            label="PostgreSQL"
            value={health?.postgres ? "✅ Online" : "❌ Offline"}
            icon={
              health?.postgres ? (
                <Wifi className="h-5 w-5 text-emerald-500" />
              ) : (
                <WifiOff className="h-5 w-5 text-red-500" />
              )
            }
          />
          <StatCard
            label="Redis"
            value={health?.redis ? "✅ Online" : "❌ Offline"}
            icon={
              health?.redis ? (
                <Wifi className="h-5 w-5 text-emerald-500" />
              ) : (
                <WifiOff className="h-5 w-5 text-red-500" />
              )
            }
          />
          <StatCard
            label="Disk Space"
            value={`${health?.disk_space_gb ?? "—"} GB`}
            icon={<HardDrive className="h-5 w-5" />}
          />
          <StatCard
            label="Stale Leases"
            value={metrics?.stale_leases ?? "—"}
            icon={
              (metrics?.stale_leases ?? 0) > 0 ? (
                <AlertTriangle className="h-5 w-5 text-amber-500" />
              ) : (
                <Activity className="h-5 w-5" />
              )
            }
          />
        </div>

        {/* Overall status */}
        {health && (
          <div
            className={cn(
              "rounded-lg border p-4 flex items-center gap-3",
              health.status === "ok"
                ? "border-emerald-200 bg-emerald-50 dark:border-emerald-800 dark:bg-emerald-900/10"
                : "border-amber-200 bg-amber-50 dark:border-amber-800 dark:bg-amber-900/10",
            )}
          >
            <StatusBadge status={health.status} />
            <span className="text-sm font-medium">
              System status: {health.status}
            </span>
          </div>
        )}

        {/* Queue breakdown */}
        <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-950 p-4">
          <h3 className="text-sm font-semibold mb-3">Queue Breakdown</h3>
          {metrics?.queue_by_stage_status &&
          Array.isArray(metrics.queue_by_stage_status) &&
          metrics.queue_by_stage_status.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-zinc-500 text-xs">
                    <th className="pb-2">Stage</th>
                    <th className="pb-2 text-right">Queued</th>
                    <th className="pb-2 text-right">Claimed</th>
                    <th className="pb-2 text-right">Succeeded</th>
                    <th className="pb-2 text-right">Failed</th>
                    <th className="pb-2 text-right">Dead Letter</th>
                  </tr>
                </thead>
                <tbody>
                  {(() => {
                    const stageMap: Record<
                      string,
                      Record<string, number>
                    > = {};
                    for (const row of metrics.queue_by_stage_status) {
                      if (!stageMap[row.stage]) stageMap[row.stage] = {};
                      stageMap[row.stage][row.status] = row.n;
                    }
                    return Object.entries(stageMap)
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
                      .map(([stage, counts]) => (
                        <tr
                          key={stage}
                          className="border-t border-zinc-100 dark:border-zinc-800"
                        >
                          <td className="py-2 font-medium">
                            {stageLabel(stage)}
                          </td>
                          <td className="py-2 text-right">
                            {counts.queued || 0}
                          </td>
                          <td className="py-2 text-right text-blue-600">
                            {counts.claimed || 0}
                          </td>
                          <td className="py-2 text-right text-emerald-600">
                            {counts.succeeded || 0}
                          </td>
                          <td className="py-2 text-right text-red-600">
                            {counts.failed || 0}
                          </td>
                          <td className="py-2 text-right">
                            {(counts.dead_letter || 0) > 0 ? (
                              <Badge variant="error">
                                {counts.dead_letter}
                              </Badge>
                            ) : (
                              "0"
                            )}
                          </td>
                        </tr>
                      ));
                  })()}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="text-sm text-zinc-400">No queue data</p>
          )}
        </div>

        {/* Workers breakdown */}
        <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-950 p-4">
          <h3 className="text-sm font-semibold mb-3">Workers</h3>
          {metrics?.workers_by_status &&
          Array.isArray(metrics.workers_by_status) &&
          metrics.workers_by_status.length > 0 ? (
            <div className="flex gap-4">
              {metrics.workers_by_status.map((w) => (
                <div
                  key={w.status}
                  className="flex items-center gap-2 text-sm"
                >
                  <StatusBadge status={w.status} />
                  <span className="font-medium">{w.n}</span>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-zinc-400">No workers</p>
          )}
        </div>

        {/* Alerts */}
        <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-950 p-4">
          <h3 className="flex items-center gap-2 text-sm font-semibold mb-3">
            <AlertTriangle className="h-4 w-4" />
            Alerts
          </h3>
          {!alerts ? (
            <p className="text-sm text-zinc-400">Loading...</p>
          ) : alerts.count === 0 ? (
            <p className="text-sm text-emerald-600">✅ No alerts</p>
          ) : (
            <div className="space-y-2">
              {(Array.isArray(alerts.alerts) ? alerts.alerts : []).map((alert, i) => (
                <div
                  key={i}
                  className="flex items-start gap-2 text-sm p-2 rounded bg-zinc-50 dark:bg-zinc-900"
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
                    {alert.severity}
                  </Badge>
                  <div>
                    <span className="font-medium">{alert.kind}</span>
                    {"worker" in alert && (
                      <span className="text-zinc-500">
                        {" "}
                        — {String(alert.worker)}
                      </span>
                    )}
                    {"task_id" in alert && (
                      <span className="text-zinc-500">
                        {" "}
                        — task {String(alert.task_id).slice(0, 8)} (
                        {String(alert.stage)})
                      </span>
                    )}
                    {"count" in alert && (
                      <span className="text-zinc-500">
                        {" "}
                        — {String(alert.count)}
                      </span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </AppShell>
  );
}
