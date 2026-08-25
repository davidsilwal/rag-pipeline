import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import { formatDistanceToNow } from "date-fns";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    return formatDistanceToNow(new Date(iso), { addSuffix: true });
  } catch {
    return iso;
  }
}

export function truncate(str: string, maxLen: number): string {
  if (str.length <= maxLen) return str;
  return str.slice(0, maxLen) + "…";
}

export function shortId(id: string): string {
  return id.slice(0, 8);
}

export function mimeIcon(mime: string): string {
  if (mime.startsWith("image/")) return "🖼️";
  if (mime.includes("pdf")) return "📄";
  if (mime.includes("word") || mime.includes("docx")) return "📝";
  if (mime.includes("sheet") || mime.includes("excel") || mime.includes("xlsx"))
    return "📊";
  if (mime.includes("presentation") || mime.includes("pptx")) return "📽️";
  if (mime.includes("markdown") || mime === "text/plain") return "📃";
  if (mime.includes("json") || mime.includes("yaml")) return "🔧";
  if (mime.startsWith("text/")) return "📄";
  return "📎";
}

export function stageLabel(stage: string): string {
  return stage.charAt(0).toUpperCase() + stage.slice(1);
}
