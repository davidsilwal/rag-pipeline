"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  FileStack,
  BookOpen,
  Search,
  ListTodo,
  Users,
  History,
  Activity,
  LogOut,
  Settings,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useAuth } from "@/lib/auth";
import { useAlerts } from "@/lib/hooks";

const NAV_ITEMS = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/sources", label: "Sources", icon: FileStack },
  { href: "/wiki", label: "Wiki", icon: BookOpen },
  { href: "/search", label: "Search", icon: Search },
  { href: "/tasks", label: "Tasks", icon: ListTodo },
  { href: "/workers", label: "Workers", icon: Users },
  { href: "/jobs", label: "Jobs", icon: History },
  { href: "/system", label: "System", icon: Activity },
];

export function Sidebar() {
  const pathname = usePathname();
  const { logout, token } = useAuth();
  const { data: alerts } = useAlerts();
  const alertCount = alerts?.count ?? 0;

  return (
    <aside className="hidden md:flex w-64 flex-col border-r border-zinc-200 dark:border-zinc-800 bg-zinc-50 dark:bg-zinc-950">
      <div className="flex items-center gap-2 px-4 py-4 border-b border-zinc-200 dark:border-zinc-800">
        <BookOpen className="h-6 w-6 text-indigo-600" />
        <span className="font-bold text-lg">Wiki Pipeline</span>
      </div>

      <nav className="flex-1 px-2 py-3 space-y-0.5">
        {NAV_ITEMS.map((item) => {
          const active =
            item.href === "/"
              ? pathname === "/"
              : pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-colors",
                active
                  ? "bg-indigo-100 text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-400"
                  : "text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-900",
              )}
            >
              <item.icon className="h-4 w-4" />
              {item.label}
              {item.label === "Tasks" && alertCount > 0 && (
                <span className="ml-auto text-xs bg-red-100 text-red-700 px-1.5 py-0.5 rounded-full">
                  {alertCount}
                </span>
              )}
            </Link>
          );
        })}
      </nav>

      <div className="px-3 py-3 border-t border-zinc-200 dark:border-zinc-800 space-y-2">
        <Link
          href="/system"
          className="flex items-center gap-2 text-xs text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300"
        >
          <Settings className="h-3 w-3" />
          Settings
        </Link>
        {token && (
          <button
            onClick={logout}
            className="flex items-center gap-2 text-xs text-red-500 hover:text-red-700"
          >
            <LogOut className="h-3 w-3" />
            Logout
          </button>
        )}
      </div>
    </aside>
  );
}
