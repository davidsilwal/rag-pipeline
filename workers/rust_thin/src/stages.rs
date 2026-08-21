//! `stages.rs` — capability detection + the light stage handlers a thin
//! device can actually run: `discover`, `extract` (text-only), `chunk`.
//! Everything else is answered `stage_not_supported` (see `main.rs` dispatch).

use std::path::Path;

use anyhow::{Context, Result};
use sha2::{Digest, Sha256};

use crate::api::{ApiClient, UnitPayload};

// ---------------------------------------------------------------------------
// Capability advertisement (registered once at boot)
// ---------------------------------------------------------------------------

pub fn detect_capabilities() -> serde_json::Value {
    let cores = std::thread::available_parallelism()
        .map(|n| n.get())
        .unwrap_or(1);
    serde_json::json!({
        "gpu": { "present": false },                       // hard gate: never embed
        "cpu": { "cores": cores },
        "memory": { "total_mb": mem_total_mb().unwrap_or(0) },
        "disk": { "free_mb": disk_free_mb(".").unwrap_or(0) },
        "models": [],                                       // no HF models on device
        "platform": "rust-thin"
    })
}

fn mem_total_mb() -> Option<u64> {
    let content = std::fs::read_to_string("/proc/meminfo").ok()?;
    let line = content.lines().find(|l| l.starts_with("MemTotal:"))?;
    let kb: u64 = line.split_whitespace().nth(1)?.parse().ok()?;
    Some(kb / 1024)
}

fn disk_free_mb(path: &str) -> Option<u64> {
    use std::ffi::CString;
    let c = CString::new(path).ok()?;
    let mut st: libc::statvfs = unsafe { std::mem::zeroed() };
    if unsafe { libc::statvfs(c.as_ptr(), &mut st) } == 0 {
        Some((st.f_bavail as u64 * st.f_frsize as u64) / (1024 * 1024))
    } else {
        None
    }
}

// ---------------------------------------------------------------------------
// Stage: discover — scan a local root, register new files, upload blobs.
// Content-hash dedup: files whose sha256 is already known cost one HEAD call.
// ---------------------------------------------------------------------------

const IGNORED_DIRS: &[&str] = &[
    ".git", ".hg", ".svn", "node_modules", "venv", ".venv", "target", "dist",
    "build", "__pycache__", ".idea", ".vscode", ".next", ".cache",
];

pub fn discover(root: &str, client: &ApiClient) -> Result<serde_json::Value> {
    let root_path = Path::new(root);
    let mut registered = 0u32;
    let mut uploaded = 0u32;
    let mut skipped_known = 0u32;

    for entry in walkdir::WalkDir::new(root_path)
        .into_iter()
        .filter_entry(|e| {
            let is_dir = e.file_type().map(|t| t.is_dir()).unwrap_or(false);
            !is_dir || !IGNORED_DIRS.contains(&e.file_name().to_string_lossy().as_ref())
        })
    {
        let entry = entry.with_context(|| format!("walk {root}"))?;
        if !entry.file_type().map(|t| t.is_file()).unwrap_or(false) {
            continue;
        }
        let path = entry.path();
        let bytes = std::fs::read(path)
            .with_context(|| format!("read {}", path.display()))?;
        let sha = hex_sha256(&bytes);

        // Skip files the control plane already knows (by content, not path).
        if client.source_exists_by_hash(&sha)? {
            skipped_known += 1;
            continue;
        }

        let rel = path.strip_prefix(root_path).unwrap_or(path);
        let file_name = path
            .file_name()
            .map(|f| f.to_string_lossy().to_string())
            .unwrap_or_else(|| rel.to_string_lossy().to_string());
        let mime = guess_mime(path);
        // `local:` namespace keeps the unique drive_item_id server-side.
        let drive_item_id = format!("local:{sha}");

        let src = client
            .register_source(
                &drive_item_id,
                &rel.to_string_lossy(),
                &file_name,
                &mime,
                bytes.len() as u64,
                &sha,
            )
            .with_context(|| format!("register {}", rel.display()))?;
        client
            .upload_blob(&src.source_id, &bytes)
            .with_context(|| format!("upload {}", rel.display()))?;
        registered += 1;
        uploaded += 1;
    }

    Ok(serde_json::json!({
        "registered": registered,
        "uploaded": uploaded,
        "skipped_known": skipped_known,
        "root": root,
    }))
}

// ---------------------------------------------------------------------------
// Stage: extract — text-only extraction on the device. Binary mimes
// (pdf/docx/images) are deferred: the server-side fat worker extracts them.
// ---------------------------------------------------------------------------

const TEXT_MIME_PREFIXES: &[&str] = &["text/", "application/json", "application/xml", "application/yaml"];

fn is_text_mime(mime: &str) -> bool {
    TEXT_MIME_PREFIXES.iter().any(|p| mime.starts_with(*p))
}

pub fn extract(source_id: &str, client: &ApiClient) -> Result<serde_json::Value> {
    let info = client.get_source(source_id)?;
    let mime = info.mime_type.as_deref().unwrap_or("application/octet-stream");

    if is_text_mime(mime) {
        let bytes = client.get_blob(source_id)?;
        let text = String::from_utf8_lossy(&bytes);
        let n = text.chars().count();
        client.post_source_text(source_id, &text)?;
        Ok(serde_json::json!({ "extracted_chars": n, "mime": mime }))
    } else {
        // No-op completion: scheduler sees `deferred` and lets the fat worker
        // (or a server-side extractor) handle the binary.
        Ok(serde_json::json!({ "deferred": true, "reason": "binary mime — server-side extraction", "mime": mime }))
    }
}

// ---------------------------------------------------------------------------
// Stage: chunk — split extracted text into units (headings + hard cap).
// Pure string work; no memory pressure; the natural job for a thin device.
// ---------------------------------------------------------------------------

const MAX_UNIT_CHARS: usize = 2000;

pub fn chunk(source_id: &str, client: &ApiClient) -> Result<serde_json::Value> {
    let text = client.get_source_text(source_id)?;
    let units = split_units(&text);

    let payloads: Vec<UnitPayload> = units
        .iter()
        .enumerate()
        .map(|(i, (heading, body))| UnitPayload {
            doc_id: format!("{source_id}#{i}"),
            unit_index: i as u32,
            unit_type: "prose".to_string(),
            heading_path: if heading.is_empty() {
                vec![]
            } else {
                vec![heading.clone()]
            },
            raw_text: body.clone(),
            clean_text: body.clone(),
            content_hash: hex_sha256(body.as_bytes()),
        })
        .collect();

    if !payloads.is_empty() {
        client.post_units(source_id, &payloads)?;
    }
    Ok(serde_json::json!({ "units": payloads.len() }))
}

/// Split markdown-ish text into units on headings, with a hard char cap.
fn split_units(text: &str) -> Vec<(String, String)> {
    let mut units: Vec<(String, String)> = Vec::new();
    let mut heading = String::new();
    let mut current = String::new();

    for line in text.lines() {
        let trimmed = line.trim();
        if is_heading(trimmed) {
            if !current.trim().is_empty() {
                units.push((heading.clone(), std::mem::take(&mut current)));
            }
            heading = trimmed.trim_start_matches('#').trim().to_string();
            continue;
        }
        if !current.is_empty() && current.len() + line.len() + 1 > MAX_UNIT_CHARS {
            units.push((heading.clone(), std::mem::take(&mut current)));
        }
        current.push_str(line);
        current.push('\n');
    }
    if !current.trim().is_empty() {
        units.push((heading.clone(), current));
    }
    units
}

fn is_heading(line: &str) -> bool {
    let b = line.as_bytes();
    if b.first() != Some(&b'#') {
        return false;
    }
    // `# `, `## `, ..., or a bare `#` run (allow long lines that are not
    // headers, e.g. `#tag`).
    let rest = &b[1..];
    let only_hashes = rest.iter().all(|c| *c == b'#');
    rest.first().map(|c| *c == b' ' || *c == b'#').unwrap_or(false) || only_hashes
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

pub fn hex_sha256(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn guess_mime(path: &Path) -> String {
    let ext = path
        .extension()
        .map(|e| e.to_string_lossy().to_ascii_lowercase())
        .unwrap_or_default();
    let mime = match ext.as_str() {
        "md" | "markdown" => "text/markdown",
        "txt" => "text/plain",
        "rst" => "text/x-rst",
        "html" | "htm" => "text/html",
        "csv" => "text/csv",
        "json" => "application/json",
        "yaml" | "yml" => "application/yaml",
        "xml" => "application/xml",
        "pdf" => "application/pdf",
        "docx" => "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "doc" => "application/msword",
        "xlsx" => "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "pptx" => "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "svg" => "image/svg+xml",
        "py" => "text/x-python",
        "ts" | "tsx" => "text/typescript",
        "js" | "jsx" => "text/javascript",
        "go" => "text/x-go",
        "rs" => "text/x-rust",
        "sql" => "text/x-sql",
        _ => "application/octet-stream",
    };
    mime.to_string()
}
