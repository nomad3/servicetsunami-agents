//! `alpha workspace` — manage the tenant's persistent workspace tree.
//!
//! Backs the new clone capability (task #255) so users can pull their
//! work repos (Levi's SRE, Integral SRE, ...) into the same
//! `/var/agentprovision/workspaces/<tenant_id>/projects/<repo>/` tree
//! the dashboard's Files mode reads from. Per the Alpha CLI kernel
//! principle the verb calls the internal API (`POST
//! /api/v1/workspace/clone`) — it does NOT shell out to `git` on the
//! client side. The server resolves the user's github integration
//! token, kicks off the clone in BackgroundTasks, and returns a
//! job_id immediately.

use std::io::IsTerminal;

use clap::{Args, Subcommand};

use crate::context::Context;
use crate::output;

#[derive(Debug, Subcommand)]
pub enum WorkspaceCommand {
    /// Clone a GitHub repo into the caller's tenant workspace.
    ///
    /// The repo lands at
    /// `/var/agentprovision/workspaces/<tenant_id>/projects/<repo>/`
    /// and is immediately readable via the dashboard's Files mode and
    /// the `/api/v1/workspace/tree` endpoint. Re-running the verb on
    /// an existing target performs `fetch + reset --hard` rather than
    /// a fresh clone (idempotent).
    Clone(CloneArgs),
}

#[derive(Debug, Args)]
pub struct CloneArgs {
    /// Repository to clone. Either `owner/name` or
    /// `https://github.com/owner/name`. Validated server-side against
    /// a strict regex — shell metas, `..`, and non-github URLs are
    /// rejected with 400.
    #[arg(value_name = "REPO")]
    pub repo: String,

    /// Branch to checkout. Defaults to the repo's default branch
    /// (server-side); explicit value bypasses `--depth=1`'s default
    /// branch resolution.
    #[arg(long, value_name = "BRANCH")]
    pub branch: Option<String>,

    /// Overwrite local changes when re-cloning into a dirty target.
    /// Without this flag the server refuses (409) so users can't lose
    /// uncommitted work. The CLI prompts for confirmation before
    /// propagating `force=true` in interactive runs; `--yes` skips
    /// the prompt for scripted callers.
    #[arg(long)]
    pub force: bool,

    /// Skip the dirty-overwrite confirmation prompt. Pair with
    /// `--force` for non-interactive automation.
    #[arg(long)]
    pub yes: bool,
}

pub async fn dispatch(cmd: WorkspaceCommand, ctx: Context) -> anyhow::Result<()> {
    match cmd {
        WorkspaceCommand::Clone(a) => clone(a, ctx).await,
    }
}

async fn clone(args: CloneArgs, ctx: Context) -> anyhow::Result<()> {
    // Interactive confirmation before propagating `force=true` — keeps
    // a stray `--force` from silently destroying local edits. Non-tty
    // / `--yes` callers skip the prompt.
    if args.force && !args.yes && std::io::stdin().is_terminal() {
        eprintln!("[alpha] --force will discard any local changes in the target. Continue? [y/N]");
        let mut answer = String::new();
        std::io::stdin().read_line(&mut answer)?;
        let answer = answer.trim().to_lowercase();
        if answer != "y" && answer != "yes" {
            output::info("aborted.");
            return Ok(());
        }
    }

    let resp = ctx
        .client
        .clone_workspace_repo(&args.repo, args.branch.as_deref(), args.force)
        .await?;

    if ctx.json {
        crate::output::emit(true, &resp, |_| {});
        return Ok(());
    }

    let branch_hint = match resp.branch.as_deref() {
        Some(b) => format!(" branch={b}"),
        None => String::new(),
    };
    output::ok(format!(
        "[alpha] clone dispatched: {}/{}{branch_hint} → {}",
        resp.owner, resp.repo, resp.target_path,
    ));
    output::info(format!("job_id={} status={}", resp.job_id, resp.status));
    output::info(
        "the clone runs in the background — open the dashboard Files tab to watch it land.",
    );
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
        Workspace {
            #[command(subcommand)]
            sub: WorkspaceCommand,
        },
    }

    fn parse_clone(args: &[&str]) -> CloneArgs {
        let cli = TestCli::try_parse_from(args).expect("clap parse");
        match cli.cmd {
            TestCmd::Workspace {
                sub: WorkspaceCommand::Clone(a),
            } => a,
        }
    }

    #[test]
    fn parses_bare_repo_arg() {
        let a = parse_clone(&["t", "workspace", "clone", "nomad3/agentprovision-agents"]);
        assert_eq!(a.repo, "nomad3/agentprovision-agents");
        assert!(a.branch.is_none());
    }

    #[test]
    fn parses_branch_flag() {
        let a = parse_clone(&[
            "t",
            "workspace",
            "clone",
            "owner/name",
            "--branch",
            "release/1.2",
        ]);
        assert_eq!(a.repo, "owner/name");
        assert_eq!(a.branch.as_deref(), Some("release/1.2"));
    }

    #[test]
    fn requires_repo_arg() {
        let cli = TestCli::try_parse_from(["t", "workspace", "clone"]);
        assert!(cli.is_err(), "missing REPO positional should fail clap");
    }
}
