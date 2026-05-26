//! `alpha learn <URL>` — dispatch Luna's LearnFromMediaWorkflow from the CLI.
//!
//! This is Task 4.3 + Task 4.4 of the Luna Learn from Media plan
//! (`docs/superpowers/plans/2026-05-25-luna-learn-from-media-plan.md`).
//!
//! ## Surface
//!
//! ```text
//! alpha learn <URL>                    # fire-and-forget dispatch
//! alpha learn <URL> --dry-run          # block until workflow completes
//! alpha learn --from-attachment FILE   # upload local file first, then dispatch
//! alpha learn --resume <job_id>        # resume a specific cached job
//! alpha learn --resume-last            # resume the most recent failed job
//! ```
//!
//! ## Wire shape
//!
//! 1. (Optional) `--from-attachment` first POSTs the file as `multipart/form-data`
//!    to `/api/v1/learning/upload-attachment` (T4.4b) — server returns
//!    `{attachment_path, source_url, duration_s, size_bytes}`.
//!
//! 2. POST a JSON `LearningIntent` envelope to `/api/v1/learning/dispatch`
//!    (T4.4c) — server returns `{workflow_id, ...}`.
//!
//! 3. Print `Got it, learning…  workflow_id=<id>` and exit.
//!    With `--dry-run`, poll the workflow run until it completes and print
//!    the result; without it, the user gets the completion notification via
//!    WhatsApp / chat (per spec §2 step 8).
//!
//! Note: T4.4b/c/d are separate upcoming tasks — the route and upload
//! endpoints don't exist server-side yet. Tests here exercise arg parsing
//! and intent-payload shape; HTTP I/O is covered when those endpoints land.

use std::path::PathBuf;

use clap::{ArgGroup, Args};
use serde::{Deserialize, Serialize};

use crate::context::Context;

#[derive(Debug, Args)]
#[command(group(
    // Exactly one of `<url>`, `--from-attachment`, `--resume`, or
    // `--resume-last` must be supplied. clap validates this at parse
    // time so we never reach `run()` with an unresolvable intent.
    // `--dry-run` is an orthogonal modifier — not part of the source
    // group.
    ArgGroup::new("learn_source")
        .required(true)
        .multiple(false)
        .args(["url", "from_attachment", "resume", "resume_last"])
))]
pub struct LearnArgs {
    /// Source URL (YouTube watch / shorts, youtu.be, Instagram reel/post).
    /// Mutually exclusive with `--from-attachment`, `--resume`, and
    /// `--resume-last`.
    #[arg(value_name = "URL", value_parser = non_blank_url)]
    pub url: Option<String>,

    /// Wait for the workflow to complete and print the result inline
    /// instead of fire-and-forget. Used by the synthesis-prompt golden
    /// tests in T6 (`alpha learn <fixture-url> --dry-run`).
    #[arg(long)]
    pub dry_run: bool,

    /// Upload a local audio or video file as the learning source. The
    /// file is sent to `/api/v1/learning/upload-attachment` first
    /// (server-side enforces MIME / size / duration caps per spec §1.8);
    /// the returned `attachment_path` is then dispatched as the intent.
    #[arg(long, value_name = "FILE")]
    pub from_attachment: Option<PathBuf>,

    /// Resume a specific cached learning job by id (looked up from the
    /// per-tenant `_learning_cache/<job_id>/` directory server-side).
    /// Re-dispatches with `resume_job_id` set, picking up from the
    /// failed step (reviewer-down / KG-down per T3.4).
    #[arg(long, value_name = "JOB_ID")]
    pub resume: Option<String>,

    /// Resume the most recently failed cached learning job. Equivalent
    /// to `--resume` against the newest `_learning_cache` entry; the
    /// server resolves "last" so the CLI doesn't have to enumerate
    /// the cache directly.
    #[arg(long)]
    pub resume_last: bool,
}

/// Reject empty / whitespace-only URLs at parse time so the dispatch
/// route never sees an unusable string. Mirrors `recall::non_blank_query`.
fn non_blank_url(s: &str) -> Result<String, String> {
    if s.trim().is_empty() {
        Err("url must not be empty or whitespace-only".to_string())
    } else {
        Ok(s.to_string())
    }
}

// ── Wire envelopes ──────────────────────────────────────────────────
//
// These mirror `apps/api/app/schemas/learning.py::LearningIntent`. The
// server fills in `tenant_id` + `actor_user_id` from the bearer token
// — the CLI never sends those (auth is the source of truth).

/// JSON payload posted to `/api/v1/learning/dispatch` (T4.4c).
#[derive(Debug, Serialize)]
struct DispatchRequest {
    #[serde(skip_serializing_if = "Option::is_none")]
    source_url: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    attachment_path: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    resume_job_id: Option<String>,
    /// `resume_last=true` is the server-side cue to look up the newest
    /// cached job and treat its job_id as `resume_job_id` — keeps the
    /// "find the latest" logic out of the CLI where it would have to
    /// reach into tenant-scoped storage.
    #[serde(skip_serializing_if = "std::ops::Not::not")]
    resume_last: bool,
    #[serde(skip_serializing_if = "std::ops::Not::not")]
    dry_run: bool,
}

/// Successful response from `/api/v1/learning/dispatch`.
#[derive(Debug, Deserialize, Serialize)]
struct DispatchResponse {
    workflow_id: String,
    /// Optional: when `dry_run=true` the server may block and return
    /// the final outcome inline; otherwise it's None and the CLI just
    /// prints the workflow_id.
    #[serde(default)]
    result: Option<serde_json::Value>,
    /// Optional: server may echo the resolved source_url (helpful when
    /// `--from-attachment` was used and the user wants to see the
    /// `attachment://<basename>` provenance value).
    #[serde(default)]
    source_url: Option<String>,
}

/// Response from `/api/v1/learning/upload-attachment` (T4.4b).
#[derive(Debug, Deserialize)]
struct UploadAttachmentResponse {
    attachment_path: String,
    #[serde(default)]
    #[allow(dead_code)]
    source_url: Option<String>,
    #[serde(default)]
    #[allow(dead_code)]
    duration_s: Option<u32>,
    #[serde(default)]
    #[allow(dead_code)]
    size_bytes: Option<u64>,
}

// ── Entry point ────────────────────────────────────────────────────

pub async fn run(args: LearnArgs, ctx: Context) -> anyhow::Result<()> {
    use agentprovision_core::error::Error;

    // Step 1 — resolve the source. If `--from-attachment` is set we
    // upload the file first and substitute its server-returned path
    // into the intent payload.
    let attachment_path = if let Some(path) = args.from_attachment.as_ref() {
        Some(upload_attachment(&ctx, path).await?)
    } else {
        None
    };

    let req_body = DispatchRequest {
        source_url: args.url.clone(),
        attachment_path,
        resume_job_id: args.resume.clone(),
        resume_last: args.resume_last,
        dry_run: args.dry_run,
    };

    // Step 2 — POST the intent. The server applies tenant_id +
    // actor_user_id from the bearer token before constructing the
    // `LearningIntent` model.
    let resp: DispatchResponse = match ctx
        .client
        .post_json("/api/v1/learning/dispatch", &req_body)
        .await
    {
        Ok(r) => r,
        Err(Error::Unauthorized) => anyhow::bail!("not logged in — run `alpha login` first"),
        Err(e) => return Err(e.into()),
    };

    // Step 3 — render. JSON mode emits the raw response so scripts
    // can pluck `workflow_id`; pretty mode prints the user-visible
    // ack matching spec §2 step 5 ("Got it, learning…").
    render(&args, &resp, ctx.json)
}

async fn upload_attachment(
    ctx: &Context,
    path: &std::path::Path,
) -> anyhow::Result<String> {
    use agentprovision_core::error::Error;
    use reqwest::multipart;

    if !path.exists() {
        anyhow::bail!("attachment file does not exist: {}", path.display());
    }
    let filename = path
        .file_name()
        .and_then(|s| s.to_str())
        .ok_or_else(|| anyhow::anyhow!("attachment filename is not valid UTF-8"))?
        .to_string();
    // Read into memory rather than streaming — server caps at 50MB
    // (spec §1.8) so this is bounded. Streaming would let
    // `try_clone()`-based auto-refresh fail (multipart streams are
    // not cloneable), which would break the 401 retry path users
    // expect from every other CLI verb.
    let bytes = tokio::fs::read(path).await.map_err(|e| {
        anyhow::anyhow!("could not read attachment {}: {e}", path.display())
    })?;

    let part = multipart::Part::bytes(bytes)
        .file_name(filename)
        // We don't probe the file's MIME — server-side authoritative
        // detection (per spec §1.8) does it from the actual bytes.
        // Setting `application/octet-stream` here is a no-op the
        // server will overwrite during its own classification.
        .mime_str("application/octet-stream")
        .map_err(|e| anyhow::anyhow!("invalid mime str: {e}"))?;
    let form = multipart::Form::new().part("file", part);

    let req = ctx
        .client
        .request(reqwest::Method::POST, "/api/v1/learning/upload-attachment")?
        .multipart(form);

    match ctx.client.send_json::<UploadAttachmentResponse>(req).await {
        Ok(r) => Ok(r.attachment_path),
        Err(Error::Unauthorized) => anyhow::bail!("not logged in — run `alpha login` first"),
        Err(e) => Err(e.into()),
    }
}

fn render(args: &LearnArgs, resp: &DispatchResponse, json: bool) -> anyhow::Result<()> {
    if json {
        println!("{}", serde_json::to_string_pretty(resp)?);
        return Ok(());
    }
    // Spec §2 step 5: Luna's ack is "Got it, learning…". Mirror the
    // phrasing here so the CLI surface matches the WhatsApp surface
    // — important for ops who jump between both during debugging.
    println!(
        "[alpha] Got it, learning…  workflow_id={}",
        resp.workflow_id
    );
    if args.dry_run {
        if let Some(result) = &resp.result {
            println!("[alpha] --dry-run result:");
            println!("{}", serde_json::to_string_pretty(result)?);
        } else {
            // The server-side T4.4c implementer chooses whether
            // --dry-run holds the connection or returns a
            // poll-handle. Until that lands we print a hint so the
            // user knows what to do next instead of getting nothing.
            println!(
                "[alpha] --dry-run requested but the server returned no inline result; \
                 use `alpha workflow get {}` to poll the run.",
                resp.workflow_id,
            );
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use clap::Parser;

    // ── arg-parsing harness ────────────────────────────────────────
    //
    // We define a minimal parent CLI rather than reach into the real
    // `crate::cli::Cli` so the tests don't drag in every other
    // subcommand's parser (some require feature-gated context). Same
    // pattern as `recall.rs::tests`.

    #[derive(Parser)]
    struct TestCli {
        #[command(subcommand)]
        cmd: TestCmd,
    }
    #[derive(clap::Subcommand)]
    enum TestCmd {
        Learn(LearnArgs),
    }

    fn parse(argv: &[&str]) -> Result<LearnArgs, clap::Error> {
        let cli = TestCli::try_parse_from(argv)?;
        let TestCmd::Learn(a) = cli.cmd;
        Ok(a)
    }

    // ── happy-path parsing ─────────────────────────────────────────

    #[test]
    fn parses_bare_url() {
        let a =
            parse(&["t", "learn", "https://youtu.be/dQw4w9WgXcQ"]).expect("parse ok");
        assert_eq!(a.url.as_deref(), Some("https://youtu.be/dQw4w9WgXcQ"));
        assert!(!a.dry_run);
        assert!(a.from_attachment.is_none());
        assert!(a.resume.is_none());
        assert!(!a.resume_last);
    }

    #[test]
    fn parses_url_plus_dry_run() {
        let a = parse(&[
            "t",
            "learn",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "--dry-run",
        ])
        .expect("parse ok");
        assert!(a.url.is_some());
        assert!(a.dry_run, "--dry-run should be set");
    }

    #[test]
    fn parses_from_attachment() {
        let a = parse(&["t", "learn", "--from-attachment", "/tmp/voice.ogg"])
            .expect("parse ok");
        assert!(a.url.is_none());
        assert_eq!(a.from_attachment, Some(PathBuf::from("/tmp/voice.ogg")));
    }

    #[test]
    fn parses_resume_with_job_id() {
        let a = parse(&["t", "learn", "--resume", "job-abc123"]).expect("parse ok");
        assert_eq!(a.resume.as_deref(), Some("job-abc123"));
        assert!(!a.resume_last);
    }

    #[test]
    fn parses_resume_last_alone() {
        let a = parse(&["t", "learn", "--resume-last"]).expect("parse ok");
        assert!(a.resume_last);
        assert!(a.url.is_none());
        assert!(a.resume.is_none());
    }

    #[test]
    fn dry_run_combines_with_from_attachment() {
        // `--dry-run` is orthogonal to the source group — should be
        // accepted with any single source flag.
        let a = parse(&[
            "t",
            "learn",
            "--from-attachment",
            "/tmp/v.mp4",
            "--dry-run",
        ])
        .expect("parse ok");
        assert!(a.dry_run);
        assert_eq!(a.from_attachment, Some(PathBuf::from("/tmp/v.mp4")));
    }

    // ── mutually-exclusive validation ──────────────────────────────

    #[test]
    fn rejects_url_plus_from_attachment() {
        // Two source flags = clap should refuse at parse time, before
        // we ever build a `LearningIntent`. This is the core safety
        // contract of the ArgGroup; if it regresses, the server would
        // get an ambiguous payload.
        let err = parse(&[
            "t",
            "learn",
            "https://youtu.be/dQw4w9WgXcQ",
            "--from-attachment",
            "/tmp/v.mp4",
        ])
        .expect_err("conflicting sources must error");
        // clap's `ArgGroup::multiple(false)` violation reports an
        // ArgumentConflict; pin the kind so future clap upgrades
        // surface a regression rather than a silent shape drift.
        assert_eq!(err.kind(), clap::error::ErrorKind::ArgumentConflict);
    }

    #[test]
    fn rejects_url_plus_resume() {
        let err =
            parse(&["t", "learn", "https://youtu.be/x", "--resume", "job-1"])
                .expect_err("url + resume must conflict");
        assert_eq!(err.kind(), clap::error::ErrorKind::ArgumentConflict);
    }

    #[test]
    fn rejects_resume_plus_resume_last() {
        let err = parse(&["t", "learn", "--resume", "job-1", "--resume-last"])
            .expect_err("resume + resume-last must conflict");
        assert_eq!(err.kind(), clap::error::ErrorKind::ArgumentConflict);
    }

    #[test]
    fn rejects_no_source_at_all() {
        // No URL, no --from-attachment, no --resume, no --resume-last
        // — the source group is `required(true)`, so this must error.
        let err = parse(&["t", "learn"]).expect_err("missing source must error");
        assert_eq!(err.kind(), clap::error::ErrorKind::MissingRequiredArgument);
    }

    #[test]
    fn rejects_empty_url() {
        // Whitespace-only URL is caught by `non_blank_url` value
        // parser — clap surfaces this as a value-validation error.
        let err = parse(&["t", "learn", "   "]).expect_err("blank url must error");
        assert_eq!(err.kind(), clap::error::ErrorKind::ValueValidation);
    }

    // ── wire-payload shape ─────────────────────────────────────────
    //
    // T4.4c (the dispatch route) consumes `DispatchRequest` as JSON
    // and turns it into a Python `LearningIntent`. These tests pin
    // the field names + omission rules so a rename here would force
    // both sides to be updated together.

    #[test]
    fn dispatch_payload_url_only_omits_unused_fields() {
        let body = DispatchRequest {
            source_url: Some("https://youtu.be/x".into()),
            attachment_path: None,
            resume_job_id: None,
            resume_last: false,
            dry_run: false,
        };
        let j: serde_json::Value = serde_json::to_value(&body).unwrap();
        assert_eq!(j["source_url"], "https://youtu.be/x");
        // The Python LearningIntent treats absent fields as None; we
        // serialize with `skip_serializing_if` so the JSON object
        // stays tight (no `null` keys floating around).
        assert!(j.get("attachment_path").is_none());
        assert!(j.get("resume_job_id").is_none());
        assert!(j.get("resume_last").is_none());
        assert!(j.get("dry_run").is_none());
    }

    #[test]
    fn dispatch_payload_dry_run_and_resume_last_emitted_when_true() {
        let body = DispatchRequest {
            source_url: None,
            attachment_path: None,
            resume_job_id: None,
            resume_last: true,
            dry_run: true,
        };
        let j: serde_json::Value = serde_json::to_value(&body).unwrap();
        assert_eq!(j["resume_last"], true);
        assert_eq!(j["dry_run"], true);
    }

    #[test]
    fn dispatch_payload_attachment_path_round_trips() {
        let body = DispatchRequest {
            source_url: None,
            attachment_path: Some("/var/agentprovision/workspaces/_learning/ab.mp4".into()),
            resume_job_id: None,
            resume_last: false,
            dry_run: false,
        };
        let j = serde_json::to_value(&body).unwrap();
        assert_eq!(
            j["attachment_path"],
            "/var/agentprovision/workspaces/_learning/ab.mp4"
        );
        assert!(j.get("source_url").is_none());
    }

    #[test]
    fn dispatch_response_parses_minimal_shape() {
        // Server may return just {workflow_id} for fire-and-forget.
        let raw = r#"{"workflow_id":"luna-learn-t1-abcdef012345"}"#;
        let r: DispatchResponse = serde_json::from_str(raw).expect("decode minimal");
        assert_eq!(r.workflow_id, "luna-learn-t1-abcdef012345");
        assert!(r.result.is_none());
    }

    #[test]
    fn dispatch_response_parses_dry_run_shape() {
        // With --dry-run, server may inline the final outcome.
        let raw = r#"{
            "workflow_id":"luna-learn-t1-abc",
            "result":{"skill_id":"sk_123","slug":"learned-from-yt-x"},
            "source_url":"https://youtu.be/x"
        }"#;
        let r: DispatchResponse = serde_json::from_str(raw).expect("decode dry-run");
        assert_eq!(r.workflow_id, "luna-learn-t1-abc");
        assert!(r.result.is_some());
        assert_eq!(r.source_url.as_deref(), Some("https://youtu.be/x"));
    }
}
