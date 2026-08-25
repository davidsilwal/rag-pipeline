"use client";

import { useState } from "react";
import Link from "next/link";
import { ArrowLeft, Copy, Check, Rocket } from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { useApi } from "@/lib/hooks";
import { PIPELINE_STAGES } from "@/lib/types";
import type { OnboardResponse } from "@/lib/types";

const PLATFORMS = ["colab", "deepnote", "docker", "rust-thin", "bare"];

export default function OnboardPage() {
  const api = useApi();
  const [name, setName] = useState("");
  const [platform, setPlatform] = useState("colab");
  const [selectedStages, setSelectedStages] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<OnboardResponse | null>(null);
  const [copied, setCopied] = useState(false);

  const toggleStage = (stage: string) => {
    setSelectedStages((prev) =>
      prev.includes(stage) ? prev.filter((s) => s !== stage) : [...prev, stage],
    );
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    setLoading(true);
    setError("");
    try {
      const res = await api.onboardWorker({
        name,
        platform,
        stages_enabled: selectedStages,
      });
      setResult(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Onboarding failed");
    } finally {
      setLoading(false);
    }
  };

  const envBlock = result
    ? Object.entries(result.env)
        .map(([k, v]) => `${k}="${v}"`)
        .join("\n")
    : "";

  const copyEnv = async () => {
    await navigator.clipboard.writeText(envBlock);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <AppShell>
      <div className="max-w-xl space-y-6">
        <Link
          href="/workers"
          className="inline-flex items-center gap-1 text-sm text-zinc-500 hover:text-zinc-700"
        >
          <ArrowLeft className="h-4 w-4" />
          Back to Workers
        </Link>

        <h2 className="flex items-center gap-2 text-lg font-bold">
          <Rocket className="h-5 w-5" />
          Onboard Worker
        </h2>

        {result ? (
          <div className="rounded-lg border border-emerald-200 dark:border-emerald-800 bg-emerald-50 dark:bg-emerald-900/10 p-6 space-y-4">
            <h3 className="font-semibold text-emerald-800 dark:text-emerald-300">
              ✅ Worker Registered
            </h3>
            <div className="text-sm space-y-2">
              <div>
                <span className="text-zinc-500">Worker ID:</span>{" "}
                <code className="font-mono text-xs bg-white dark:bg-zinc-800 px-2 py-0.5 rounded">
                  {result.worker_id}
                </code>
              </div>
              <div>
                <span className="text-zinc-500">Token:</span>{" "}
                <code className="font-mono text-xs bg-white dark:bg-zinc-800 px-2 py-0.5 rounded">
                  {result.token}
                </code>
              </div>
            </div>

            <div>
              <div className="flex items-center justify-between mb-1">
                <span className="text-sm font-medium">
                  Environment Variables
                </span>
                <button
                  onClick={copyEnv}
                  className="flex items-center gap-1 text-xs text-indigo-600 hover:text-indigo-800"
                >
                  {copied ? (
                    <>
                      <Check className="h-3 w-3" />
                      Copied
                    </>
                  ) : (
                    <>
                      <Copy className="h-3 w-3" />
                      Copy
                    </>
                  )}
                </button>
              </div>
              <pre className="text-xs font-mono bg-white dark:bg-zinc-800 p-3 rounded overflow-x-auto">
                {envBlock}
              </pre>
            </div>

            <p className="text-xs text-zinc-500">
              Paste these environment variables into your Colab/Deepnote session
              before running the worker.
            </p>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-zinc-700 dark:text-zinc-300 mb-1">
                Worker Name
              </label>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. colab-session-7f"
                className="w-full rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                required
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-zinc-700 dark:text-zinc-300 mb-1">
                Platform
              </label>
              <select
                value={platform}
                onChange={(e) => setPlatform(e.target.value)}
                className="w-full rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-2 text-sm"
              >
                {PLATFORMS.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="block text-sm font-medium text-zinc-700 dark:text-zinc-300 mb-2">
                Stages Enabled
              </label>
              <div className="flex flex-wrap gap-2">
                {PIPELINE_STAGES.map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => toggleStage(s)}
                    className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${
                      selectedStages.includes(s)
                        ? "bg-indigo-100 text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-400"
                        : "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400 hover:bg-zinc-200"
                    }`}
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>

            {error && (
              <div className="rounded-md bg-red-50 dark:bg-red-900/20 p-3 text-sm text-red-700">
                {error}
              </div>
            )}

            <button
              type="submit"
              disabled={loading || !name.trim()}
              className="w-full rounded-md bg-indigo-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-indigo-500 disabled:opacity-50"
            >
              {loading ? "Registering..." : "Register Worker"}
            </button>
          </form>
        )}
      </div>
    </AppShell>
  );
}
