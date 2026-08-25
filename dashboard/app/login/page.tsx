"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { BookOpen, Loader2 } from "lucide-react";
import { useAuth } from "@/lib/auth";
import { getApiClient } from "@/lib/api";

export default function LoginPage() {
  const [token, setToken] = useState("");
  const [apiUrl, setApiUrl] = useState(
    process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1",
  );
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const { setToken: saveToken, setApiUrl: saveApiUrl } = useAuth();
  const router = useRouter();

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);

    try {
      const client = getApiClient(apiUrl, token);
      const health = await client.getHealth();
      if (!health.postgres) {
        setError("PostgreSQL is not reachable. Check the API server.");
        setLoading(false);
        return;
      }
      saveApiUrl(apiUrl);
      saveToken(token);
      router.push("/");
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Connection failed",
      );
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-zinc-50 dark:bg-zinc-950">
      <div className="w-full max-w-md space-y-8 px-6">
        <div className="text-center">
          <BookOpen className="mx-auto h-12 w-12 text-indigo-600" />
          <h1 className="mt-4 text-2xl font-bold text-zinc-900 dark:text-zinc-100">
            LLM Markdown Wiki
          </h1>
          <p className="mt-2 text-sm text-zinc-500">
            Enter your API token to access the pipeline dashboard
          </p>
        </div>

        <form onSubmit={handleLogin} className="space-y-4">
          <div>
            <label
              htmlFor="apiUrl"
              className="block text-sm font-medium text-zinc-700 dark:text-zinc-300 mb-1"
            >
              API URL
            </label>
            <input
              id="apiUrl"
              type="url"
              value={apiUrl}
              onChange={(e) => setApiUrl(e.target.value)}
              className="w-full rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              placeholder="http://localhost:8000/api/v1"
            />
          </div>

          <div>
            <label
              htmlFor="token"
              className="block text-sm font-medium text-zinc-700 dark:text-zinc-300 mb-1"
            >
              API Token
            </label>
            <input
              id="token"
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              className="w-full rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500"
              placeholder="sk_prod_..."
              autoFocus
            />
          </div>

          {error && (
            <div className="rounded-md bg-red-50 dark:bg-red-900/20 p-3 text-sm text-red-700 dark:text-red-400">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading || !token}
            className="w-full rounded-md bg-indigo-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
          >
            {loading ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                Connecting...
              </>
            ) : (
              "Connect"
            )}
          </button>
        </form>
      </div>
    </div>
  );
}
