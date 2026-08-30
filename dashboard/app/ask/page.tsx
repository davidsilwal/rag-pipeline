"use client";

import { useState } from "react";
import Link from "next/link";
import {
  MessagesSquare,
  Send,
  Loader2,
  User,
  BookOpen,
  FileText,
  AlertCircle,
} from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { Badge } from "@/components/ui/badge";
import { useApi } from "@/lib/hooks";
import { truncate } from "@/lib/utils";
import type { ChatMessage } from "@/lib/types";

const SUGGESTIONS = [
  "What are the main topics covered in this knowledge base?",
  "Summarize the key concepts in the wiki.",
  "What does the pipeline do end to end?",
];

export default function AskPage() {
  const [query, setQuery] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const api = useApi();

  const send = async (text: string) => {
    const q = text.trim();
    if (!q || loading) return;
    setQuery("");
    setMessages((m) => [...m, { role: "user", content: q }]);
    setLoading(true);
    try {
      const data = await api.askQuestion(q, 6);
      if (data.answer) {
        setMessages((m) => [
          ...m,
          {
            role: "assistant",
            content: data.answer ?? "",
            sources: data.sources,
          },
        ]);
      } else {
        setMessages((m) => [
          ...m,
          {
            role: "assistant",
            content: data.llm_error || "No answer available.",
            error: true,
          },
        ]);
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Ask failed";
      setMessages((m) => [
        ...m,
        { role: "assistant", content: msg, error: true },
      ]);
    } finally {
      setLoading(false);
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    send(query);
  };

  return (
    <AppShell>
      <div className="flex flex-col h-[calc(100vh-8rem)] max-w-3xl mx-auto">
        <div className="flex items-center gap-2 pb-3">
          <MessagesSquare className="h-5 w-5 text-indigo-500" />
          <h2 className="text-lg font-bold">Ask the Knowledge Base</h2>
          <Badge variant="info" className="ml-1">
            RAG
          </Badge>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto space-y-4 pr-1">
          {messages.length === 0 && (
            <div className="flex flex-col items-center justify-center h-full text-center text-zinc-400 gap-3">
              <BookOpen className="h-12 w-12 opacity-40" />
              <p className="max-w-md text-sm">
                Ask a question and get a grounded, cited answer retrieved from
                the wiki.
              </p>
              <div className="flex flex-col gap-2 w-full max-w-md">
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s}
                    onClick={() => send(s)}
                    className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-950 px-4 py-2.5 text-left text-sm text-zinc-600 dark:text-zinc-400 hover:border-indigo-300 dark:hover:border-indigo-700 transition-colors"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}
          {messages.map((m, i) => (
            <div
              key={i}
              className={
                m.role === "user"
                  ? "flex items-start justify-end gap-2"
                  : "flex items-start gap-2"
              }
            >
              {m.role === "assistant" && (
                <User className="h-6 w-6 mt-1 text-indigo-500 shrink-0" />
              )}
              <div
                className={
                  m.role === "user"
                    ? "bg-indigo-600 text-white rounded-xl rounded-tr-none px-4 py-2.5 text-sm max-w-[85%]"
                    : "rounded-xl rounded-tl-none border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-950 px-4 py-2.5 text-sm max-w-[90%] space-y-2"
                }
              >
                <p className="whitespace-pre-wrap">{m.content}</p>
                {m.sources && m.sources.length > 0 && (
                  <div className="pt-2 border-t border-zinc-100 dark:border-zinc-800">
                    <p className="text-xs font-semibold text-zinc-500 mb-1.5">
                      Sources
                    </p>
                    <div className="space-y-1">
                      {m.sources.slice(0, 5).map((s) => {
                        const href = s.file_path
                          ? `/wiki/${s.file_path
                              .split("/")
                              .map(encodeURIComponent)
                              .join("/")}`
                          : null;
                        const label = s.file_path || s.chunk_id;
                        const inner = (
                          <>
                            <FileText className="h-3.5 w-3.5 text-zinc-400 shrink-0" />
                            <span className="truncate text-xs">
                              {truncate(label, 80)}
                            </span>
                          </>
                        );
                        return href ? (
                          <Link
                            key={s.chunk_id}
                            href={href}
                            className="flex items-center gap-1.5 text-zinc-500 hover:text-indigo-600"
                          >
                            {inner}
                          </Link>
                        ) : (
                          <div
                            key={s.chunk_id}
                            className="flex items-center gap-1.5 text-zinc-500"
                          >
                            {inner}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}
              </div>
            </div>
          ))}
          {loading && (
            <div className="flex items-start gap-2">
              <User className="h-6 w-6 mt-1 text-indigo-500" />
              <div className="flex items-center gap-2 rounded-xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-950 px-4 py-2.5 text-sm text-zinc-500">
                <Loader2 className="h-4 w-4 animate-spin" />
                Retrieving and synthesizing…
              </div>
            </div>
          )}
          {messages.some((m) => m.error) && (
            <div className="flex items-center gap-2 text-xs text-amber-600">
              <AlertCircle className="h-3.5 w-3.5" />
              One or more answers could not be generated. Retrieved sources are
              shown where available.
            </div>
          )}
        </div>

        {/* Input */}
        <form onSubmit={handleSubmit} className="mt-3 flex gap-2">
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Ask a question about the knowledge base…"
            className="flex-1 rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
          />
          <button
            type="submit"
            disabled={loading || !query.trim()}
            className="rounded-md bg-indigo-600 px-4 py-2.5 text-white hover:bg-indigo-500 disabled:opacity-50 flex items-center gap-2"
          >
            {loading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Send className="h-4 w-4" />
            )}
            Ask
          </button>
        </form>
      </div>
    </AppShell>
  );
}