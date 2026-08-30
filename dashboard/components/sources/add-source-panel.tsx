"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  FolderOpen,
  Folder,
  FileText,
  Upload,
  Link2,
  Server,
  Loader2,
  Check,
  X,
  HardDrive,
  Plus,
} from "lucide-react";
import { useApi } from "@/lib/hooks";
import { formatBytes } from "@/lib/utils";

type Tab = "server" | "upload" | "url";

interface Entry {
  name: string;
  path: string;
  is_dir: boolean;
  mime_type: string | null;
  size_bytes: number;
}

interface BrowseNode {
  path: string;
  absolute: string;
  is_root: boolean;
  dirs: Entry[];
  files: Entry[];
}

export function AddSourcePanel({
  onClose,
  onAdded,
}: {
  onClose: () => void;
  onAdded: () => void;
}) {
  const api = useApi();
  const [tab, setTab] = useState<Tab>("server");

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-2xl rounded-xl border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 shadow-xl">
        <div className="flex items-center justify-between border-b border-zinc-200 dark:border-zinc-800 px-5 py-3">
          <h3 className="flex items-center gap-2 font-semibold">
            <Plus className="h-4 w-4" /> Add source
          </h3>
          <button
            onClick={onClose}
            className="p-1 text-zinc-400 hover:text-zinc-600"
            aria-label="Close"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-zinc-200 dark:border-zinc-800">
          {(
            [
              { id: "server", label: "Server files", icon: HardDrive },
              { id: "upload", label: "Upload", icon: Upload },
              { id: "url", label: "Link / repo", icon: Link2 },
            ] as { id: Tab; label: string; icon: typeof HardDrive }[]
          ).map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`flex flex-1 items-center justify-center gap-1.5 px-3 py-2.5 text-sm font-medium border-b-2 ${
                tab === t.id
                  ? "border-indigo-500 text-indigo-600"
                  : "border-transparent text-zinc-500 hover:text-zinc-700"
              }`}
            >
              <t.icon className="h-4 w-4" />
              {t.label}
            </button>
          ))}
        </div>

        <div className="p-5">
          {tab === "server" && (
            <ServerPicker
              api={api}
              onAdded={onAdded}
            />
          )}
          {tab === "upload" && (
            <UploadPicker api={api} onAdded={onAdded} />
          )}
          {tab === "url" && <UrlPicker api={api} onAdded={onAdded} />}
        </div>
      </div>
    </div>
  );
}

/* eslint-disable @typescript-eslint/no-explicit-any */
function ServerPicker({
  api,
  onAdded,
}: {
  api: any;
  onAdded: () => void;
}) {
  const [cwd, setCwd] = useState("");
  const [node, setNode] = useState<BrowseNode | null>(null);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<Entry | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const load = useCallback(
    async (path: string) => {
      setLoading(true);
      setSelected(null);
      try {
        const n = await api.browseIngestNode(path || undefined);
        setNode(n);
        setCwd(n.path);
        setMsg(null);
      } catch (e: any) {
        setMsg({
          ok: false,
          text: e?.message || "Failed to read directory",
        });
      } finally {
        setLoading(false);
      }
    },
    [api],
  );

  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const n = await api.browseIngestNode(undefined);
        if (!active) return;
        setNode(n);
        setCwd(n.path);
        setMsg(null);
      } catch (e: any) {
        if (active)
          setMsg({ ok: false, text: e?.message || "Failed to read directory" });
      }
    })();
    return () => {
      active = false;
    };
  }, [api]);

  const enterDir = (e: Entry) => load(e.path);
  const goUp = () => {
    // Parent = strip trailing segment
    const parts = cwd.split("/").filter(Boolean);
    parts.pop();
    load(parts.join("/"));
  };

  const add = async () => {
    if (!selected && !cwd) return;
    setBusy(true);
    setMsg(null);
    const target = selected ? selected.path : cwd;
    try {
      const res = await api.registerServerPath(target);
      setMsg({
        ok: true,
        text: `Registered ${res.registered} source${res.registered === 1 ? "" : "s"}`,
      });
      onAdded();
    } catch (e: any) {
      setMsg({ ok: false, text: e?.message || "Registration failed" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-sm">
        <FolderOpen className="h-4 w-4 text-zinc-400" />
        <span className="text-zinc-500 truncate">
          {node ? node.absolute : "…"}
        </span>
        {!node?.is_root && (
          <button
            onClick={goUp}
            className="ml-auto text-xs text-indigo-600 hover:underline"
          >
            Up one level
          </button>
        )}
      </div>

      <div className="h-64 overflow-auto rounded-md border border-zinc-200 dark:border-zinc-700 divide-y divide-zinc-100 dark:divide-zinc-800">
        {(loading || !node) && (
          <div className="flex items-center justify-center py-16 text-zinc-400">
            <Loader2 className="h-5 w-5 animate-spin" />
          </div>
        )}
        {!loading &&
          node?.dirs.map((d) => (
            <RowButton
              key={d.path}
              icon={<Folder className="h-4 w-4 text-amber-500" />}
              name={d.name}
              sub={d.path}
              selected={!!selected && selected.path === d.path}
              onClick={() => setSelected(d)}
              onDoubleClick={() => enterDir(d)}
            />
          ))}
        {!loading &&
          node?.files.map((f) => (
            <RowButton
              key={f.path}
              icon={<FileText className="h-4 w-4 text-zinc-400" />}
              name={f.name}
              sub={f.mime_type || "file"}
              extra={formatBytes(f.size_bytes)}
              selected={!!selected && selected.path === f.path}
              onClick={() => setSelected(f)}
            />
          ))}
        {!loading && node && node.dirs.length === 0 && node.files.length === 0 && (
          <div className="py-16 text-center text-zinc-400 text-sm">
            Empty folder
          </div>
        )}
      </div>

      <p className="text-xs text-zinc-400">
        Select a file to add it, or select a folder (then “Add selected
        folder”) to ingest everything under it. Double-click a folder to open
        it.
      </p>

      {msg && (
        <div
          className={`flex items-center gap-2 text-sm ${
            msg.ok ? "text-emerald-600" : "text-red-600"
          }`}
        >
          {msg.ok ? <Check className="h-4 w-4" /> : <X className="h-4 w-4" />}
          {msg.text}
        </div>
      )}

      <div className="flex items-center justify-end gap-2">
        <button
          onClick={add}
          disabled={busy || (!selected && !cwd)}
          className="inline-flex items-center gap-1.5 rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
        >
          {busy ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Server className="h-4 w-4" />
          )}
          {selected
            ? selected.is_dir
              ? "Add selected folder"
              : "Add selected file"
            : "Add current folder"}
        </button>
      </div>
    </div>
  );
}

function UploadPicker({ api, onAdded }: { api: any; onAdded: () => void }) {
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const pick = (list: FileList | null) => {
    if (!list) return;
    setFiles((prev) => Array.from(new Set([...prev, ...Array.from(list)])));
  };

  const upload = async () => {
    if (files.length === 0) return;
    setBusy(true);
    setMsg(null);
    try {
      const res = await api.uploadSources(files);
      setMsg({
        ok: true,
        text: `Uploaded ${res.registered} source${res.registered === 1 ? "" : "s"}`,
      });
      setFiles([]);
      onAdded();
    } catch (e: any) {
      setMsg({ ok: false, text: e?.message || "Upload failed" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3">
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          pick(e.dataTransfer.files);
        }}
        className="flex w-full flex-col items-center gap-2 rounded-lg border-2 border-dashed border-zinc-300 dark:border-zinc-700 px-4 py-10 text-zinc-400 hover:border-indigo-400 hover:text-indigo-500"
      >
        <Upload className="h-8 w-8" />
        <span className="text-sm">
          Drop files here, or click to choose. Use a folder picker to upload a
          whole folder.
        </span>
        {files.length > 0 && (
          <span className="text-xs text-zinc-500">
            {files.length} file(s) selected
          </span>
        )}
      </button>
      <input
        ref={inputRef}
        type="file"
        multiple
        /* webkitdirectory lets the user select an entire folder as multiple files */
        // @ts-expect-error non-standard attribute
        webkitdirectory=""
        className="hidden"
        onChange={(e) => pick(e.target.files)}
      />

      {files.length > 0 && (
        <ul className="max-h-40 space-y-1 overflow-auto rounded-md border border-zinc-200 dark:border-zinc-700 p-2 text-sm">
          {files.map((f) => (
            <li key={f.name} className="flex items-center gap-2">
              <FileText className="h-3.5 w-3.5 text-zinc-400" />
              <span className="truncate">{f.name}</span>
              <span className="ml-auto text-xs text-zinc-400">
                {formatBytes(f.size)}
              </span>
            </li>
          ))}
        </ul>
      )}

      {msg && (
        <div
          className={`flex items-center gap-2 text-sm ${
            msg.ok ? "text-emerald-600" : "text-red-600"
          }`}
        >
          {msg.ok ? <Check className="h-4 w-4" /> : <X className="h-4 w-4" />}
          {msg.text}
        </div>
      )}

      <div className="flex items-center justify-end gap-2">
        <button
          onClick={upload}
          disabled={busy || files.length === 0}
          className="inline-flex items-center gap-1.5 rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
        >
          {busy && <Loader2 className="h-4 w-4 animate-spin" />}
          Upload {busy ? "…" : `${files.length > 0 ? `${files.length} file(s)` : ""}`}
        </button>
      </div>
    </div>
  );
}

function UrlPicker({ api, onAdded }: { api: any; onAdded: () => void }) {
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const submit = async () => {
    if (!url.trim()) return;
    setBusy(true);
    setMsg(null);
    try {
      const res = await api.addUrlSource(url.trim());
      setMsg({
        ok: true,
        text: `Queued clone of ${res.url}. Files will appear after the worker pulls the repo.`,
      });
      setUrl("");
      onAdded();
    } catch (e: any) {
      setMsg({ ok: false, text: e?.message || "Failed to queue source" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3">
      <div>
        <label className="mb-1 block text-sm font-medium text-zinc-700 dark:text-zinc-300">
          Public repo / source URL
        </label>
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://github.com/org/repo"
          onKeyDown={(e) => e.key === "Enter" && submit()}
          className="w-full rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-2 text-sm"
        />
      </div>
      <p className="text-xs text-zinc-400">
        A public HTTP(S) git repo (e.g. an OSS GitHub repo). The worker clones
        it onto the ingest root and ingests every file into the pipeline.
      </p>

      {msg && (
        <div
          className={`flex items-center gap-2 text-sm ${
            msg.ok ? "text-emerald-600" : "text-red-600"
          }`}
        >
          {msg.ok ? <Check className="h-4 w-4" /> : <X className="h-4 w-4" />}
          {msg.text}
        </div>
      )}

      <div className="flex items-center justify-end gap-2">
        <button
          onClick={submit}
          disabled={busy || !url.trim()}
          className="inline-flex items-center gap-1.5 rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
        >
          {busy && <Loader2 className="h-4 w-4 animate-spin" />}
          <Link2 className="h-4 w-4" />
          {busy ? "Queuing…" : "Add source"}
        </button>
      </div>
    </div>
  );
}

function RowButton({
  icon,
  name,
  sub,
  extra,
  selected,
  onClick,
  onDoubleClick,
}: {
  icon: React.ReactNode;
  name: string;
  sub?: string;
  extra?: string;
  selected: boolean;
  onClick: () => void;
  onDoubleClick?: () => void;
}) {
  return (
    <div
      onDoubleClick={onDoubleClick}
      className={`flex cursor-pointer items-center gap-2 px-3 py-2 ${
        selected
          ? "bg-indigo-50 dark:bg-indigo-900/30"
          : "hover:bg-zinc-50 dark:hover:bg-zinc-800/50"
      }`}
      onClick={onClick}
    >
      {icon}
      <span className="truncate text-sm">{name}</span>
      {sub && (
        <span className="truncate text-xs text-zinc-400">{sub}</span>
      )}
      {extra && (
        <span className="ml-auto shrink-0 text-xs text-zinc-400">{extra}</span>
      )}
    </div>
  );
}