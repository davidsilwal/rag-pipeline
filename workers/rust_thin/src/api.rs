//! `api.rs` — Minimal HTTPS client for the Control API contract.
//!
//! Contract source: `docs/MULTI_WORKER_PLAN.md` §13 (workers/tasks endpoints)
//! plus the thin-client extensions documented in `workers/rust_thin/README.md`
//! (`/sources/by-hash/{sha}`, `/sources/{id}/blob`, `/sources/{id}/text`).
//!
//! Blocking `ureq` on purpose: one small dependency, no async runtime,
//! ~20 MB RSS. All calls are JSON over HTTPS with a bearer token.

use std::cell::RefCell;
use std::time::Duration;

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};

pub struct ApiClient {
    agent: ureq::Agent,
    base: String,
    // Mutable so we can swap the admin API_TOKEN for the per-worker token
    // issued by /workers/register (plan §13) — see `set_token`.
    token: RefCell<String>,
}

// ---------------------------------------------------------------------------
// Wire models (subset of the server schemas we interact with)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Deserialize)]
pub struct Registration {
    pub worker_id: String,
    pub token: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct ClaimedTask {
    pub task_id: String,
    pub stage: String,
    pub scope_type: String,
    pub scope_id: String,
    #[serde(default)]
    pub payload: serde_json::Value,
    pub lease_token: String,
    pub lease_expires_at: String,
}

#[derive(Debug, Deserialize)]
pub struct SourceRef {
    pub source_id: String,
}

#[derive(Debug, Deserialize)]
pub struct SourceInfo {
    pub source_id: Option<String>,
    pub mime_type: Option<String>,
    #[serde(default)]
    pub status: String,
}

/// Outcome of a claimed task, sent to the server via `settle`.
pub enum TaskResult {
    Complete { meta: serde_json::Value },
    /// `will_retry=false`: unsupported stage or poison task — the scheduler
    /// re-enqueues for a capable worker, never silently dropped.
    Fail { error: String },
}

// ---------------------------------------------------------------------------
// Client
// ---------------------------------------------------------------------------

impl ApiClient {
    pub fn new(base: &str, token: &str) -> Result<Self> {
        let agent = ureq::AgentBuilder::new()
            .timeout_connect(Duration::from_secs(10))
            // Long-poll mode holds the claim connection up to ~30 s server-side.
            .timeout_read(Duration::from_secs(45))
            .user_agent(concat!("rust-thin-worker/", env!("CARGO_PKG_VERSION")))
            .build();
        Ok(Self {
            agent,
            base: base.trim_end_matches('/').to_string(),
            token: RefCell::new(token.to_string()),
        })
    }

    /// Switch the bearer token. Callers pass the admin `API_TOKEN` at startup
    /// and swap to the per-worker token returned by `register` so that
    /// claim/complete/fail/heartbeat are validated via `optional_worker_token`.
    pub fn set_token(&self, token: &str) {
        *self.token.borrow_mut() = token.to_string();
    }

    fn url(&self, path: &str) -> String {
        format!("{}{}", self.base, path)
    }

    fn auth(&self) -> Option<String> {
        let token = self.token.borrow();
        (!token.is_empty()).then(|| format!("Bearer {}", token.as_str()))
    }

    fn post_json<T: Serialize, R: for<'de> Deserialize<'de>>(
        &self,
        path: &str,
        body: &T,
    ) -> Result<R> {
        let mut req = self.agent.post(&self.url(path));
        if let Some(a) = self.auth() {
            req = req.set("Authorization", &a);
        }
        let resp = req
            .send_json(body)
            .with_context(|| format!("POST {path} failed"))?;
        resp.into_json()
            .with_context(|| format!("decode response of POST {path}"))
    }

    // --- worker registry -------------------------------------------------

    pub fn register(
        &self,
        name: &str,
        stages_enabled: &[String],
        capabilities: &serde_json::Value,
    ) -> Result<Registration> {
        #[derive(Serialize)]
        struct Req<'a> {
            name: &'a str,
            platform: &'a str,
            version: &'a str,
            stages_enabled: Vec<&'a str>,
            capabilities: &'a serde_json::Value,
            concurrency_max: u32,
        }
        let body = Req {
            name,
            platform: "rust-thin-container",
            version: env!("CARGO_PKG_VERSION"),
            stages_enabled: stages_enabled.iter().map(String::as_str).collect(),
            capabilities,
            concurrency_max: 1,
        };
        self.post_json("/workers/register", &body)
    }

    pub fn heartbeat(&self, worker_id: &str, running: usize) -> Result<()> {
        #[derive(Serialize)]
        struct Load {
            running: usize,
            queue_len: u32,
        }
        #[derive(Serialize)]
        struct Req<'a> {
            load: Load,
        }
        let _: serde_json::Value =
            self.post_json(&format!("/workers/{worker_id}/heartbeat"), &Req {
                load: Load { running, queue_len: 0 },
            })?;
        Ok(())
    }

    pub fn deregister(&self, worker_id: &str) -> Result<()> {
        let _: serde_json::Value =
            self.post_json(&format!("/workers/{worker_id}/deregister"), &serde_json::json!({}))?;
        Ok(())
    }

    // --- task queue ------------------------------------------------------

    /// Claim up to `max_tasks` tasks for any of `stages`. With `long_poll`
    /// the server holds the connection until a task is available or ~30 s.
    /// Contract note: server may accept either `stage` (single) or `stages`
    /// (array); the array form keeps a thin device on ONE long-poll socket.
    pub fn claim(
        &self,
        worker_id: &str,
        stages: &[String],
        max_tasks: usize,
        long_poll: bool,
    ) -> Result<Vec<ClaimedTask>> {
        #[derive(Serialize)]
        struct Req<'a> {
            worker_id: &'a str,
            stages: Vec<&'a str>,
            max_tasks: usize,
            long_poll: bool,
        }
        let body = Req {
            worker_id,
            stages: stages.iter().map(String::as_str).collect(),
            max_tasks,
            long_poll,
        };
        let tasks: Vec<ClaimedTask> = self.post_json("/tasks/claim", &body)?;
        Ok(tasks)
    }

    /// Report task outcome (token-guarded on the server).
    pub fn settle(&self, task: &ClaimedTask, result: TaskResult) -> Result<()> {
        match result {
            TaskResult::Complete { meta } => {
                #[derive(Serialize)]
                struct Req<'a> {
                    lease_token: &'a str,
                    result_meta: &'a serde_json::Value,
                }
                let body = Req {
                    lease_token: &task.lease_token,
                    result_meta: &meta,
                };
                let _: serde_json::Value =
                    self.post_json(&format!("/tasks/{}/complete", task.task_id), &body)?;
            }
            TaskResult::Fail { error } => {
                #[derive(Serialize)]
                struct Req<'a> {
                    lease_token: &'a str,
                    error_message: &'a str,
                    will_retry: bool,
                }
                let body = Req {
                    lease_token: &task.lease_token,
                    error_message: &error,
                    will_retry: false,
                };
                let _: serde_json::Value =
                    self.post_json(&format!("/tasks/{}/fail", task.task_id), &body)?;
            }
        }
        Ok(())
    }

    // --- source / blob / text (thin-client extensions) -------------------

    /// True if a source with this content hash is already registered.
    pub fn source_exists_by_hash(&self, sha256: &str) -> Result<bool> {
        let mut req = self.agent.head(&self.url(&format!("/sources/by-hash/{sha256}")));
        if let Some(a) = self.auth() {
            req = req.set("Authorization", &a);
        }
        match req.call() {
            Ok(_) => Ok(true),
            Err(ureq::Error::Status(404, _)) => Ok(false),
            Err(e) => Err(e).with_context(|| format!("HEAD /sources/by-hash/{sha256}")),
        }
    }

    pub fn register_source(
        &self,
        drive_item_id: &str,
        file_path: &str,
        file_name: &str,
        mime_type: &str,
        size_bytes: u64,
        sha256: &str,
    ) -> Result<SourceRef> {
        #[derive(Serialize)]
        struct Req<'a> {
            drive_item_id: &'a str,
            drive_id: &'a str,
            file_path: &'a str,
            file_name: &'a str,
            mime_type: &'a str,
            size_bytes: u64,
            sha256_hash: &'a str,
            status: &'a str,
        }
        let body = Req {
            drive_item_id,
            drive_id: "local",
            file_path,
            file_name,
            mime_type,
            size_bytes,
            sha256_hash: sha256,
            status: "discovered",
        };
        self.post_json("/sources/register", &body)
    }

    pub fn upload_blob(&self, source_id: &str, bytes: &[u8]) -> Result<()> {
        let mut req = self.agent.post(&self.url(&format!("/sources/{source_id}/blob")));
        if let Some(a) = self.auth() {
            req = req.set("Authorization", &a);
        }
        req.set("Content-Type", "application/octet-stream")
            .send_bytes(bytes)
            .with_context(|| format!("POST /sources/{source_id}/blob"))?;
        Ok(())
    }

    pub fn get_blob(&self, source_id: &str) -> Result<Vec<u8>> {
        let mut req = self.agent.get(&self.url(&format!("/sources/{source_id}/blob")));
        if let Some(a) = self.auth() {
            req = req.set("Authorization", &a);
        }
        let mut resp = req
            .call()
            .with_context(|| format!("GET /sources/{source_id}/blob"))?;
        let mut buf = Vec::new();
        let mut reader = resp.into_reader();
        std::io::Read::read_to_end(&mut reader, &mut buf)
            .with_context(|| format!("read blob {source_id}"))?;
        Ok(buf)
    }

    pub fn get_source(&self, source_id: &str) -> Result<SourceInfo> {
        let mut req = self.agent.get(&self.url(&format!("/sources/by-id/{source_id}")));
        if let Some(a) = self.auth() {
            req = req.set("Authorization", &a);
        }
        let resp = req
            .call()
            .with_context(|| format!("GET /sources/by-id/{source_id}"))?;
        resp.into_json().with_context(|| format!("decode source {source_id}"))
    }

    pub fn post_source_text(&self, source_id: &str, text: &str) -> Result<()> {
        let mut req = self.agent.post(&self.url(&format!("/sources/{source_id}/text")));
        if let Some(a) = self.auth() {
            req = req.set("Authorization", &a);
        }
        req.set("Content-Type", "text/plain; charset=utf-8")
            .send_string(text)
            .with_context(|| format!("POST /sources/{source_id}/text"))?;
        Ok(())
    }

    pub fn get_source_text(&self, source_id: &str) -> Result<String> {
        let mut req = self.agent.get(&self.url(&format!("/sources/{source_id}/text")));
        if let Some(a) = self.auth() {
            req = req.set("Authorization", &a);
        }
        let resp = req
            .call()
            .with_context(|| format!("GET /sources/{source_id}/text"))?;
        resp.into_string().with_context(|| format!("read text {source_id}"))
    }

    /// Register chunked units for a source (server dedups by content_hash).
    pub fn post_units(&self, source_id: &str, units: &[UnitPayload]) -> Result<()> {
        #[derive(Serialize)]
        struct Req<'a> {
            source_id: &'a str,
            units: &'a [UnitPayload],
        }
        let body = Req { source_id, units };
        let _: serde_json::Value = self.post_json("/units", &body)?;
        Ok(())
    }
}

/// Minimal unit payload (matches the `units` table shape we depend on).
#[derive(Debug, Serialize)]
pub struct UnitPayload {
    pub doc_id: String,
    pub unit_index: u32,
    pub unit_type: String,
    pub heading_path: Vec<String>,
    pub raw_text: String,
    pub clean_text: String,
    pub content_hash: String,
}
