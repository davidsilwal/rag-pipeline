import { cn } from "@/lib/utils";

interface StatCardProps {
  label: string;
  value: string | number;
  icon?: React.ReactNode;
  className?: string;
  trend?: "up" | "down" | "neutral";
}

export function StatCard({ label, value, icon, className }: StatCardProps) {
  return (
    <div
      className={cn(
        "rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-950 p-4",
        className,
      )}
    >
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium text-zinc-500 dark:text-zinc-400">
          {label}
        </span>
        {icon && <span className="text-zinc-400">{icon}</span>}
      </div>
      <div className="mt-2 text-2xl font-bold text-zinc-900 dark:text-zinc-100">
        {value}
      </div>
    </div>
  );
}
