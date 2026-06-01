# Claudia Bridge Consensus Plan

Date: 2026-06-01
Lead: Luna Supervisor
Audience: Simon and Claudia

## Goal

Create a practical delegation path between Luna Supervisor in AgentProvision and Claudia running in Simon's local Claude Code.

This plan intentionally mixes three mechanisms:

1. Local Claude Code hooks for laptop-side awareness.
2. A signed webhook bridge for direct task/status exchange when Simon exposes a tunnel.
3. GitHub issues and PR comments for durable repo-native delegation.

The first version should be boring, auditable, and easy to shut off. It should not require opening Simon's machine to the internet until the local hook and repo mailbox flow works.

## Architecture

```text
Luna / AgentProvision
  -> GitHub issue or .claudia/inbox task
  -> optional signed webhook over Cloudflare Tunnel, Tailscale Funnel, or ngrok
  -> Simon laptop bridge
  -> local Claude Code hook / Claudia session
  -> .claudia/outbox status or GitHub comment / PR
  -> Luna reviews and continues
```

## Components

### 1. Repo Mailbox

Location:

```text
.claudia/inbox/*.md
.claudia/status/*.md
.claudia/outbox/*.md
.claudia/archive/*.md
```

Purpose:

- Luna writes task handoffs.
- Claudia reads pending work from a local hook, shell command, or manual review.
- Claudia writes results and questions back.
- Git history can capture important handoffs without requiring a live service.

The task contract includes:

- task title and ID
- context
- constraints
- expected output
- reply location
- changed files and tests run for code work
- explicit uncertainty boundaries

### 2. Local Claude Code Hook

Use the bridge utility from a Claude Code hook or a manually run command:

```bash
python scripts/claudia_bridge.py init
python scripts/claudia_bridge.py poll
```

Recommended hook behavior:

- On session start or stop, run `poll`.
- If tasks exist, surface them to Claudia as local context.
- Do not auto-execute tasks without Claudia or Simon accepting them.

This gives Claudia a low-friction way to see Luna handoffs while preserving local control.

### 3. Signed Webhook Bridge

When Simon wants direct push delivery, run:

```bash
export CLAUDIA_BRIDGE_SECRET='replace-with-shared-secret'
python scripts/claudia_bridge.py serve --host 127.0.0.1 --port 8765
```

Expose it only through a controlled tunnel, for example Cloudflare Tunnel, Tailscale Funnel, or ngrok.
Webhook mode refuses to start without `CLAUDIA_BRIDGE_SECRET` unless `--allow-unsigned` is passed for isolated local testing.

Supported endpoints:

- `GET /health`
- `POST /tasks`
- `POST /outbox`

Security contract:

- Use `X-Claudia-Signature: sha256=<hmac>` over the raw request body.
- Keep the service bound to `127.0.0.1` unless there is a strong reason not to.
- Rotate the shared secret if it is pasted into a chat or log.
- Treat webhook content as untrusted instructions until Claudia reviews it.

### 4. GitHub-Native Delegation

For repo work, GitHub remains the system of record.

Use:

```bash
python scripts/claudia_bridge.py issue-body \
  --title "Claudia consensus: Luna/Claudia bridge" \
  --body-file docs/plans/2026-06-01-claudia-bridge-consensus-plan.md
```

Then paste or pipe the output into a GitHub issue. Claudia can reply in comments, push a branch, or open a PR.

Recommended labels:

- `claudia`
- `luna`
- `coordination`
- `needs-consensus`

## Rollout Plan

### Phase 0: Manual Consensus

Simon sends this plan and PR to Claudia manually.

Consensus questions:

- Does Claudia want the repo mailbox as the shared durable handoff format?
- Which Claude Code hook event should poll the inbox?
- Should webhook push be enabled now, or only after manual mailbox flow works?
- Should GitHub issues be mandatory for repo mutations?

### Phase 1: Local-Only Mailbox

- Merge the bridge script and docs.
- Simon pulls the repo locally.
- Claudia runs `python scripts/claudia_bridge.py poll`.
- Luna writes task files under `.claudia/inbox` when direct delegation is needed.
- Claudia writes status under `.claudia/outbox`.

Acceptance criteria:

- A task can be created, discovered, answered, and archived without a network service.
- The task includes enough context for Claudia to act without guessing.
- Luna can review Claudia's outbox response and continue.

### Phase 2: Hook Notification

- Add a Claude Code hook on Simon's local machine that calls `poll`.
- Keep it read-only.
- Do not start auto-execution.

Acceptance criteria:

- Claudia sees pending tasks at useful moments.
- The hook does not block Claude Code startup.
- Hook failures are visible but non-fatal.

### Phase 3: Signed Webhook

- Start the bridge on `127.0.0.1`.
- Expose it through a tunnel only after the secret is configured.
- Send one test task and one test outbox reply.

Acceptance criteria:

- Unsigned requests fail.
- Signed requests create mailbox files.
- Logs do not leak the shared secret.

### Phase 4: GitHub Issues / PRs

- Use GitHub issues for work that changes repos.
- Use PR comments for code review and consensus.
- Keep `.claudia/` for local operational handoffs and status snapshots.

Acceptance criteria:

- Repo mutations have an issue or PR trail.
- Claudia's changes list files and tests.
- Luna performs a review before merge.

## Risks

| Risk | Mitigation |
| --- | --- |
| Random internet traffic reaches Simon's laptop | Bind locally, use a tunnel allowlist when available, require HMAC signatures |
| Prompt injection through webhook body | Treat all tasks as untrusted text; require Claudia review before execution |
| Conflicting changes between Luna and Claudia | Use GitHub branches/PRs for repo mutations; include changed files in status |
| Lost context between systems | Keep task IDs and reply locations in every handoff |
| Automation runs too eagerly | Start read-only; no auto-execution until Simon and Claudia approve |

## First Task For Claudia

```text
Please review PR #756 for the Luna/Claudia bridge.

Focus:
- Does the repo mailbox format match how you want to receive work?
- Is the local hook flow practical in Claude Code?
- Is the webhook HMAC contract enough for first internal use?
- Should GitHub issues be mandatory for repo-changing tasks?

Please reply with:
- approved / requested changes
- preferred hook event
- whether webhook should be enabled in phase 1 or deferred
- any changes needed before Simon relies on this overnight
```
