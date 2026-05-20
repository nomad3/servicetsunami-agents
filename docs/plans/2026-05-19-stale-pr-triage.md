# Stale PR triage — 2026-05-19

Date: 2026-05-19
Owner: Claude Code (triaging) — operator decides closures
Status: Recommendations only — no PR has been closed
Tracks: task #303

## Scope

13 open PRs sitting >3 days old with `mergeable=UNKNOWN` (all have drifted from main and would need a rebase). Operator approval required before any closure per the never-destructive standing rule. This doc surfaces recommended dispositions for operator decision.

## Recommendations

### High confidence — close (superseded by shipped work)

| PR | Title | Recommendation | Why |
|---|---|---|---|
| **#472** | fix(ci): remove all docker prunes from workflows | **Close** | Superseded by PR #568 (pre-build prune step + `--force-rm`) which structurally fixed disk-pressure deploys. PR #472's approach was "remove prunes entirely"; #568's approach won. Memory `feedback_no_local_builds` reflects the post-#568 stance. |
| **#473** | feat(sentinel): build-aware disk pressure thresholds | **Close** | Operator explicitly said 2026-05-19 *"i dont think that sentinel is the permanent fix"* (in this very session's earlier prompt context). Subsequent work (#566, #567, #568, #569) addressed the root cause (image size + pre-build prune). Sentinel approach abandoned. |
| **#316** | fix(ci+ui): harden CI secret hydration; hide 'Requires approval' toggle | **Close OR re-confirm merged** | Memory `ci_deploy_secret_hydration_race` says this *did* merge as PR #316 on 2026-05-08. The PR appears open in `gh pr list` though. Possible cause: a branch with the same PR number was reused after the original merge. **Operator action: confirm whether the secret hydration race fix is currently live on main; if yes, close the open #316.** |

### Medium confidence — close (rebrand absorbed elsewhere)

| PR | Title | Recommendation | Why |
|---|---|---|---|
| **#431** | chore: rename servicetsunami → agentprovision (URLs + container names) | **Close** | The platform IS agentprovision per memory `product_is_agentprovision` (confirmed 2026-05-10). Repo path + container names + URLs are all already agentprovision. The rename has been absorbed piecemeal across many PRs. This consolidating chore PR is stale. |
| **#451** | chore: update in-repo refs to nomad3/agentprovision-agents | **Close** | Same situation — the repo is `nomad3/agentprovision-agents` everywhere relevant. Memory `github_assignee_handle` documents the current assignee handle. |

### Operator-blocked — keep open

| PR | Title | Recommendation | Why |
|---|---|---|---|
| **#93** | feat: iOS build support for Luna Tauri client | **Keep open, no work** | Memory `ios_build_blocker` documents the blocker: needs Apple Developer Program ($99/yr), free team insufficient. Until operator pays for the program, this PR cannot complete. |
| **#478** | RFC: Alpha OS — product family plan (agentprovision / Alpha / Luna) | **Keep open as living RFC** | RFC documents framing that was later codified in memory (`agentprovision_product_family`, `alpha_brand_identity`). PR-as-doc-archive has value; do not close. |

### Needs operator decision — content review required

| PR | Title | Recommendation | Why |
|---|---|---|---|
| **#142** | fix: Luna chat send + HUD toggle button | **Operator review** | Old Luna client fix (2026-04-13). May be superseded by later Luna OS Spatial Workstation work (PR #88/#89, 2026-03-29 — but wait, those are older). Needs spot-check whether the chat-send/HUD-toggle bugs still exist. |
| **#154** | Feat: Luna OS Native Voice & Avatar Integration | **Operator review** | Memory `luna_client_voice_pattern` says PR #154 "PR #154, 2026-04-19" shipped the voice pattern fix. The PR shows as open here, suggesting either (a) the original #154 merged and this is a stale follow-up branch, or (b) the memory is wrong. **18 files / +1073 / −119 suggests this is a substantial unshipped branch.** Operator triage required. |
| **#318** | feat: SRE platform automation and benchmark | **Operator review** | Integral / SRE work. Memory `integral_integration` confirms Integral is a real tenant. Whether THIS PR is the canonical implementation or has been superseded by direct work in the integral repo (separate GitHub project) is unclear. |
| **#400** | docs(plans): ap quickstart design — training-first adoption flow | **Operator review or auto-merge** | Docs-only PR. Cheap to land. If the design is still aligned with the platform direction (quickstart adoption flow), merge after a quick read-pass. Otherwise close. |
| **#479** | fix: execute_shell background job pattern to beat HTTP transport timeouts | **Likely superseded — verify** | The Cloudflare 524 / SSE timeout problem was solved by PR #570 (async chat-result pattern, migration 137) which shipped 2026-05-19. PR #479 attacked a related symptom in a different code path. Verify the execute_shell path uses the chat_jobs pattern now; if so, close. |
| **#528** | feat(skills): import 12 community superpowers skills globally | **Operator review** | Substantial work (+1232 lines). Skills marketplace v2 (PRs #182-#193) shipped 2026-04-26 and changed the skill import semantics. This PR may need rebase + re-validation against the v2 marketplace, OR it may be entirely superseded by the bundled skills shipped in v2. Read-pass required. |

## Recommended operator actions

1. **Quick wins (do these first)**: close #472 + #473 + #431 + #451. ~5 min total. Clears 4/13 PRs immediately.
2. **Re-confirm + close #316** after checking main has the secret hydration fix.
3. **Schedule a 30-min triage window** for #142 / #154 / #318 / #400 / #479 / #528 — each needs a read-pass to determine current relevance. Recommend doing this when next dipping into the Luna client work or the skills marketplace.
4. **Leave #93 + #478 open** as documented above.

## Pending work surfaced

If after operator triage any of the medium/needs-review PRs prove to have load-bearing content that's not yet on main, a follow-up task should be created to either:
- Cherry-pick the relevant commits onto a fresh branch + open a new PR, or
- Rebase the original branch + push for fresh CI.

The standing rule `feedback_single_pr_for_feature` applies: if work spans multiple files / image builds, chain branches but squash-merge as one PR.

## After this triage

Goal: reduce open PR backlog to ≤5 active PRs. Currently 14 open. If all high-confidence closures land + needs-review batch resolves: 14 → ~5 active. Reaches goal.

## Related memories

- `product_is_agentprovision` — platform identity
- `agentprovision_product_family` — Alpha OS family framing  
- `ios_build_blocker` — #93 blocked on Apple Developer Program
- `luna_client_voice_pattern` — #154 disposition signal
- `ci_deploy_secret_hydration_race` — #316 disposition signal
- `feedback_no_local_builds` — #472 disposition signal
- `skills_marketplace_v2` — #528 disposition signal
