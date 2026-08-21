//! `main.rs` — rust-thin-worker entry point.
//!
//! Ultra-lightweight RAG pipeline worker (T2 relay profile, plan §9A):
//!   register → claim (long-poll) → run light stages → complete/fail → heartbeat
//!
//! No DB, no models, no torch, no async runtime. Single-threaded, blocking.
//! Side-by-side with the fat Python worker (`rag-pipeline-worker`).

mod api;
mod stages;

use std::env;
use std::process::exit;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

use anyhow::{Context, Result};

use api::{ApiClient, ClaimedTask, TaskResult};

// ---------------------------------------------------------------------------
// Configuration (README has the full table)
// ---------------------------------------------------------------------------

struct Config {
    control_api_url: String,
    api_token: String,
    worker_name: String,
    stages_enabled: Vec<String>,
    discover_root: String,
    long_poll: bool,
    max_claim: usize,
    poll_interval_secs: u64,
}

fn cfg_from_env() -> Config {
    Config {
        control_api_url: env::var("CONTROL_API_URL")
            .unwrap_or_else(|_| "http://control-api:8000/api/v1".into()),
        api_token: env::var("API_TOKEN").unwrap_or_default(),
        // Docker sets HOSTNAME per container → unique default even with
        // `--scale worker-thin=N`. Set WORKER_NAME for readable names.
        worker_name: env::var("WORKER_NAME")
            .or_else(|_| env::var("HOSTNAME"))
            .unwrap_or_else(|_| "rust-thin".into()),
        stages_enabled: env::var("STAGES_ENABLED")
            .unwrap_or_else(|_| "discover,extract,chunk".into())
            .split(',')
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
            .collect(),
        discover_root: env::var("DISCOVER_ROOT").unwrap_or_else(|_| "/workspace".into()),
        long_poll: env::var("LONG_POLL").map(|v| v == "1").unwrap_or(true),
        max_claim: env::var("MAX_CLAIM")
            .ok()
            .and_then(|v| v.parse().ok())
            .unwrap_or(1),
        poll_interval_secs: env::var("POLL_INTERVAL_SECS")
            .ok()
            .and_then(|v| v.parse().ok())
            .unwrap_or(30),
    }
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

fn main() {
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();

    let cfg = cfg_from_env();
    if cfg.api_token.is_empty() {
        log::warn!("API_TOKEN is empty — Control API will reject unauthenticated requests");
    }

    // Graceful shutdown: finish the current tick, drain, deregister.
    static SHUTDOWN: AtomicBool = AtomicBool::new(false);
    ctrlc::set_handler(|| SHUTDOWN.store(true, Ordering::SeqCst))
        .expect("failed to install SIGINT/SIGTERM handler");

    let client = match ApiClient::new(&cfg.control_api_url, &cfg.api_token) {
        Ok(c) => c,
        Err(e) => {
            log::error!("failed to build API client: {e:#}");
            exit(2);
        }
    };

    let caps = stages::detect_capabilities();
    log::info!(
        "registering worker '{}' stages=[{}] caps={}",
        cfg.worker_name,
        cfg.stages_enabled.join(","),
        caps
    );

    let reg = match client.register(&cfg.worker_name, &cfg.stages_enabled, &caps) {
        Ok(r) => r,
        Err(e) => {
            log::error!("registration failed (is the Control API up?): {e:#}");
            exit(1);
        }
    };
    log::info!("registered worker_id={} (token withheld)", reg.worker_id);

    // Authenticate with the per-worker token from here on (plan §13), so
    // claim/complete/fail/heartbeat are validated via optional_worker_token.
    client.set_token(&reg.token);

    let mut consecutive_failures: u32 = 0;
    while !SHUTDOWN.load(Ordering::SeqCst) {
        match tick(&client, &cfg, &reg.worker_id) {
            Ok(worked) => {
                consecutive_failures = 0;
                if !worked {
                    sleep_secs(cfg.poll_interval_secs);
                }
            }
            Err(e) => {
                consecutive_failures = consecutive_failures.saturating_add(1);
                let backoff = 2u64.saturating_mul(1u64 << consecutive_failures.min(5).min(30));
                log::warn!("tick failed: {e:#}; backing off {backoff}s");
                sleep_secs(backoff);
            }
        }
    }

    // Drain & deregister.
    match client.deregister(&reg.worker_id) {
        Ok(()) => log::info!("worker '{}' drained and deregistered", cfg.worker_name),
        Err(e) => log::warn!("deregister failed (lease will expire naturally): {e:#}"),
    }
}

// ---------------------------------------------------------------------------
// One tick: claim → dispatch → settle → heartbeat
// ---------------------------------------------------------------------------

fn tick(client: &ApiClient, cfg: &Config, worker_id: &str) -> Result<bool> {
    let claimed = client
        .claim(worker_id, &cfg.stages_enabled, cfg.max_claim, cfg.long_poll)
        .context("claim")?;

    for task in &claimed {
        let outcome = dispatch(task, cfg, client);
        if let Err(e) = client.settle(task, outcome) {
            // A stale lease (task reclaimed after our lease expired) is
            // expected on flaky networks — log and move on.
            log::warn!("settle task {} failed: {e:#}", task.task_id);
        }
    }

    // Heartbeat once per tick: cadence = work time + poll interval ≤ TTL(120s).
    client
        .heartbeat(worker_id, claimed.len())
        .context("heartbeat")?;

    Ok(!claimed.is_empty())
}

/// Run one claimed task. Any stage we cannot run is answered
/// `stage_not_supported` (will_retry=false) — the scheduler re-enqueues it
/// for a capable worker. A thin node never silently drops a task.
fn dispatch(task: &ClaimedTask, cfg: &Config, client: &ApiClient) -> TaskResult {
    if !cfg.stages_enabled.iter().any(|s| s == &task.stage) {
        return TaskResult::Fail {
            error: format!("stage_not_supported: {}", task.stage),
        };
    }

    let result = match task.stage.as_str() {
        "discover" => stages::discover(&cfg.discover_root, client),
        "extract" => stages::extract(&task.scope_id, client),
        "chunk" => stages::chunk(&task.scope_id, client),
        other => Err(anyhow::anyhow!("stage_not_supported: {other}")),
    };

    match result {
        Ok(meta) => {
            log::info!(
                "task {} stage={} scope={}:{} ok meta={}",
                task.task_id, task.stage, task.scope_type, task.scope_id, meta
            );
            TaskResult::Complete { meta }
        }
        Err(e) => {
            log::error!(
                "task {} stage={} scope={}:{} failed: {e:#}",
                task.task_id, task.stage, task.scope_type, task.scope_id
            );
            TaskResult::Fail { error: format!("{e:#}") }
        }
    }
}

fn sleep_secs(secs: u64) {
    std::thread::sleep(Duration::from_secs(secs));
}
