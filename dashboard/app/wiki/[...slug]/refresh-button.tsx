"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { RefreshCw } from "lucide-react";

export function RefreshButton() {
  const router = useRouter();
  const [refreshing, setRefreshing] = useState(false);

  const handleRefresh = () => {
    setRefreshing(true);
    // router.refresh() re-runs the server components on this route, so the
    // page re-fetches the wiki page and flips to the compiled view if the
    // compile task finished since the last render.
    router.refresh();
    setTimeout(() => setRefreshing(false), 1000);
  };

  return (
    <button
      type="button"
      onClick={handleRefresh}
      disabled={refreshing}
      className="inline-flex items-center gap-1.5 rounded-md bg-amber-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-amber-500 disabled:opacity-50"
    >
      <RefreshCw className={`h-3 w-3 ${refreshing ? "animate-spin" : ""}`} />
      {refreshing ? "Checking..." : "Refresh"}
    </button>
  );
}
