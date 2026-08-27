"use client";

import { useState } from "react";
import Link from "next/link";
import { Search as SearchIcon, FileText, Loader2 } from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { Badge } from "@/components/ui/badge";
import { useApi } from "@/lib/hooks";
import { truncate } from "@/lib/utils";
import type { SearchResult } from "@/lib/types";

export default function SearchPage() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [mode, setMode] = useState<"fts" | "hybrid">("fts");
  const [error, setError] = useState("");
  const api = useApi();

  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim()) return;
    setLoading(true);
    setError("");
    try {
      const data =
        mode === "fts"
          ? await api.searchFTS(query, 20)
          : await api.searchHybrid(query, 20);
      setResults(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Search failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <AppShell>
      <div className="space-y-6 max-w-4xl">
        <h2 className="flex items-center gap-2 text-lg font-bold">
          <SearchIcon className="h-5 w-5" />
          Search
        </h2>

        {/* Search form */}
        <form onSubmit={handleSearch} className="space-y-3">
          <div className="flex gap-2">
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search the wiki..."
              className="flex-1 rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              autoFocus
            />
            <button
              type="submit"
              disabled={loading || !query.trim()}
              className="rounded-md bg-indigo-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-indigo-500 disabled:opacity-50 flex items-center gap-2"
            >
              {loading ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <SearchIcon className="h-4 w-4" />
              )}
              Search
            </button>
          </div>
          <div className="flex items-center gap-4 text-sm">
            <label className="flex items-center gap-1.5">
              <input
                type="radio"
                name="mode"
                value="fts"
                checked={mode === "fts"}
                onChange={() => setMode("fts")}
                className="text-indigo-600"
              />
              <span className="text-zinc-600 dark:text-zinc-400">
                Full-text (lexical)
              </span>
            </label>
            <label className="flex items-center gap-1.5">
              <input
                type="radio"
                name="mode"
                value="hybrid"
                checked={mode === "hybrid"}
                onChange={() => setMode("hybrid")}
                className="text-indigo-600"
              />
              <span className="text-zinc-600 dark:text-zinc-400">
                Hybrid (semantic)
              </span>
            </label>
          </div>
        </form>

        {error && (
          <div className="rounded-md bg-red-50 dark:bg-red-900/20 p-3 text-sm text-red-700">
            {error}
          </div>
        )}

        {/* Results */}
        {results.length > 0 && (
          <div className="space-y-3">
            <p className="text-sm text-zinc-500">
              {results.length} results found
            </p>
            {results.map((r) => {
              const wikiHref = r.file_path
                ? `/wiki/${r.file_path.split("/").map(encodeURIComponent).join("/")}`
                : null;
              const card = (
                <>
                  <div className="flex items-start justify-between mb-2">
                    <div className="flex items-center gap-2 min-w-0">
                      <FileText className="h-4 w-4 text-zinc-400 shrink-0" />
                      {r.file_path && (
                        <span className="text-xs text-zinc-500 truncate">
                          {r.file_path}
                        </span>
                      )}
                    </div>
                    {r.rank != null && (
                      <Badge variant="info">
                        Score: {r.rank.toFixed(3)}
                      </Badge>
                    )}
                    {r.rrf_score != null && (
                      <Badge variant="info">
                        RRF: {r.rrf_score.toFixed(4)}
                      </Badge>
                    )}
                  </div>
                  {r.heading_path && r.heading_path.length > 0 && (
                    <div className="text-xs text-zinc-400 mb-2">
                      {r.heading_path.join(" → ")}
                    </div>
                  )}
                  {r.content && (
                    <p className="text-sm text-zinc-600 dark:text-zinc-400 line-clamp-4">
                      {truncate(r.content, 500)}
                    </p>
                  )}
                </>
              );
              return wikiHref ? (
                <Link
                  key={r.chunk_id}
                  href={wikiHref}
                  className="block rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-950 p-4 hover:border-indigo-300 dark:hover:border-indigo-700 transition-colors"
                >
                  {card}
                </Link>
              ) : (
                <div
                  key={r.chunk_id}
                  className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-950 p-4"
                >
                  {card}
                </div>
              );
            })}
          </div>
        )}

        {!loading && results.length === 0 && query && !error && (
          <div className="text-center py-12 text-zinc-400">
            <SearchIcon className="h-12 w-12 mx-auto mb-3 opacity-50" />
            <p>No results found for &quot;{query}&quot;</p>
          </div>
        )}
      </div>
    </AppShell>
  );
}
