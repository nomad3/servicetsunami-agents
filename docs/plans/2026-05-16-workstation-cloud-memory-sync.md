# Workstation ↔ Cloud Memory Sync

**Status:** Plan (2026-05-16). Task #256.
**Depends on:** PR #530 (`alpha workspace clone`), `docs/architecture/workspace.md`, `docs/architecture/alpha_cli_kernel.md`.
**Authoritative kernel principle:** [`../architecture/alpha_cli_kernel.md`](../architecture/alpha_cli_kernel.md)
**Volume:** `agentprovision-agents_workspaces` (`/var/agentprovision/workspaces/<tenant_id>/`)

---

## 0. Problem statement

Today Luna's `memory/`, `docs/plans/`, and `projects/<repo>/` live in the tenant workspace volume on the cloud. Users also run Claude Code / Codex / Gemini on **local workstations** with their own local `memory/` directories. The only bridge today is manual: `alpha workspace clone` pulls a repo from GitHub, or the user `git push`es from their laptop. Memory edits and plan drafts have no first-class round-trip.

We want a bidirectional protocol where:
1. Editing `memory/<topic>.md` locally pushes to cloud.
2. Editing `memory/<topic>.md` in the cloud (dashboard, Luna, leaf agents) becomes visible locally.
3. The same applies to `docs/plans/` and `projects/<repo>/`.
4. All flows go through the Alpha CLI kernel — no out-of-band file servers.

---

## 1. Protocol options analysed

### Option A — Git-backed memory repo (per-tenant)
Server provisions a tenant-scoped private git repo seeded with the tenant's `<tenant_id>/` tree. Local clones, pushes when changing memory; cloud pulls on a tick. Cloud side is pull-only to avoid two-way storms; server writes propagate via commit-on-write hook.

**Pros:** reuses GitHub OAuth, offline-friendly, 3-way merge free, history/blame/revert.
**Cons:** still has a server-side write hook, one remote per tenant (sprawl/limits), nested git in `projects/<repo>/` is a footgun.

### Option B — `alpha sync push` / `alpha sync pull` (delta over HTTP)
Kernel verb. Server endpoints `POST /workspace/sync/manifest`, `GET /workspace/sync/download`, `POST /workspace/sync/upload`. Diff-walk via sha256; transfer only deltas.

**Pros:** one protocol, no second remote, reuses tenant-scoped JWT + `_safe_join`/`_reject_hidden_segments`, cheap for many small markdown files, fits the kernel pattern cleanly.
**Cons:** hand-roll conflict semantics; no history out of the box (add `memory/.versions/` ring buffer in Phase 4).

### Option C — MCP-over-WAN (cloud workspace as live source-of-truth)
Local agents read/write through MCP tools. Cloud is source of truth; local FS only caches.

**Pros:** one source of truth; aligns with Memory-First redesign.
**Cons:** constant connectivity required; breaks local-first ergonomics; conflicts become per-keystroke LWW.

### Recommendation: **Option B** (`alpha sync push/pull`)

Fits the existing kernel pattern (PR #530 shape: thin route → background worker → tenant volume + event publish), reuses JWT auth, degrades to manual upload/download cleanly. Option A tangles with the already-git-backed `projects/<repo>/`. Option C is a fine endgame but a poor starting point. We retain MCP read paths as a fast complement (read-through cache) once Option B is in place.

---

## 2. Conflict resolution

**Append-aware last-write-wins with conflict markers**, plus opt-in 3-way merge for textual files.

### 2.1 Per file class

| Subtree | Default strategy | Rationale |
|---|---|---|
| `memory/*.md` | LWW + conflict-marker file | Memory is mostly add-only; conflicts rare; forensics matter more than auto-merge. Override with `--strategy=merge`. |
| `docs/plans/*.md` | 3-way merge (LWW fallback) | Plans are co-edited; humans expect merge semantics. |
| `projects/<repo>/` | Skip — use git | Don't sync inside a git repo. Outer files only. |

### 2.2 Mechanics

Each manifest entry carries `{sha256, size, mtime_ns, base_sha256}`. `base_sha256` is the sha the client thinks the server has; stored in `~/.config/agentprovision/sync-state.json` keyed by tenant + relpath.

Server logic on `push`:
1. Server current sha == client `base_sha256` → fast-forward, accept.
2. Server current sha != base AND server == new → noop, accept.
3. Otherwise → **conflict**:
   - For `docs/plans/*.md`: attempt 3-way merge with server-current/base/new. Clean merge → accept. Conflict → write client content to `<file>.conflict-<timestamp>-<short-sha>` and respond 409 with the conflict path.
   - For `memory/*.md`: always write to `<file>.conflict-<timestamp>-<short-sha>`, respond 409.

Pull is plain "fetch newer-mtime files" — no conflict surface (Phase 1 is pull-only).

### 2.3 Why not append-only journal

Tool-generated memory often *rewrites* `<topic>.md` (Luna deduplicates). Journal pushes complexity to readers. Conflict markers are uglier but predictable.

---

## 3. Auth + tenant scoping

### 3.1 Authentication
Reuse the existing tenant-scoped JWT from `login_password` (`apps/agentprovision-core/src/client.rs:456`). `alpha sync` runs through the same `ApiClient` with `Authorization: Bearer …`; refresh-token rotation via `exchange_refresh`. No new credential type.

### 3.2 Tenant scoping
Server-side `_resolve_root("tenant", user)` is the only path-resolution primitive. Path traversal, hidden-segment, `_BLOCKED_DIRS` checks (`workspace.py:107`) unchanged. **Sync endpoints never accept `scope=platform`** — platform docs stay read-only and superuser-only. All writes through `_safe_join` + symlink-realpath re-check (`workspace.py:638`).

### 3.3 Per-user role within a tenant
New optional column `users.workspace_role` enum `{viewer, editor}` (default `editor` for back-compat). `viewer` can `pull` but not `push` (403 on push). Roles managed via existing `/api/v1/users` admin endpoints.

---

## 4. Trigger model

Three tiers, additive:

1. **Manual** (Phase 1+) — `alpha sync push memory/` / `alpha sync pull docs/plans/`. User controls when.
2. **Daemon / cron** (Phase 3) — `alpha sync daemon` long-running, polls every `--interval 60s` per-subtree. State in `~/.config/agentprovision/sync-state.json`.
3. **Filesystem watcher** (Phase 3.1) — `alpha sync daemon --watch` via the `notify` crate (inotify/FSEvents/ReadDirectoryChangesW). Debounced 500ms.

Daemon is client-side. Server has no equivalent — broadcast remains the existing Redis `workspace:{tenant_id}` channel (Phase 4 stretch: daemon SSE-subscribes).

---

## 5. Initial bootstrap

### 5.1 Local memory → empty cloud workspace
1. `alpha sync push memory/ --initial`
2. Server seeds tenant tree via `_seed_tenant_workspace` (idempotent)
3. Client uploads everything; server enforces `_MAX_SYNC_FILE_BYTES` (256 KiB for memory, 5 MiB for plans)
4. Client writes sync-state with all uploaded shas

### 5.2 Empty laptop, populated cloud workspace
`alpha sync pull memory/ docs/plans/ --initial` — symmetric.

### 5.3 Fresh tenant onboarding
`_seed_tenant_workspace` already plants README. Optional follow-up: seed `memory/onboarding.md` (out of scope).

### 5.4 Conflict on first run
If neither side empty, `--initial` is disallowed. User picks direction (`pull` or `push --force`). Plain `alpha sync` errors out clearly.

---

## 6. Alpha CLI kernel pattern

```
Workstation alpha CLI
        │ HTTP + Bearer JWT
        ▼
POST /workspace/sync/manifest
GET  /workspace/sync/download
POST /workspace/sync/upload
POST /workspace/sync/commit
        │
        ▼ (kernel) apps/api/app/api/v1/workspace.py (extended)
_resolve_root("tenant", user) → _safe_join → _reject_hidden_segments
        │
        ▼
/var/agentprovision/workspaces/<tenant_id>/<subtree>/
        │
        ▼
publish_session_event("workspace_sync_*", …) on workspace:{tenant_id}
```

Frontend never reads/writes disk; no Tauri-direct path; no direct Postgres. WhatsApp / Tauri / leaf-agent MCP get `alpha sync` for free via the same routes.

---

## 7. Specific endpoints + CLI verbs

### 7.1 HTTP API (extend `apps/api/app/api/v1/workspace.py`)

All require authenticated user, all tenant-scoped, no `scope=platform`.

#### `POST /api/v1/workspace/sync/manifest`
```json
{ "subtree": "memory/", "include_globs": ["*.md"], "exclude_globs": [".git/**"] }
```
Returns `{subtree, files: [{path, sha256, size, mtime_ns}], generated_at}`. Refuses subtrees outside `{memory/, docs/plans/, projects/<repo>/<sub>/}`. Caps entries at 50,000 (413 excess).

#### `GET /api/v1/workspace/sync/download?path=<rel>`
Octet-stream, `ETag: sha256:<sha>`, `X-Mtime-Ns: <int>`. Honors `If-None-Match` (304). Per-class file caps.

#### `POST /api/v1/workspace/sync/upload`
Multipart: `path`, `base_sha256?`, `content`, `mtime_ns?`.
Returns `{path, result: written|merged|conflict|unchanged, new_sha256, conflict_path?}`. 409 on conflict, 412 on If-Match precondition. Atomic write via temp + `os.replace`.

#### `POST /api/v1/workspace/sync/commit`
Server-side journal: `{job_id, paths, direction, started_at, finished_at, conflicts}` → Redis publish + `workspace_sync_log` table. Enables `alpha sync log` and Phase 4 SSE consumers.

### 7.2 CLI verbs (`apps/agentprovision-cli/src/commands/sync.rs`)

```
alpha sync push    <subtree> [--initial] [--force] [--strategy=lww|merge] [--dry-run]
alpha sync pull    <subtree> [--initial] [--force] [--dry-run]
alpha sync status  [<subtree>]
alpha sync daemon  [--interval 60s] [--watch] [--push memory/] [--pull docs/plans/]
alpha sync log     [--limit 20]
```

Wired through `ApiClient` (`apps/agentprovision-core/src/client.rs`). New methods: `manifest`, `download`, `upload`, `sync_log`. `alpha workspace` keeps `clone`. `alpha sync` is a sibling top-level verb (operates across tenant subtrees, not just `projects/`).

---

## 8. Out of scope

- Real-time collaborative editing (CRDTs)
- Mobile clients
- Cross-tenant sharing
- Conflict-free merge of binary files (LWW + `.conflict-…`)
- Sync of `.git/` directories under `projects/<repo>/`
- Sync of platform-scope docs
- Server → client push notifications (Phase 4 stretch; daemon polls until then)

---

## 9. Phased rollout

### Phase 1 — Pull-only (cloud → local) — target: 2 weeks
- `/manifest` + `/download`, `alpha sync pull <subtree>`
- Sync-state file, `base_sha256` tracked
- Zero conflict surface (overwrites local)

### Phase 2 — Push (local → cloud) — target: +2 weeks
- `/upload` + `/commit`
- 3-way merge for `docs/plans/*.md` (`merge3`)
- Conflict-file write for `memory/*.md`
- `workspace_sync_log` table + `alpha sync log`
- `users.workspace_role` + push gate

### Phase 3 — Daemon / watcher — target: +3 weeks
- `alpha sync daemon` with systemd / launchd templates in `apps/agentprovision-cli/install/`
- `--watch` via `notify` crate
- Backoff on auth failure; respects `exchange_refresh` rotation

### Phase 4 — Live push (stretch)
- Daemon SSE-subscribes to `workspace:{tenant_id}` Redis channel
- Pulls within seconds of cloud-side write

---

## 10. Tests to plan

### 10.1 Unit (pytest under `apps/api/tests/`)
- `_safe_join` jail tests for every new endpoint
- 3-way merge happy + conflict paths
- LWW path on `memory/*.md` always writes conflict file when shas diverge
- Quota: tenant over `_TENANT_WORKSPACE_BUDGET` → 413
- `subtree` outside allow-list → 400
- `subtree` into a git repo under `projects/` → 409 use git
- `viewer` role attempting upload → 403

### 10.2 Integration (`tests/integration/`)
- **Bootstrap empty tenant** — `pull --initial` against tenant hitting `_seed_tenant_workspace`
- **Round-trip a memory file** — push, pull on a second machine
- **Conflict scenario** — two clients with same `base_sha256`; first wins, second 409 + `.conflict-…`
- **3-way merge on plans** — different sections; both diffs land
- **Network failure mid-sync** — kill client between upload and commit; rerun; no duplicate writes (atomic `os.replace`)
- **Per-tenant scope** — cross-tenant manifest/download → 404
- **Path traversal** — `..`, encoded, hidden segments — all 404
- **Quota trip** — upload until budget exceeded → 413 with no partial state
- **Symlink swap (TOCTOU)** — realpath re-check (mirror of `_run_clone`)

### 10.3 CLI tests (`#[cfg(test)] mod tests`)
- Arg parsing for every subcommand
- `--initial` rejected when both sides non-empty
- Daemon backoff on 401, refresh-token rotation, exit on terminal auth failure

---

## 11. Open questions (resolve during Phase 1)

1. **Manifest hashing cost.** sha256 over every file on every sync — measure on a 1k-file tenant; if >200ms add mtime-based fast path.
2. **Top-level `alpha sync` vs `alpha workspace sync`.** Plan assumes top-level for friction; revisit at v1 ship.
3. **Encryption at rest of sync-state.** JWT is plaintext today (known trade-off). Sync-state holds shas only — not sensitive. v1: plaintext.

---

## 12. References

| Topic | Doc |
|---|---|
| Workspace persistence + endpoints | [`../architecture/workspace.md`](../architecture/workspace.md) |
| Kernel principle | [`../architecture/alpha_cli_kernel.md`](../architecture/alpha_cli_kernel.md) |
| Workspace backend (extend) | `apps/api/app/api/v1/workspace.py` |
| ApiClient (extend with sync methods) | `apps/agentprovision-core/src/client.rs` |
| CLI commands (add `sync.rs`) | `apps/agentprovision-cli/src/commands/` |
| `alpha workspace clone` reference impl | `apps/agentprovision-cli/src/commands/workspace.rs` |
| Memory-first redesign (parallel track) | memory `memory_first_design.md` |
