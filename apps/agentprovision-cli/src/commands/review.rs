//! `alpha review` — cross-CLI consensus code review.
//!
//! See docs/plans/2026-05-18-alpha-review-consensus.md.
//!
//! Subcommands:
//!   alpha review <ref>                     POST /api/v1/reviews/start
//!   alpha review status <id>               GET  /api/v1/reviews/{id}
//!   alpha review reply <id> <ref>          POST /api/v1/reviews/{id}/reply
//!   alpha review watch <id>                GET  /api/v1/reviews/{id}/events
//!
//! `<ref>` is opaque to the server — PR number ("#570"), a commit SHA,
//! a "path/to/file.py:50-100" range, or "--stdin" for piped content
//! (which the CLI hashes to a stdin://<sha256> URL).
//!
//! Backend reference:
//!   - `ReviewWorkflow` on `agentprovision-orchestration` queue
//!     (parallel fanout over `clis`).
//!   - Findings stored on a per-review Blackboard (reuses the
//!     a2a_collaboration substrate from PR #182-#205).
//!   - Consensus heuristic: ≥ 2 CLIs flag the same (file, line_range
//!     overlap, Jaccard-similar description) → "agreed" finding.

use std::io::{self, Read};

use clap::{Args, Subcommand};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::context::Context;

#[derive(Debug, Subcommand)]
pub enum ReviewCommand {
    /// Dispatch a cross-CLI review for the given ref.
    Start(StartArgs),

    /// Print the current state of a review (rounds, status,
    /// agreed_findings).
    Status(StatusArgs),

    /// Operator: submit the updated diff/ref after fixing the agreed
    /// findings. Re-runs the review for another consensus round.
    Reply(ReplyArgs),

    /// Tail review state-transition events (SSE). Cloudflare cuts
    /// idle streams at ~100s — just re-run the command to resubscribe
    /// (see #570 for the async/queue-buffered migration).
    Watch(WatchArgs),

    /// List recent reviews for the current tenant.
    List(ListArgs),
}

#[derive(Debug, Args)]
pub struct StartArgs {
    /// Review target. PR number ("#570"), commit SHA, "path:line" /
    /// "path:start-end", or "--stdin" to hash piped content.
    ///
    /// IMPORTANT: PR refs starting with `#` MUST be quoted on the
    /// shell — `alpha review start "#570"`, not `alpha review start
    /// #570`. An unquoted `#` is a shell comment marker and the ref
    /// will be silently dropped before the CLI even sees it.
    #[arg(value_name = "REF")]
    pub r#ref: Option<String>,

    /// Read the review content from stdin and hash it into a
    /// stdin://<sha256> ref. Mutually exclusive with positional <REF>.
    #[arg(long)]
    pub stdin: bool,

    /// Comma-separated list of leaf CLIs to fan out to (e.g.
    /// `claude,codex,gemini`). Defaults to the tenant's active set.
    #[arg(long, value_delimiter = ',')]
    pub clis: Vec<String>,

    /// Review focus: "bugs+security" (default), "perf", "style",
    /// "all", or any tenant-defined scope.
    #[arg(long, default_value = "bugs+security")]
    pub scope: String,

    /// Max consensus rounds before giving up. 1..=10. Default 3.
    #[arg(long, default_value_t = 3, value_parser = clap::value_parser!(u32).range(1..=10))]
    pub max_rounds: u32,

    /// Return the review_id immediately rather than waiting.
    #[arg(long)]
    pub background: bool,
}

#[derive(Debug, Args)]
pub struct StatusArgs {
    /// Review id (uuid).
    pub id: String,
}

#[derive(Debug, Args)]
pub struct ReplyArgs {
    pub id: String,
    /// New ref (PR/SHA/path:line) after applying the agreed findings.
    pub updated_ref: String,
}

/// Tail review state-transition events via SSE.
///
/// Note: the upstream SSE stream is plain (no async-channel buffering
/// yet — see issue #570). Cloudflare's edge has a hard ~100s idle
/// ceiling on long-lived HTTP responses, so if a review sits idle
/// (waiting on a slow CLI) past that ceiling the stream will be cut
/// with a 524 by the edge even though the server is healthy. Re-run
/// `alpha review watch <id>` to resubscribe — events you missed are
/// reflected in the snapshot replayed on reconnect.
///
/// TODO(#570): migrate to the async/queue-buffered SSE pattern so the
/// edge-idle ceiling stops mattering.
#[derive(Debug, Args)]
pub struct WatchArgs {
    pub id: String,
}

#[derive(Debug, Args)]
pub struct ListArgs {
    #[arg(long, default_value_t = 20)]
    pub limit: u32,

    /// Filter by status (running | awaiting_response | done | failed).
    #[arg(long)]
    pub status: Option<String>,
}

#[derive(Debug, Serialize)]
struct StartBody {
    r#ref: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    clis: Option<Vec<String>>,
    scope: String,
    max_rounds: u32,
}

#[derive(Debug, Deserialize)]
struct CliEntry {
    #[serde(default)]
    name: String,
    #[serde(default)]
    #[allow(dead_code)]
    agent_slug: String,
}

#[derive(Debug, Deserialize)]
struct StartResponse {
    review_id: String,
    status: String,
    #[serde(default)]
    clis: Vec<CliEntry>,
    #[serde(default)]
    message: Option<String>,
}

#[derive(Debug, Deserialize)]
struct AgreedFinding {
    #[serde(default)]
    severity: String,
    #[serde(default)]
    file: Option<String>,
    #[serde(default)]
    line_range: Option<String>,
    #[serde(default)]
    descriptions: Vec<String>,
    #[serde(default)]
    cli_set: Vec<String>,
}

#[derive(Debug, Deserialize)]
struct ReviewStateResponse {
    id: String,
    #[serde(default)]
    r#ref: String,
    #[serde(default)]
    scope: String,
    #[serde(default)]
    status: String,
    #[serde(default)]
    rounds_completed: u32,
    #[serde(default)]
    max_rounds: u32,
    #[serde(default)]
    #[allow(dead_code)]
    clis: Vec<CliEntry>,
    #[serde(default)]
    agreed_findings: Vec<AgreedFinding>,
}

pub async fn dispatch(cmd: ReviewCommand, ctx: Context) -> anyhow::Result<()> {
    match cmd {
        ReviewCommand::Start(a) => start(a, ctx).await,
        ReviewCommand::Status(a) => status(a, ctx).await,
        ReviewCommand::Reply(a) => reply(a, ctx).await,
        ReviewCommand::Watch(a) => watch(a, ctx).await,
        ReviewCommand::List(a) => list(a, ctx).await,
    }
}

fn resolve_ref(args: &StartArgs) -> anyhow::Result<String> {
    if args.stdin {
        if args.r#ref.is_some() {
            anyhow::bail!("pass either <REF> or --stdin, not both");
        }
        let mut buf = String::new();
        io::stdin().read_to_string(&mut buf)?;
        if buf.trim().is_empty() {
            anyhow::bail!("--stdin requested but stdin was empty");
        }
        let mut h = Sha256::new();
        h.update(buf.as_bytes());
        let sha = format!("{:x}", h.finalize());
        Ok(format!("stdin://{}", &sha[..16]))
    } else {
        match args.r#ref.clone() {
            Some(r) if !r.trim().is_empty() => Ok(r),
            _ => anyhow::bail!("<REF> is required (or pass --stdin)"),
        }
    }
}

async fn start(args: StartArgs, ctx: Context) -> anyhow::Result<()> {
    use agentprovision_core::error::Error;
    use reqwest::Method;

    let ref_value = resolve_ref(&args)?;
    let clis = if args.clis.is_empty() {
        None
    } else {
        Some(args.clis.clone())
    };

    let body = StartBody {
        r#ref: ref_value.clone(),
        clis,
        scope: args.scope.clone(),
        max_rounds: args.max_rounds,
    };

    let req = ctx
        .client
        .request(Method::POST, "/api/v1/reviews/start")?
        .json(&body);
    let resp: StartResponse = match ctx.client.send_json(req).await {
        Ok(r) => r,
        Err(Error::Unauthorized) => {
            anyhow::bail!("not logged in — run `alpha login` first")
        }
        Err(e) => return Err(e.into()),
    };

    if ctx.json {
        println!(
            "{}",
            serde_json::to_string_pretty(&serde_json::json!({
                "review_id": resp.review_id,
                "status": resp.status,
                "ref": ref_value,
                "clis": resp.clis.iter().map(|c| c.name.clone()).collect::<Vec<_>>(),
            }))?
        );
        return Ok(());
    }
    println!("[alpha] review dispatched");
    println!("       review_id: {}", resp.review_id);
    println!("       ref: {}", ref_value);
    let names: Vec<String> = resp.clis.iter().map(|c| c.name.clone()).collect();
    println!("       clis: {}", names.join(", "));
    if let Some(m) = resp.message {
        println!("       {m}");
    }
    if !args.background {
        println!(
            "       follow with: alpha review status {} (or `alpha review watch {}`)",
            resp.review_id, resp.review_id,
        );
    }
    Ok(())
}

async fn status(args: StatusArgs, ctx: Context) -> anyhow::Result<()> {
    use agentprovision_core::error::Error;
    use reqwest::Method;

    let path = format!("/api/v1/reviews/{}", args.id);
    let req = ctx.client.request(Method::GET, &path)?;
    let state: ReviewStateResponse = match ctx.client.send_json(req).await {
        Ok(s) => s,
        Err(Error::Unauthorized) => anyhow::bail!("not logged in — run `alpha login` first"),
        Err(e) => return Err(e.into()),
    };

    if ctx.json {
        println!(
            "{}",
            serde_json::to_string_pretty(&serde_json::json!({
                "id": state.id,
                "ref": state.r#ref,
                "scope": state.scope,
                "status": state.status,
                "rounds_completed": state.rounds_completed,
                "max_rounds": state.max_rounds,
                "agreed_findings": state.agreed_findings.iter().map(|f| {
                    serde_json::json!({
                        "severity": f.severity,
                        "file": f.file,
                        "line_range": f.line_range,
                        "descriptions": f.descriptions,
                        "cli_set": f.cli_set,
                    })
                }).collect::<Vec<_>>(),
            }))?
        );
        return Ok(());
    }

    println!("[alpha] review {}", state.id);
    println!("       ref: {}", state.r#ref);
    println!(
        "       status: {} (round {}/{})",
        state.status, state.rounds_completed, state.max_rounds,
    );
    if state.agreed_findings.is_empty() {
        println!("       agreed_findings: (none yet)");
    } else {
        println!("       agreed_findings:");
        for (i, f) in state.agreed_findings.iter().enumerate() {
            let loc = match (&f.file, &f.line_range) {
                (Some(file), Some(lr)) => format!("{file}:{lr}"),
                (Some(file), None) => file.clone(),
                _ => "(no file)".into(),
            };
            let desc = f
                .descriptions
                .first()
                .map(String::as_str)
                .unwrap_or("(no description)");
            println!(
                "         {:>2}. [{}] {} — {}  (flagged by: {})",
                i + 1,
                f.severity,
                loc,
                desc,
                f.cli_set.join(", "),
            );
        }
    }
    Ok(())
}

async fn reply(args: ReplyArgs, ctx: Context) -> anyhow::Result<()> {
    use agentprovision_core::error::Error;
    use reqwest::Method;

    let path = format!("/api/v1/reviews/{}/reply", args.id);
    let body = serde_json::json!({ "updated_ref": args.updated_ref });
    let req = ctx.client.request(Method::POST, &path)?.json(&body);
    let state: ReviewStateResponse = match ctx.client.send_json(req).await {
        Ok(s) => s,
        Err(Error::Unauthorized) => anyhow::bail!("not logged in — run `alpha login` first"),
        Err(e) => return Err(e.into()),
    };

    if ctx.json {
        println!(
            "{}",
            serde_json::to_string_pretty(&serde_json::json!({
                "id": state.id,
                "status": state.status,
                "rounds_completed": state.rounds_completed,
                "ref": state.r#ref,
            }))?
        );
        return Ok(());
    }
    println!(
        "[alpha] reply submitted — review {} now {} (round {}/{})",
        state.id, state.status, state.rounds_completed, state.max_rounds,
    );
    Ok(())
}

async fn watch(args: WatchArgs, _ctx: Context) -> anyhow::Result<()> {
    // SSE consumer — v1 reuses the raw reqwest streaming pattern that
    // `tail_task_events` uses inside agentprovision-core. We keep it
    // local here (rather than threading a new helper into core) until
    // the surface stabilizes; the pretty render lives in the operator
    // workflow, not here.
    use futures_util::StreamExt;

    let path = format!("/api/v1/reviews/{}/events", args.id);
    let url = _ctx.client.build_url(&path)?;
    let mut req = _ctx
        .client
        .http()
        .get(url)
        .header("Accept", "text/event-stream");
    if let Some(tok) = _ctx.client.token() {
        req = req.header("Authorization", format!("Bearer {tok}"));
    }
    let resp = req.send().await?;
    if !resp.status().is_success() {
        anyhow::bail!(
            "review events stream failed: HTTP {}",
            resp.status().as_u16()
        );
    }
    let mut stream = resp.bytes_stream();
    while let Some(chunk) = stream.next().await {
        let bytes = chunk?;
        let text = String::from_utf8_lossy(&bytes);
        for line in text.lines() {
            if line.starts_with("data:") {
                println!("{}", line.trim_start_matches("data:").trim());
            }
        }
    }
    Ok(())
}

async fn list(args: ListArgs, ctx: Context) -> anyhow::Result<()> {
    use agentprovision_core::error::Error;
    use reqwest::Method;

    let mut req = ctx
        .client
        .request(Method::GET, "/api/v1/reviews")?
        .query(&[("limit", args.limit.to_string())]);
    if let Some(ref s) = args.status {
        req = req.query(&[("status", s.clone())]);
    }
    let rows: Vec<ReviewStateResponse> = match ctx.client.send_json(req).await {
        Ok(r) => r,
        Err(Error::Unauthorized) => anyhow::bail!("not logged in — run `alpha login` first"),
        Err(e) => return Err(e.into()),
    };

    if ctx.json {
        println!(
            "{}",
            serde_json::to_string_pretty(&serde_json::json!(rows
                .iter()
                .map(|r| serde_json::json!({
                    "id": r.id,
                    "ref": r.r#ref,
                    "status": r.status,
                    "rounds": r.rounds_completed,
                    "agreed_count": r.agreed_findings.len(),
                }))
                .collect::<Vec<_>>()))?
        );
        return Ok(());
    }
    if rows.is_empty() {
        println!("[alpha] no reviews yet for this tenant");
        return Ok(());
    }
    println!("[alpha] {} review(s):", rows.len());
    for (i, r) in rows.iter().enumerate() {
        println!(
            "  {:>2}. {} [{}] round {}/{} — {} agreed — ref={}",
            i + 1,
            r.id,
            r.status,
            r.rounds_completed,
            r.max_rounds,
            r.agreed_findings.len(),
            r.r#ref,
        );
    }
    Ok(())
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
        #[command(subcommand)]
        Review(ReviewCommand),
    }

    #[test]
    fn parses_start_with_ref() {
        let cli = TestCli::try_parse_from(["t", "review", "start", "#570"]).unwrap();
        let TestCmd::Review(ReviewCommand::Start(a)) = cli.cmd else {
            panic!()
        };
        assert_eq!(a.r#ref.as_deref(), Some("#570"));
        assert_eq!(a.scope, "bugs+security");
        assert_eq!(a.max_rounds, 3);
        assert!(a.clis.is_empty());
        assert!(!a.stdin);
    }

    #[test]
    fn parses_start_with_clis_list() {
        let cli = TestCli::try_parse_from([
            "t",
            "review",
            "start",
            "#42",
            "--clis",
            "claude,codex,gemini",
        ])
        .unwrap();
        let TestCmd::Review(ReviewCommand::Start(a)) = cli.cmd else {
            panic!()
        };
        assert_eq!(a.clis, vec!["claude", "codex", "gemini"]);
    }

    #[test]
    fn parses_start_max_rounds_in_range() {
        let cli =
            TestCli::try_parse_from(["t", "review", "start", "#1", "--max-rounds", "5"]).unwrap();
        let TestCmd::Review(ReviewCommand::Start(a)) = cli.cmd else {
            panic!()
        };
        assert_eq!(a.max_rounds, 5);
    }

    #[test]
    fn rejects_max_rounds_above_limit() {
        let parsed = TestCli::try_parse_from(["t", "review", "start", "#1", "--max-rounds", "11"]);
        assert!(parsed.is_err());
    }

    #[test]
    fn parses_reply() {
        let cli = TestCli::try_parse_from([
            "t",
            "review",
            "reply",
            "11111111-1111-1111-1111-111111111111",
            "#570-rev2",
        ])
        .unwrap();
        let TestCmd::Review(ReviewCommand::Reply(a)) = cli.cmd else {
            panic!()
        };
        assert_eq!(a.id, "11111111-1111-1111-1111-111111111111");
        assert_eq!(a.updated_ref, "#570-rev2");
    }

    #[test]
    fn parses_list_with_status_filter() {
        let cli = TestCli::try_parse_from(["t", "review", "list", "--status", "awaiting_response"])
            .unwrap();
        let TestCmd::Review(ReviewCommand::List(a)) = cli.cmd else {
            panic!()
        };
        assert_eq!(a.status.as_deref(), Some("awaiting_response"));
    }
}
