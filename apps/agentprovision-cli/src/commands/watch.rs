//! `alpha watch <task_id>` — resume tailing a durable task from any machine.
//!
//! Companion to `alpha run --background`. The same JWT in `~/.config/agentprovision/config.toml`
//! authorizes the watch, so a task dispatched on a laptop can be tailed
//! from a desktop without any handoff dance.
//!
//! ## JWT expiry between `run` and `watch`
//!
//! Access tokens are short-lived (~30 min). If a user runs
//! `alpha run --background` and then comes back hours later to
//! `alpha watch <task_id>`, the access token has likely expired —
//! but the task result is still on the server. `ApiClient`'s
//! auto-refresh middleware (wired in `context.rs::Context::new`)
//! transparently swaps in a fresh access token from the stored
//! refresh credential on 401, so the second watch invocation just
//! works for the duration of REFRESH_TOKEN_EXPIRE_DAYS (server
//! default 30d).
//!
//! If the refresh token has ALSO expired (multi-month gap), the
//! client surfaces the 401 verbatim and the user re-runs
//! `alpha login`. Task results persist server-side regardless, so
//! the user can resume the same `alpha watch <task_id>` right after
//! re-login — no work is lost.
//!
//! Prototype scope: polls `/tasks-fanout/{id}/status` every 1500ms.
//! Phase 1 ship swaps the loop for an SSE consumer on the existing
//! `/chat/sessions/{id}/events/stream` route reused for tasks.
//!
//! Round-1 L1: the poll loop is exported as `poll_until_terminal` and
//! reused by `alpha run` (foreground mode) so future SSE replacement is
//! a single-site change.

use clap::Args;
use serde::Deserialize;
use std::time::{Duration, Instant};

use crate::context::Context;

/// Round-1 review L3: shared poll cadence constant. Referenced by both
/// `poll_until_terminal` (legacy fallback) and the SSE-handshake-
/// failure recovery in `sse_until_terminal`.
const POLL_TICK: Duration = Duration::from_millis(1500);

#[derive(Debug, Args)]
pub struct WatchArgs {
    /// Task ID returned by `alpha run` (e.g. `t_a4f3b2c1d2e3f4a5`).
    #[arg(value_name = "TASK_ID")]
    pub task_id: String,

    /// If the task is already in a terminal state when `alpha watch` is
    /// invoked, print a single-line status and exit instead of
    /// rendering the full final result. Useful for scripted polling
    /// (`alpha watch t_xxx --no-tail-if-done --json`).
    ///
    /// Round-1 H3: previously declared but unused. Now wired.
    #[arg(long)]
    pub no_tail_if_done: bool,

    /// Maximum number of seconds to tail before exiting. The task
    /// itself keeps running on the backend — when the deadline hits,
    /// the CLI prints a "still running; resume with alpha watch <id>"
    /// hint and exits 0. Default 1800s (30 min). Round-2 L2-2: long
    /// migrations (e.g. monorepo refactors) may want `--timeout 7200`
    /// or `--timeout 0` (= no ceiling, runs until terminal).
    #[arg(long, default_value_t = 1800)]
    pub timeout: u64,

    /// Fall back to the legacy 1.5s poll loop instead of SSE. Used
    /// when the backend SSE endpoint is unavailable (older API
    /// version) or when corporate proxies strip `text/event-stream`.
    /// #188.
    #[arg(long)]
    pub poll: bool,
}

/// Status payload mirror for `GET /tasks-fanout/{id}/status`.
///
/// Mirrors `apps/api/app/api/v1/tasks_fanout.py::TaskStatusResponse`.
/// `error` (round-1 M2) is populated on `failed` / `cancelled` so the
/// CLI can render something more useful than `[alpha] t_xxx — failed`.
///
/// Round-2 L2-1: kept module-private. `poll_until_terminal` exposes
/// `Result<()>` to callers; nothing outside this module needs the
/// type names.
#[derive(Debug, Deserialize)]
struct TaskStatus {
    status: String,
    #[serde(default)]
    result: Option<String>,
    #[serde(default)]
    error: Option<String>,
    /// Children's terminal statuses, populated for fanout parent tasks.
    /// Empty for single-provider tasks.
    #[serde(default)]
    children: Vec<ChildStatus>,
}

#[derive(Debug, Deserialize)]
struct ChildStatus {
    task_id: String,
    provider: String,
    status: String,
}

pub async fn run(args: WatchArgs, ctx: Context) -> anyhow::Result<()> {
    let path = format!("/api/v1/tasks-fanout/{}/status", args.task_id);

    // Snapshot first to handle the already-done case before entering
    // the poll loop (avoids one wasted sleep when the user runs
    // `alpha watch` on a task that finished hours ago).
    let initial: TaskStatus = ctx.client.get_json(&path).await?;
    if is_terminal(&initial.status) {
        if args.no_tail_if_done {
            // Round-1 H3: scripted-polling mode. Single-line status,
            // no body, no children breakdown.
            print_terminal_short(&args.task_id, &initial, ctx.json);
        } else {
            render_terminal(&args.task_id, &initial, ctx.json);
        }
        return Ok(());
    }

    // Round-1 H4 + round-2 L2-2: deadline (None when --timeout 0).
    let deadline = (args.timeout > 0).then(|| Instant::now() + Duration::from_secs(args.timeout));
    // #188: default to SSE; `--poll` falls back to the legacy
    // 1.5s poll loop for environments where SSE isn't viable
    // (corporate proxies, older API versions).
    if args.poll {
        poll_until_terminal(&ctx, &args.task_id, deadline, POLL_TICK).await
    } else {
        sse_until_terminal(&ctx, &args.task_id, deadline).await
    }
}

/// #188: SSE-driven event consumer. Mirrors `poll_until_terminal`'s
/// output semantics (prints transitions only, returns on terminal)
/// but listens to the server's `/tasks-fanout/{id}/events/stream`
/// instead of polling /status. On terminal status (`ended`), fetches
/// the final `/status` payload and renders via `render_terminal` —
/// so SSE-mode and poll-mode output are render-identical for both
/// human and `--json` consumers (round-1 H2 + M1 fix).
///
/// Falls back to the poll loop on non-2xx SSE handshake so a
/// missing-endpoint API doesn't break the user.
pub async fn sse_until_terminal(
    ctx: &Context,
    task_id: &str,
    deadline: Option<Instant>,
) -> anyhow::Result<()> {
    use agentprovision_core::events::tail_task_events;
    use futures_util::StreamExt;

    let stream_result = tail_task_events(&ctx.client, task_id).await;
    let mut stream = match stream_result {
        Ok(s) => s,
        Err(e) => {
            // Graceful degradation: if the SSE endpoint isn't
            // available (older API, proxy stripping), fall back to
            // the legacy poll loop with a one-line warning to stderr.
            eprintln!(
                "[alpha] SSE unavailable ({e}); falling back to poll. Pass --poll to silence."
            );
            return poll_until_terminal(ctx, task_id, deadline, POLL_TICK).await;
        }
    };

    loop {
        // Round-1 review H1: wrap stream.next() in a timeout so a
        // wedged stream (heartbeat-only, no events) still respects
        // the user's --timeout. `eventsource_stream` filters SSE
        // comments per spec, so heartbeats never yield items.
        let remaining = match deadline {
            Some(d) => d.saturating_duration_since(Instant::now()),
            // No deadline → use a long-but-finite wait so we still
            // see Ctrl-C and don't accidentally block forever.
            None => Duration::from_secs(86_400),
        };
        if remaining.is_zero() {
            if !ctx.json {
                println!(
                    "[alpha] {task_id} — still running after timeout; task continues. \
                     Resume with: alpha watch {task_id}"
                );
            }
            return Ok(());
        }

        let next = tokio::time::timeout(remaining, stream.next()).await;
        let item = match next {
            Err(_elapsed) => {
                // CLI-side deadline hit (no events for the whole window).
                if !ctx.json {
                    println!(
                        "[alpha] {task_id} — still running after timeout; task continues. \
                         Resume with: alpha watch {task_id}"
                    );
                }
                return Ok(());
            }
            Ok(None) => {
                // Stream closed without `ended` event — most likely
                // a backend hiccup. Render the final state via /status
                // so SSE + poll outputs stay identical (H2 / M1).
                return finalize_terminal(ctx, task_id).await;
            }
            Ok(Some(item)) => item,
        };

        let ev = match item {
            Ok(ev) => ev,
            Err(e) => {
                eprintln!("[alpha] SSE stream error: {e}");
                return Ok(());
            }
        };

        match ev.event.as_deref() {
            Some("status") => {
                if let Some(status) = extract_field(&ev.data, "status") {
                    if ctx.json {
                        println!("{}", serde_json::json!({"status": status}));
                    } else {
                        println!("[alpha] {task_id} — {status}");
                    }
                }
            }
            Some("child_status") => {
                if !ctx.json {
                    let provider =
                        extract_field(&ev.data, "provider").unwrap_or_else(|| "?".to_string());
                    let status =
                        extract_field(&ev.data, "status").unwrap_or_else(|| "?".to_string());
                    let child_tid =
                        extract_field(&ev.data, "task_id").unwrap_or_else(|| "?".to_string());
                    println!("       child {child_tid} ({provider}) — {status}");
                }
            }
            Some("result") => {
                // Best-effort body emission. The `ended` event below
                // triggers the canonical `finalize_terminal` render
                // which fetches /status and prints everything via
                // `render_terminal` for parity with poll-mode.
            }
            Some("ended") => {
                // Round-1 review H2 + M1: SSE-mode finalization now
                // mirrors poll-mode by GETting /status one more time
                // and rendering via render_terminal. Catches the
                // `error` field on failed tasks and emits the proper
                // structured JSON payload in --json mode.
                return finalize_terminal(ctx, task_id).await;
            }
            Some("timeout") => {
                if !ctx.json {
                    println!(
                        "[alpha] server-side SSE deadline hit; task still running. \
                         Resume with: alpha watch {task_id}"
                    );
                }
                return Ok(());
            }
            Some("error") => {
                eprintln!("[alpha] server-side stream error: {}", ev.data);
                return Ok(());
            }
            _ => {
                // Unrecognized / comment-only event — ignore.
            }
        }
    }
}

/// Round-1 review H2 + M1: shared finalizer. Fetches the canonical
/// `/status` once at terminal and renders via `render_terminal` so
/// SSE and poll modes emit identical bodies (error field, result
/// field, children breakdown, --json payload shape).
async fn finalize_terminal(ctx: &Context, task_id: &str) -> anyhow::Result<()> {
    let path = format!("/api/v1/tasks-fanout/{task_id}/status");
    match ctx.client.get_json::<TaskStatus>(&path).await {
        Ok(s) => {
            render_terminal(task_id, &s, ctx.json);
            Ok(())
        }
        Err(e) => {
            // /status 404 here usually means TTL-evicted or cancelled
            // — print a minimal terminal line so the user isn't left
            // wondering whether the stream just dropped.
            if !ctx.json {
                println!("[alpha] {task_id} — terminal (final state unavailable: {e})");
            }
            Ok(())
        }
    }
}

/// Round-1 review N2: dropped the leading-underscore. JSON field
/// extractor for SSE data lines.
fn extract_field(data: &str, field: &str) -> Option<String> {
    serde_json::from_str::<serde_json::Value>(data)
        .ok()?
        .get(field)?
        .as_str()
        .map(|s| s.to_string())
}

/// Round-1 L1: shared poll loop used by `alpha run` (foreground) and
/// `alpha watch`. Prints transitions on the parent status and on every
/// child status. Returns Ok(()) on terminal status OR deadline hit.
///
/// `deadline` is the wall-clock cutoff (None = run forever, which we
/// currently never use — both callers pass a deadline now). The
/// `tick` argument is the poll cadence; 1500ms is the prototype
/// default — Phase 1 ship replaces this with SSE.
pub async fn poll_until_terminal(
    ctx: &Context,
    task_id: &str,
    deadline: Option<Instant>,
    tick: Duration,
) -> anyhow::Result<()> {
    let path = format!("/api/v1/tasks-fanout/{}/status", task_id);

    let mut last_status: Option<String> = None;
    let mut last_child_states: Vec<(String, String)> = Vec::new();

    loop {
        let s: TaskStatus = ctx.client.get_json(&path).await?;

        if last_status.as_deref() != Some(&s.status) {
            if ctx.json {
                println!("{}", serde_json::to_string(&s.status)?);
            } else {
                println!("[alpha] {} — {}", task_id, s.status);
            }
            last_status = Some(s.status.clone());
        }

        for c in &s.children {
            let prev = last_child_states
                .iter()
                .find(|(tid, _)| tid == &c.task_id)
                .map(|(_, st)| st.clone());
            if prev.as_deref() != Some(&c.status) {
                if !ctx.json {
                    println!("       child {} ({}) — {}", c.task_id, c.provider, c.status);
                }
                if let Some(entry) = last_child_states
                    .iter_mut()
                    .find(|(tid, _)| tid == &c.task_id)
                {
                    entry.1 = c.status.clone();
                } else {
                    last_child_states.push((c.task_id.clone(), c.status.clone()));
                }
            }
        }

        if is_terminal(&s.status) {
            render_terminal(task_id, &s, ctx.json);
            return Ok(());
        }

        if let Some(d) = deadline {
            if Instant::now() >= d {
                // Round-1 H4: hit the safety ceiling. The task continues
                // running on the backend; the user can resume via
                // `alpha watch <task_id>` later.
                if !ctx.json {
                    println!(
                        "[alpha] {} — still {} after timeout; task continues. \
                         Resume with: alpha watch {}",
                        task_id, s.status, task_id
                    );
                }
                return Ok(());
            }
        }
        tokio::time::sleep(tick).await;
    }
}

fn is_terminal(status: &str) -> bool {
    matches!(status, "completed" | "failed" | "cancelled")
}

/// Full terminal render: status line + final result body + any error.
fn render_terminal(task_id: &str, s: &TaskStatus, json: bool) {
    if json {
        // Final structured record. Useful for `jq` pipelines.
        let payload = serde_json::json!({
            "task_id": task_id,
            "status": s.status,
            "result": s.result,
            "error": s.error,
            "children": s.children.iter().map(|c| serde_json::json!({
                "task_id": c.task_id,
                "provider": c.provider,
                "status": c.status,
            })).collect::<Vec<_>>(),
        });
        println!("{}", serde_json::to_string_pretty(&payload).unwrap());
        return;
    }
    println!("[alpha] {task_id} — {} (terminal)", s.status);
    // Round-1 M2: render `error` before `result` so a failed task
    // shows the reason first.
    if let Some(err) = &s.error {
        println!("\n[alpha] error: {err}");
    }
    if let Some(result) = &s.result {
        println!("\n{result}");
    }
}

/// Short terminal render (round-1 H3 — `--no-tail-if-done`).
/// Single-line status + child summary; no full result body.
fn print_terminal_short(task_id: &str, s: &TaskStatus, json: bool) {
    if json {
        let payload = serde_json::json!({
            "task_id": task_id,
            "status": s.status,
            "children": s.children.iter().map(|c| serde_json::json!({
                "task_id": c.task_id,
                "provider": c.provider,
                "status": c.status,
            })).collect::<Vec<_>>(),
        });
        println!("{}", serde_json::to_string(&payload).unwrap());
        return;
    }
    println!("[alpha] {task_id} — {}", s.status);
    if !s.children.is_empty() {
        let summary: Vec<String> = s
            .children
            .iter()
            .map(|c| format!("{}:{}", c.provider, c.status))
            .collect();
        println!("       children: {}", summary.join(", "));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use clap::Parser;

    #[derive(Parser)]
    struct TestCli {
        #[command(subcommand)]
        cmd: TestCmd,
    }
    #[derive(clap::Subcommand)]
    enum TestCmd {
        Watch(WatchArgs),
    }

    #[test]
    fn parses_task_id() {
        let cli = TestCli::try_parse_from(["test", "watch", "t_a4f3b2c1d2e3f4a5"]).unwrap();
        let TestCmd::Watch(a) = cli.cmd;
        assert_eq!(a.task_id, "t_a4f3b2c1d2e3f4a5");
        assert!(!a.no_tail_if_done);
        // Round-1 H4: default timeout.
        assert_eq!(a.timeout, 1800);
    }

    #[test]
    fn parses_no_tail_if_done_and_timeout() {
        // Round-1 H3 + H4: both flags accepted.
        let cli = TestCli::try_parse_from([
            "test",
            "watch",
            "t_x",
            "--no-tail-if-done",
            "--timeout",
            "60",
        ])
        .unwrap();
        let TestCmd::Watch(a) = cli.cmd;
        assert!(a.no_tail_if_done);
        assert_eq!(a.timeout, 60);
        // #188: default is SSE (poll=false).
        assert!(!a.poll);
    }

    #[test]
    fn parses_poll_fallback_flag() {
        // #188: --poll flips to the legacy 1.5s poll loop.
        let cli = TestCli::try_parse_from(["test", "watch", "t_x", "--poll"]).unwrap();
        let TestCmd::Watch(a) = cli.cmd;
        assert!(a.poll);
    }

    #[test]
    fn extract_field_basic_shapes() {
        // SSE data shape parser. Used by the SSE consumer to pull
        // typed fields out of free-form JSON event data.
        assert_eq!(
            extract_field(r#"{"status":"running"}"#, "status"),
            Some("running".to_string())
        );
        assert_eq!(extract_field(r#"{"x":1}"#, "missing"), None);
        // Numeric fields aren't strings — extractor returns None
        // (caller handles via Option semantics, no panic).
        assert_eq!(extract_field(r#"{"x":1}"#, "x"), None);
        // Malformed JSON: graceful None.
        assert_eq!(extract_field("not-json", "status"), None);
    }

    #[test]
    fn terminal_status_classification() {
        assert!(is_terminal("completed"));
        assert!(is_terminal("failed"));
        assert!(is_terminal("cancelled"));
        assert!(!is_terminal("running"));
        assert!(!is_terminal("queued"));
        assert!(!is_terminal(""));
    }

    #[test]
    fn task_status_deserializes_with_error_field() {
        // Round-1 M2: TaskStatus mirror picks up the optional `error`
        // field added on the backend side without breaking when older
        // backends omit it.
        let no_error: TaskStatus =
            serde_json::from_str(r#"{"status":"completed","result":"ok"}"#).unwrap();
        assert!(no_error.error.is_none());
        assert_eq!(no_error.result.as_deref(), Some("ok"));

        let with_error: TaskStatus = serde_json::from_str(
            r#"{"status":"failed","error":"quota_exceeded after 12 tool calls"}"#,
        )
        .unwrap();
        assert_eq!(
            with_error.error.as_deref(),
            Some("quota_exceeded after 12 tool calls")
        );
    }
}
