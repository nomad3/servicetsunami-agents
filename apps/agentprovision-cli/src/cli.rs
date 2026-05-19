//! Top-level clap definitions + dispatch.

use clap::{Parser, Subcommand};

use crate::commands::{
    agent, cancel, chat, coalition, completions, goal, integration, login, logout, memory, policy,
    quickstart, recall, recipes, remember, review, run, session, sessions, skill, status, tasks,
    upgrade, usage, watch, workflow, workspace,
};
use crate::context::Context;

#[derive(Debug, Parser)]
#[command(
    name = "alpha",
    version,
    about = "AgentProvision CLI — orchestrator of CLIs.",
    long_about = "alpha — the AgentProvision command-line client. Login, chat, run workflows, and orchestrate agents (Claude Code / Codex / Gemini CLI / Copilot) from your terminal.\n\nDocs: https://agentprovision.com/docs/cli"
)]
pub struct Cli {
    /// Override the API server URL (defaults to https://agentprovision.com or `server` from config.toml).
    #[arg(long, global = true, env = "AGENTPROVISION_SERVER")]
    pub server: Option<String>,

    // PR #332 review Critical #3: a `--tenant` flag was removed before
    // initial ship. None of the user-facing subcommands in this PR
    // (login/logout/status/chat) consume `X-Tenant-Id` — it's an
    // MCP-server header. Shipping the flag would have given users a
    // silent no-op and a false sense of multi-tenancy support. The
    // tenant override will return in PR-C alongside the first
    // subcommand that actually needs it (e.g. `tenant switch`).
    /// Emit machine-readable JSON instead of pretty output.
    #[arg(long, global = true)]
    pub json: bool,

    /// Disable streaming chat responses; wait for the full reply.
    #[arg(long, global = true)]
    pub no_stream: bool,

    /// Increase verbosity; -v info, -vv debug. Logs go to stderr.
    #[arg(short, long, global = true, action = clap::ArgAction::Count)]
    pub verbose: u8,

    #[command(subcommand)]
    pub command: Command,
}

#[derive(Debug, Subcommand)]
pub enum Command {
    /// Authenticate with AgentProvision. Stores the bearer token in the OS keychain.
    Login(login::LoginArgs),

    /// Remove the stored token from the OS keychain.
    Logout,

    /// Show the current user, tenant, server, and CLI version. With
    /// `--runtimes`, also reports preflight status for Claude Code,
    /// Codex, Gemini CLI, and Copilot CLI.
    Status(status::StatusArgs),

    /// Chat with the default agent. Run without subcommand for an interactive REPL.
    #[command(subcommand)]
    Chat(ChatCommand),

    /// Dispatch a durable task. Supports multi-provider fanout
    /// (`--fanout claude,codex,gemini --merge council`), fallback
    /// chains (`--providers claude,codex,opencode`), and background
    /// execution (`--background` + later `alpha watch <id>`).
    ///
    /// Phase 1 prototype — see
    /// docs/plans/2026-05-13-ap-cli-differentiation-roadmap.md.
    Run(run::RunArgs),

    /// Tail an in-flight task's status from any machine. Pairs with
    /// `alpha run --background` for fire-and-forget then later resume.
    Watch(watch::WatchArgs),

    /// Cancel an in-flight task. For fanout tasks, both the parent
    /// and all child workflows are cancelled — pass the parent
    /// task_id only; child workflows are cancelled automatically
    /// by the backend cascade. Best-effort under Temporal: the
    /// leaf CLI subprocess may take seconds to observe the signal.
    Cancel(cancel::CancelArgs),

    /// Self-update the `alpha` binary from GitHub Releases.
    Upgrade(upgrade::UpgradeArgs),

    /// List and inspect agents in the current tenant.
    #[command(subcommand)]
    Agent(agent::AgentCommand),

    /// List, inspect, run, and tail dynamic workflows.
    #[command(subcommand)]
    Workflow(workflow::WorkflowCommand),

    /// List recent chat sessions and read their message history.
    #[command(subcommand)]
    Session(session::SessionCommand),

    /// Manage long-lived authentication sessions (the refresh tokens
    /// minted by `alpha login`). One row per logged-in device.
    /// Note: plural — distinct from `alpha session` which lists chat
    /// sessions.
    Sessions(sessions::SessionsArgs),

    /// Inspect integration connection status for the current tenant.
    #[command(subcommand)]
    Integration(integration::IntegrationCommand),

    /// Browse the file-based skill library.
    #[command(subcommand)]
    Skill(skill::SkillCommand),

    /// Browse and search the tenant's knowledge graph (entities).
    #[command(subcommand)]
    Memory(memory::MemoryCommand),

    /// Unified semantic search across the tenant's memory layer
    /// (entities, observations, episodes, conversation snippets).
    /// The same surface chat agents query before every turn under
    /// the memory-first design. Distinct from `alpha memory search`
    /// which is scoped to knowledge-graph entities only.
    ///
    /// Phase 2 of the CLI roadmap (#179) — see
    /// docs/plans/2026-05-13-ap-cli-differentiation-roadmap.md.
    Recall(recall::RecallArgs),

    /// Write a free-form fact into the tenant's memory layer. The
    /// fact is embedded for semantic recall and recorded as an
    /// observation (optionally attached to an entity via `--entity`).
    /// Phase 2 (#179) companion to `alpha recall`.
    Remember(remember::RememberArgs),

    /// Inspect agent governance policies (rate limits, allowed tools,
    /// approval gates, escalation chains). Read-only — policy
    /// mutation goes through the web UI for audit trail. Phase 2
    /// (#179) of the CLI roadmap.
    #[command(subcommand)]
    Policy(policy::PolicyCommand),

    /// Dispatch and inspect multi-agent coalitions (incident
    /// investigations, plan/verify, research/synthesize, debate/
    /// resolve, propose/critique/revise). Backed by the existing
    /// CoalitionWorkflow on agentprovision-orchestration queue +
    /// Blackboard pub/sub. Phase 3 of the CLI roadmap (#180).
    #[command(subcommand)]
    Coalition(coalition::CoalitionCommand),

    /// Cross-CLI consensus code review. Fan the same review prompt
    /// out to N active CLIs in parallel, aggregate findings via the
    /// existing Blackboard + Coalition primitives, and return
    /// `agreed_findings` (≥ 2 CLIs flagged) to the operator. Loop
    /// until consensus or `--max-rounds` is exhausted. See
    /// docs/plans/2026-05-18-alpha-review-consensus.md.
    #[command(subcommand)]
    Review(review::ReviewCommand),

    /// Install + run pre-built dynamic workflows (daily briefing,
    /// competitor watch, code review, cardiac report, deal pipeline,
    /// ...). The "Helm charts for AI workflows" surface of the
    /// roadmap (#180 §8). Use `alpha recipes ls` to browse, then
    /// `alpha recipes install <id>` or `alpha recipes run <id>`.
    #[command(subcommand)]
    Recipes(recipes::RecipesCommand),

    /// Dispatch a structured autonomous task — outcome + success
    /// criteria + operating rules + quality bar + deliverable. Sugar
    /// over the native `Goal` recipe (`alpha recipes run goal`).
    /// Interactive when called with no flags; non-interactive when
    /// any slot is provided.
    Goal(goal::GoalArgs),

    /// Cross-machine task dashboard — working + recently-completed
    /// workflow runs for the caller's tenant. `alpha tasks attach
    /// <id>` streams a live task; `alpha tasks cancel <id>` stops
    /// one. v1 surfaces working + completed; needs-input is deferred
    /// to a follow-up (see the agent-view design doc).
    Tasks(tasks::TasksArgs),

    /// Per-provider tokens + cost rollup for the caller's tenant.
    /// Phase 4 of the CLI roadmap (#181). See `alpha costs` for the
    /// daily breakdown.
    Usage(usage::UsageArgs),

    /// Per-day cost rollup for the caller's tenant. Optional
    /// `--agent <uuid>` to scope to a single agent. Phase 4 of the
    /// CLI roadmap (#181). See `alpha usage` for the provider split.
    Costs(usage::CostsArgs),

    /// Guided initial-training flow. Auto-fires the first time you
    /// `alpha login` against a fresh tenant; can be re-run explicitly to
    /// re-train (with `--force`) or to opt back in after Skip.
    Quickstart(quickstart::QuickstartArgs),

    /// Emit shell completion script (bash / zsh / fish / powershell / elvish).
    Completions(completions::CompletionsArgs),

    /// Manage the tenant's persistent workspace tree. Currently
    /// exposes `clone <owner/name>` which fetches a GitHub repo into
    /// `/var/agentprovision/workspaces/<tenant_id>/projects/<repo>/`
    /// using the user's connected `github` integration token. The
    /// repo is immediately visible in the dashboard Files mode.
    /// Task #255.
    #[command(subcommand)]
    Workspace(workspace::WorkspaceCommand),
}

#[derive(Debug, Subcommand)]
pub enum ChatCommand {
    /// Send a one-shot prompt and stream the reply.
    Send(chat::SendArgs),
    /// Open an interactive REPL.
    Repl(chat::ReplArgs),
}

pub async fn dispatch(args: Cli, ctx: Context) -> anyhow::Result<()> {
    match args.command {
        Command::Login(a) => login::run(a, ctx).await,
        Command::Logout => logout::run(ctx).await,
        Command::Status(a) => status::run(ctx, a.runtimes).await,
        Command::Chat(ChatCommand::Send(a)) => chat::send(a, ctx).await,
        Command::Chat(ChatCommand::Repl(a)) => chat::repl(a, ctx).await,
        Command::Run(a) => run::run(a, ctx).await,
        Command::Watch(a) => watch::run(a, ctx).await,
        Command::Cancel(a) => cancel::run(a, ctx).await,
        Command::Upgrade(a) => upgrade::run(a, ctx).await,
        Command::Agent(cmd) => agent::dispatch(cmd, ctx).await,
        Command::Workflow(cmd) => workflow::dispatch(cmd, ctx).await,
        Command::Session(cmd) => session::dispatch(cmd, ctx).await,
        Command::Sessions(a) => sessions::run(a, ctx).await,
        Command::Integration(cmd) => integration::dispatch(cmd, ctx).await,
        Command::Skill(cmd) => skill::dispatch(cmd, ctx).await,
        Command::Memory(cmd) => memory::dispatch(cmd, ctx).await,
        Command::Recall(a) => recall::run(a, ctx).await,
        Command::Remember(a) => remember::run(a, ctx).await,
        Command::Policy(cmd) => policy::run(policy::PolicyArgs { command: cmd }, ctx).await,
        Command::Coalition(cmd) => coalition::dispatch(cmd, ctx).await,
        Command::Review(cmd) => review::dispatch(cmd, ctx).await,
        Command::Recipes(cmd) => recipes::run(recipes::RecipesArgs { command: cmd }, ctx).await,
        Command::Goal(a) => goal::run(a, ctx).await,
        Command::Tasks(a) => tasks::run(a, ctx).await,
        Command::Usage(a) => usage::usage(a, ctx).await,
        Command::Costs(a) => usage::costs(a, ctx).await,
        Command::Quickstart(a) => quickstart::run(a, ctx).await,
        Command::Completions(a) => completions::run(a, ctx).await,
        Command::Workspace(cmd) => workspace::dispatch(cmd, ctx).await,
    }
}
