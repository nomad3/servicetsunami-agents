"""Claude Code chat executor — hoisted from workflows.py in Phase 1.6.

Body is byte-identical to the previous ``_execute_claude_chat`` (just
renamed to ``execute_claude_chat`` and with the two helper calls
rewired to the new ``cli_runtime`` module). Workflows-side helpers
(``_fetch_claude_token``, ``_INTEGRATION_NOT_CONNECTED_MESSAGES``,
``_build_allowed_tools_from_mcp``, the ``ChatCliResult``
dataclasses, and the module-level constants) are imported lazily inside
the function body so:

  1. The import cycle ``workflows -> cli_executors -> workflows`` does
     not fire at module-load time (workflows imports executors via the
     dispatch table inside ``execute_chat_cli``).
  2. Existing test monkeypatches on ``wf._fetch_claude_token`` etc. still
     take effect — lazy imports re-resolve the attribute on every call.

2026-05-16: gains a streaming pump (`SessionEventEmitter` +
`claude_stream_parser`) wired through `cli_runtime.on_chunk` so the
dashboard terminal card sees reasoning + tool_use + tool_result live
instead of waiting for `proc.communicate()` to return. The
`--output-format stream-json --verbose` switch is rollout-flagged via
`tenant_features.cli_stream_output` (default OFF prod, ON for the
saguilera test tenant). When the flag is OFF we keep the legacy
single-line `--output-format json` shape.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid

import cli_runtime
import tenant_home_quota
from cli_executors import claude_interactive
from cli_executors import claude_stream_parser
from session_event_emitter import SessionEventEmitter
from tenant_feature_flags import is_enabled as _feature_enabled

logger = logging.getLogger(__name__)


def _build_git_credential_env(github_token: str | None) -> dict[str, str]:
    """Return env vars so ``git clone https://github.com/...`` authenticates with
    the tenant's GitHub OAuth ``github_token`` — Claude can pull repos the
    connected account can read (2026-05-31).

    Unified gh-based auth (Simon's steer — every CLI on the code-worker shares the
    same ``gh``): the image wires a SYSTEM credential helper
    ``credential.https://github.com.helper = !gh auth git-credential`` (Dockerfile),
    so git delegates github.com auth to ``gh``, and ``gh`` resolves its token from
    ``GH_TOKEN``/``GITHUB_TOKEN`` in the env. We therefore only need to put the
    tenant's token in the turn env — HOME-independent (system config), and the
    SAME mechanism every other CLI gets (codex/gemini/copilot), instead of a
    claude-only bespoke helper. Fetched FRESH per-tenant by the caller (NOT the
    process-global ``os.environ`` set at workflows.py:1188, which leaks across
    tenants), so this is leak-free.

    Empty dict when there is no token — the Dockerfile's ``GIT_TERMINAL_PROMPT=0``
    etc. then make an unauthenticated clone fail fast instead of hanging.
    """
    if not github_token:
        return {}
    # Both names: gh prefers GH_TOKEN; GITHUB_TOKEN is the broad fallback other
    # tools read. The system `!gh auth git-credential` helper reads either.
    return {"GH_TOKEN": github_token, "GITHUB_TOKEN": github_token}


def _write_secret_file(path: str, content: str) -> None:
    """Write ``content`` to ``path`` with mode 0o600 (N2).

    The interactive turn blob (``turn_prompt.md``) and ``CLAUDE.md`` hold the
    persona + full conversation history, so they are secret-grade. ``os.open``
    with ``O_CREAT|0o600`` sets the perms atomically on create; an explicit
    ``chmod`` re-tightens a pre-existing world-readable file from an earlier
    turn. Interactive path only — the print path keeps its plain ``open``."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(content)
    finally:
        # Re-assert perms in case the file pre-existed (O_CREAT mode is ignored
        # for an existing file).
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def _ensure_claude_onboarding(home: str, trusted_cwd: str | None = None) -> None:
    """Seed ``$HOME/.claude.json`` so interactive Claude Code skips its
    first-run onboarding wizard (theme → login-method → folder-trust) AND the
    cold folder-trust dialog that floods/blocks the PTY before submit.

    A fresh HOME makes ``claude`` re-run the wizard on every interactive turn;
    at "Select login method" it starts a *new* OAuth login instead of using the
    stored ``.credentials.json``, which the headless PTY cannot complete — so a
    HOME that is actually logged in still surfaces as a subscription-auth
    failure. Marking onboarding complete makes the TTY use the stored
    credential silently.

    Folder-trust (root-cause fix, 2026-05-30): Claude v2.1.x keys the trust
    check on the REALPATH of cwd — ``getProjectPathForConfig`` resolves through
    symlinks (``fs.realpathSync`` / ``path.resolve``) before looking up
    ``config.projects[<key>]``. The earlier seed used the LITERAL cwd, so a
    symlinked worker cwd (e.g. tenant HOME on the workspaces volume) still hit
    the trust dialog because the resolved key never matched. We now key on
    ``os.path.realpath(trusted_cwd)`` and seed EVERY flag the binary checks:
    ``hasTrustDialogAccepted``, ``hasCompletedProjectOnboarding``, and
    ``projectOnboardingSeenCount`` (>0 — a 0 count re-triggers onboarding).
    Best-effort; never raises.
    """
    if not home:
        return
    try:
        cfg_path = os.path.join(home, ".claude.json")
        data: dict = {}
        if os.path.exists(cfg_path):
            with open(cfg_path, encoding="utf-8") as fh:
                raw = fh.read()
            if raw.strip():
                try:
                    data = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    # An existing but unparseable config may be real state or a
                    # transient partial write — never clobber it.
                    logger.warning(
                        "skipping onboarding seed: %s is not valid JSON", cfg_path
                    )
                    return
        if not isinstance(data, dict):
            data = {}
        changed = data.get("hasCompletedOnboarding") is not True
        data["hasCompletedOnboarding"] = True
        if trusted_cwd:
            projects = data.get("projects")
            if not isinstance(projects, dict):
                projects = {}
                data["projects"] = projects
                changed = True
            # Key on the RESOLVED path — Claude resolves symlinks before the
            # projects[...] trust lookup, so the literal path would never match.
            try:
                resolved_cwd = os.path.realpath(trusted_cwd)
            except OSError:
                resolved_cwd = trusted_cwd
            proj = projects.get(resolved_cwd)
            if not isinstance(proj, dict):
                proj = {}
                projects[resolved_cwd] = proj
                changed = True
            # Boolean trust flags the cold dialog gates on.
            for key in ("hasTrustDialogAccepted", "hasCompletedProjectOnboarding"):
                if proj.get(key) is not True:
                    proj[key] = True
                    changed = True
            # A 0/absent seen-count re-arms the project-onboarding flow; force ≥1.
            if not isinstance(proj.get("projectOnboardingSeenCount"), int) or \
                    proj.get("projectOnboardingSeenCount", 0) < 1:
                proj["projectOnboardingSeenCount"] = 1
                changed = True
        if not changed:
            return
        os.makedirs(home, exist_ok=True)
        # mkstemp gives a unique, O_EXCL, 0600 temp in the same dir; os.replace
        # then atomically swaps it in — preserving 0600 (this file is
        # secret-grade, see hook_templates.py SR-4), replacing a symlink instead
        # of writing through it, and staying race-safe across concurrent turns.
        fd, tmp_path = tempfile.mkstemp(dir=home, prefix=".claude.json.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
            os.replace(tmp_path, cfg_path)
        except OSError:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except OSError as exc:
        logger.warning("could not seed Claude onboarding flags in %s: %s", home, exc)


def execute_claude_chat(task_input, session_dir: str):
    from workflows import (
        _fetch_claude_token,
        _fetch_claude_credential,
        _fetch_github_token,
        _INTEGRATION_NOT_CONNECTED_MESSAGES,
        _build_allowed_tools_from_mcp,
        ChatCliResult,
        WORKSPACE,
        CLAUDE_CODE_MODEL,
    )
    # Prefer the typed credential (OAuth vs api_key); fall back to the
    # legacy OAuth-only helper so existing test monkeypatches on
    # `_fetch_claude_token` keep working.
    credential = _fetch_claude_credential(task_input.tenant_id)
    if credential is None:
        legacy_token = _fetch_claude_token(task_input.tenant_id)
        credential = (legacy_token, "oauth") if legacy_token else None
    if not credential:
        # Canonical not-connected message — must match
        # `cli_platform_resolver._MISSING_CRED_PATTERNS` so the
        # resolver chain classifies this as `missing_credential`
        # (skip without cooldown). The short form "Claude Code not
        # connected" did NOT match the regex (only the long
        # "subscription is not connected" did) — that broke chain
        # fallback for tenants who hit a credential-missing CLI.
        return ChatCliResult(
            response_text="",
            success=False,
            error=_INTEGRATION_NOT_CONNECTED_MESSAGES["claude_code"],
        )

    if task_input.instruction_md_content:
        with open(os.path.join(session_dir, "CLAUDE.md"), "w") as f:
            f.write(task_input.instruction_md_content)

    if task_input.mcp_config:
        with open(os.path.join(session_dir, "mcp.json"), "w") as f:
            f.write(task_input.mcp_config)

    token, kind = credential
    _model = task_input.model or CLAUDE_CODE_MODEL
    _allowed = task_input.allowed_tools or _build_allowed_tools_from_mcp(
        task_input.mcp_config, extra="Bash,Read,Edit,Write,WebFetch,WebSearch"
    )
    
    prompt = task_input.message
    if task_input.instruction_md_content.strip():
        # Bypass the 20KB limit of --append-system-prompt by injecting
        # instructions and conversation history directly into the prompt.
        prompt = f"{task_input.instruction_md_content.strip()}\n\n# User Request\n\n{task_input.message}"

    # ---- output-format rollout gate (plan 2026-05-16 §9) ----
    # `stream-json` emits NDJSON per event (system.init / assistant.text /
    # tool_use / tool_result / result.*) which the terminal card renders
    # live. Falls back to single-line `json` when the flag is OFF so the
    # legacy parse below (line 125) still works byte-for-byte.
    _stream_enabled = _feature_enabled(
        task_input.tenant_id, "cli_stream_output", default=False
    )
    execution_mode = os.environ.get(
        "CLAUDE_CODE_EXECUTION_MODE", "print"
    ).strip().lower()
    interactive_requested = execution_mode in {"interactive", "pty", "native"}
    # API-key tenants are already on the Console billing path; keep their
    # machine-readable print mode even when the worker globally enables
    # interactive native-auth for subscription/OAuth tenants.
    #
    # Native worker-login: the web connect flow (claude_auth.py) stores a
    # sentinel `session_token = "__native_worker_login__"` whose real credential
    # lives in the worker HOME volume. Force the interactive PTY path for THIS
    # tenant regardless of the global execution-mode env — the sentinel is never
    # a usable token (it's popped below for native auth). api_key tenants stay on
    # print mode (the `kind == "oauth"` gate holds).
    _native_worker_login = kind == "oauth" and token == "__native_worker_login__"
    interactive_mode = (interactive_requested or _native_worker_login) and kind == "oauth"
    if _native_worker_login:
        # The sentinel promises a native credential in the worker HOME volume.
        # If it's missing/wiped, fail explicitly rather than letting the chain
        # silently fall back and "hallucinate" Claude connectivity.
        _worker_home = os.environ.get("CLAUDE_CODE_WORKER_HOME", "/home/codeworker")
        if not os.path.isfile(os.path.join(_worker_home, ".claude", ".credentials.json")):
            return ChatCliResult(
                response_text="",
                success=False,
                error=(
                    "Claude Code worker auth missing — reconnect Claude Code on "
                    "the integrations page."
                ),
            )
    cmd = ["claude"]
    if not interactive_mode:
        cmd.extend(["-p", prompt])
        if _stream_enabled:
            cmd.extend(["--output-format", "stream-json", "--verbose"])
        else:
            cmd.extend(["--output-format", "json"])
    else:
        # Interactive REPL: auto-accept tool edits. Without this, Claude's
        # Write(answer.md) call intermittently raises a tool-permission menu
        # ("Do you want to create answer.md? 1.Yes 2.Yes-allow-all 3.No") that
        # the PTY runner can't answer (it only handles the folder-trust dialog),
        # so the answer file is never written and the turn dies SIGTERM/exit 143.
        # `acceptEdits` matches print mode's headless auto-accept and makes the
        # Write deterministic. (NOT `bypassPermissions` — it gates on its own
        # confirmation menu the runner likewise can't answer.)
        cmd.extend(["--permission-mode", "acceptEdits"])
    cmd.extend([
        "--model", _model,
        "--allowedTools", _allowed,
        "--add-dir", session_dir,
    ])
    if os.path.isdir(WORKSPACE):
        cmd.extend(["--add-dir", WORKSPACE])

    # NOTE: --resume intentionally NOT used. Previously we stored an
    # ever-growing session_id per chat and resumed it on every message.
    # For long conversations (Luna on WhatsApp), the JSONL session file
    # grew to 16+ MB, causing:
    #   - slow startup (loading + parsing the full file)
    #   - lossy context compaction (old details silently dropped)
    #   - context loss on specific entities (names, prior lead gen lists)
    # Instead, each `claude -p` invocation is a fresh one-shot session,
    # and the caller (chat.py) is responsible for passing the last N
    # messages via --append-system-prompt. This gives deterministic,
    # bounded context under our control.
    # Use --no-session-persistence to avoid leaking JSONL files on every
    # call (842+ files were accumulated in the previous model).
    if not interactive_mode:
        cmd.append("--no-session-persistence")

    mcp_path = os.path.join(session_dir, "mcp.json")
    if os.path.exists(mcp_path):
        cmd.extend(["--mcp-config", mcp_path])

    env = os.environ.copy()
    if kind == "oauth":
        # Subscription-OAuth flow: token is a Bearer for claude.com.
        # CRITICAL: also drop any inherited ANTHROPIC_API_KEY from the
        # container env (loaded via env_file: ./apps/api/.env). Claude
        # Code's auth priority puts ANTHROPIC_API_KEY ahead of
        # CLAUDE_CODE_OAUTH_TOKEN — so a leftover API key in the
        # container would silently route the subscription user to the
        # Console billing path, surfacing as "Credit balance is too
        # low" even though the OAuth account has quota. Observed
        # 2026-05-16 after PR #530 recreated code-worker.
        # Interactive mode is for native Claude Code subscription auth in
        # the worker HOME. Do not force the old print-mode OAuth env unless
        # explicitly requested; recent Claude Code releases route that path
        # differently from a normal logged-in TTY session.
        if interactive_mode and os.environ.get("CLAUDE_CODE_INTERACTIVE_AUTH", "native") == "native":
            env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
        else:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = token
        env.pop("ANTHROPIC_API_KEY", None)
    else:
        # API-key flow (kind == "api_key"): claude CLI honours
        # ANTHROPIC_API_KEY and routes to the Console billing path.
        env["ANTHROPIC_API_KEY"] = token
        # Defensive: clear any stale OAuth token from the container env
        # so the per-tenant API key takes effect.
        env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)

    # ── Prong 1: kill the startup chrome on the interactive path ─────────
    # Root cause of intermittent exit-143 (2026-05-30): on a cold/perturbed
    # worker HOME, Claude Code's launch FLOODS the PTY with continuous chrome —
    # auto-updater, the official-Anthropic marketplace auto-install, telemetry/
    # error-reporting — which never quiets, so the runner's quiet-settle never
    # fires and the trigger is never submitted. Disable those sources at the
    # source so the REPL settles fast. These envs are honoured by Claude Code
    # v2.1.x (verified against the binary):
    #   • DISABLE_AUTOUPDATER — no mid-launch self-update.
    #   • CLAUDE_CODE_DISABLE_OFFICIAL_MARKETPLACE_AUTOINSTALL — no marketplace
    #     auto-install attempt (the "installing plugins…" chrome).
    #   • CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC — broad off switch for
    #     telemetry / error reporting / feedback / live-preview prefetches.
    # Interactive-only: the print path stays byte-identical (it's already
    # headless + fast). Set as the worker default in docker-compose + Helm too
    # (no drift) so the deployed worker gets them even if this code is bypassed.
    if interactive_mode:
        env["DISABLE_AUTOUPDATER"] = "1"
        env["CLAUDE_CODE_DISABLE_OFFICIAL_MARKETPLACE_AUTOINSTALL"] = "1"
        env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"

    # ── tenant workspace cwd (task #259) ─────────────────────────────────
    # Scope the subprocess cwd to the tenant's persistent workspace
    # projects dir so files Claude writes via the Write/Edit tools land
    # in the named ``workspaces`` volume that's shared with the api
    # container — and therefore appear in the dashboard's FileTreePanel.
    # Falls back to the legacy ``WORKSPACE`` (=/workspace) or session_dir
    # when the volume isn't mounted (tests, bare-metal dev).
    _cwd_fallback = WORKSPACE if os.path.isdir(WORKSPACE) else session_dir
    cli_cwd = cli_runtime.resolve_cli_cwd(task_input, _cwd_fallback)
    env["WORKSPACE"] = cli_cwd
    # Also let Claude see the tenant workspace as an --add-dir so it
    # treats files there as in-scope for Read/Edit.
    if cli_cwd != _cwd_fallback:
        cmd.extend(["--add-dir", cli_cwd])

    # ── interactive submission (Approach C, plan 2026-05-30) ────────────
    # Claude Code v2.1.144's interactive REPL does NOT auto-execute a
    # positional [prompt] arg, so appending the turn blob to ``cmd`` (as the
    # old code did) submitted nothing and the turn died at the idle ``/exit``.
    # Instead: write the blob to a per-turn scratch file (``session_dir`` is
    # already ``--add-dir``'d at L212 so Claude's Read tool can reach it by
    # absolute path) and hand the runner a single-line trigger to TYPE. A
    # single line sidesteps both the unreliable CLAUDE.md auto-load (cwd-upward
    # only; --add-dir grants access, not memory loading) and the multi-line
    # bracketed-paste ``[Pasted text +N lines]`` placeholder that needs a
    # second Enter. The runner types the trigger and strips its echo.
    interactive_submit = None
    interactive_answer_dir = None

    def _build_interactive_turn() -> tuple[str, str]:
        """Create a FRESH per-turn scratch dir + single-line trigger and return
        ``(trigger, turn_dir)``. Called once per interactive ATTEMPT so a
        startup-freeze relaunch (below) writes into clean state — the runner
        ``rmtree``s the dir it was handed, so a retry must never reuse it.

        Mangle-robust scratch DIR (bug fix 2026-05-30): Claude intermittently
        DROPS characters from a long hex FILENAME when it re-types it into its
        ``Write`` call (told ``answer_<32hex>.md``, writes a SHORTER name). The
        old code polled the exact un-mangled path → waited forever → idle
        ``/exit`` → exit 143 → Gemini fallback (~25% of turns). Fix: a UNIQUE
        per-turn scratch DIRECTORY plus a SHORT, FIXED answer name in it. The
        runner globs the fresh dir, so a dropped-char filename is still caught
        (the ``answer`` prefix survives; any non-``turn_prompt`` ``*.md`` is a
        fallback). ``session_dir`` is ``--add-dir``'d, so a child dir is writable.
        """
        turn_dir = os.path.join(session_dir, f"turn_{uuid.uuid4().hex}")
        os.makedirs(turn_dir, 0o700)
        # Re-assert 0o700 in case the umask trimmed the mode at create time.
        try:
            os.chmod(turn_dir, 0o700)
        except OSError:
            pass
        turn_file = os.path.join(turn_dir, "turn_prompt.md")
        # N2: turn blob is secret-grade (persona + conversation history) → 0o600.
        _write_secret_file(turn_file, prompt)
        # Re-tighten CLAUDE.md (written above with a plain `open`) on the
        # interactive path for consistency — it carries the same blob.
        _claude_md = os.path.join(session_dir, "CLAUDE.md")
        if os.path.exists(_claude_md):
            try:
                os.chmod(_claude_md, 0o600)
            except OSError:
                pass
        # Defect 2 (plan §4.1/§4.4): interactive Claude is a cursor-addressed
        # TUI whose transcript can't be reliably cleaned, so have Claude write
        # its final answer into the scratch dir under a SHORT, FIXED name the
        # runner globs back out-of-band.
        answer_file = os.path.join(turn_dir, "answer.md")
        # FINDING 3 (Luna): ask for the COMPLETE user-facing response, not a
        # terse stub — include important results, file changes, errors, or next
        # steps (but no tool-chatter/preamble) so the deliverable the runner
        # reads back is the full reply.
        trigger = (
            f"Read the file {turn_file} and respond to the user request it "
            f"contains. Write your COMPLETE final response for the user to "
            f"{answer_file} (overwrite it) — include any important "
            "results, file changes, errors, or next steps, but no preamble or "
            "tool-chatter. Reply directly — do not ask for confirmation."
        )
        return trigger, turn_dir

    # ── tenant HOME on workspaces volume (task #267 Phase 1) ────────────
    # Redirect HOME onto the persistent workspaces volume so per-tenant
    # ``.local/`` / ``.cache/`` / package installs don't grow the
    # code-worker writable layer (root cause of the 2026-05-04 &
    # 2026-05-17 disk-full incidents). Non-UUID tenant_id falls back to
    # the container's default HOME — same defensive shape as
    # ``resolve_cli_cwd``.
    tenant_home_path: str | None = None
    home_resolved = False
    try:
        tenant_home_path = str(cli_runtime.tenant_home_dir(task_input.tenant_id))
        interactive_home_mode = os.environ.get(
            "CLAUDE_CODE_INTERACTIVE_HOME", "tenant"
        ).strip().lower()
        if interactive_mode and (
            _native_worker_login or interactive_home_mode in {"worker", "codeworker"}
        ):
            # Native Claude Code subscription auth is tied to HOME. Some
            # workers are authenticated once as the codeworker user, so use
            # that HOME only for the TTY path when explicitly requested.
            env["HOME"] = os.environ.get("CLAUDE_CODE_WORKER_HOME", "/home/codeworker")
        else:
            env["HOME"] = tenant_home_path
        home_resolved = True
    except (ValueError, OSError) as exc:
        logger.warning(
            "tenant_home_dir(%s) failed (%s); HOME falls back to container default",
            task_input.tenant_id, exc,
        )

    # Interactive TTY only: pre-complete Claude Code's onboarding wizard for
    # the HOME we deliberately resolved (never the inherited fallback HOME) so
    # it uses the stored subscription credential instead of re-initiating an
    # OAuth login the headless PTY can't finish.
    if interactive_mode and home_resolved:
        _ensure_claude_onboarding(env["HOME"], cli_cwd)

    # ── git auth for the turn (2026-05-31) ──────────────────────────────
    # Wire the tenant's GitHub OAuth token (the /integrations connection) into
    # this subprocess's git so Claude's ``git clone https://github.com/…`` for
    # "pull my repos" authenticates as the user — instead of prompting for a
    # username on the PTY and hanging the full 25-min timeout. Fetched FRESH
    # per-tenant (never trusting the process-global ``os.environ["GITHUB_TOKEN"]``
    # the chat dispatcher sets — that leaks across tenants). Injected into the
    # per-subprocess ``env`` only (ephemeral GIT_CONFIG_* — no on-disk token).
    # No token → empty patch → the Dockerfile's GIT_TERMINAL_PROMPT=0 et al. make
    # an unauthenticated clone fail fast rather than hang.
    try:
        _gh_token = _fetch_github_token(task_input.tenant_id)
    except Exception as exc:  # noqa: BLE001 - never block the turn on token fetch
        logger.warning("github token fetch failed (%s); clones will be unauthenticated", exc)
        _gh_token = None
    env.update(_build_git_credential_env(_gh_token))

    # ---- streaming emitter (no-op if flag off / chat_session_id missing) ----
    emitter = SessionEventEmitter(
        chat_session_id=getattr(task_input, "chat_session_id", "") or "",
        tenant_id=task_input.tenant_id,
        platform="claude_code",
        attempt=getattr(task_input, "attempt", 1) or 1,
    )
    # The stream parser only understands Claude's `stream-json` protocol.
    # Interactive mode emits terminal text from a PTY, so leave raw transcript
    # handling inside `claude_interactive` instead of feeding it to NDJSON code.
    on_chunk = (
        claude_stream_parser.build_parser(emitter)
        if (_stream_enabled and not interactive_mode and emitter.enabled)
        else None
    )

    try:
        if interactive_mode:
            # Startup-freeze recovery (2026-05-30): under host starvation Claude
            # can paint its UI, swallow the typed trigger, then FREEZE — zero
            # post-submit output, no answer file written. The runner detects this
            # fast (short post-submit no-output cap) and returns a killed, EMPTY
            # result. A resend into a frozen REPL is useless; only a FRESH PROCESS
            # cures it. So relaunch once with a clean per-turn scratch dir. Bounded
            # by ``CLAUDE_CODE_INTERACTIVE_MAX_ATTEMPTS`` (default 2). Each attempt
            # carries its own internal heartbeat + caps, and a relaunch is
            # immediate (no gap), so Temporal's activity heartbeat stays fresh.
            max_attempts = max(
                1, int(os.environ.get("CLAUDE_CODE_INTERACTIVE_MAX_ATTEMPTS", "2"))
            )
            # Backstop bound for an interactive CHAT turn (2026-05-31). A
            # conversational Luna turn should take seconds-to-minutes; a heavy
            # coding job routes through the code-task path, not here. Capping at
            # 600s (was 1500s) means ANY future unknown hang — a new pager/editor
            # vector, a wedged MCP call, a stuck spinner — fails in ≤10 min and
            # retries once, instead of tying up the worker for 25 min. Env-tunable.
            _interactive_timeout = int(
                os.environ.get("CLAUDE_CODE_INTERACTIVE_TIMEOUT_SECONDS", "600")
            )
            result = None
            for _attempt in range(max_attempts):
                interactive_submit, interactive_answer_dir = _build_interactive_turn()
                result = claude_interactive.run_claude_interactive_with_heartbeat(
                    cmd,
                    prompt=interactive_submit,
                    label="Claude Code",
                    timeout=_interactive_timeout,
                    env=env,
                    cwd=cli_cwd,
                    on_chunk=on_chunk,
                    heartbeat=cli_runtime.activity.heartbeat,
                    answer_dir=interactive_answer_dir,
                )
                # Relaunch on any non-zero exit whose output carries NO actual
                # textual content (no alphanumerics) — a startup freeze, a
                # pre-banner timeout, or a killed launch, all transient and all
                # curable by a fresh process. We test ``isalnum`` rather than
                # ``strip()`` because a frozen TUI commonly leaves a bare prompt
                # glyph (``❯``) or box chrome that the best-effort cleaner can't
                # fully remove — that residue is NOT a real answer and must still
                # trigger recovery (verified: a synthetic freeze leaked ``❯`` and
                # the strip() guard wrongly suppressed the retry). A genuine error
                # (auth/usage) carries an alphanumeric message, and a real reply —
                # even a terse scraped one — has letters/digits, so neither is
                # retried (no double-billing). A successful turn is rc 0 anyway.
                if result.returncode == 0 or any(c.isalnum() for c in result.stdout):
                    break
                if _attempt + 1 < max_attempts:
                    logger.warning(
                        "interactive Claude turn empty (rc=%s) — likely a startup "
                        "freeze/timeout under load; relaunching fresh process (attempt %s/%s)",
                        result.returncode, _attempt + 2, max_attempts,
                    )
        else:
            result = cli_runtime.run_cli_with_heartbeat(
                cmd,
                label="Claude Code",
                timeout=1500,
                env=env,
                cwd=cli_cwd,
                on_chunk=on_chunk,
            )
    finally:
        _stats = emitter.close()
        # ── Phase 2 quota walker (task #264) ────────────────────────────
        # Walk the tenant HOME dir on the workspaces volume and prune
        # non-essential subtrees if we're over the 2 GiB soft cap.
        # Watermark-gated inside maybe_enforce_quota; non-raising.
        if tenant_home_path:
            tenant_home_quota.maybe_enforce_quota(
                task_input.tenant_id,
                tenant_home_path,
                cumulative_chunks=int(_stats.get("emitted", 0)) if isinstance(_stats, dict) else 0,
            )

    if result.returncode != 0:
        err = cli_runtime.safe_cli_error_snippet(result.stderr, result.stdout, 1000)
        return ChatCliResult(response_text="", success=False, error=f"CLI exit {result.returncode}: {err}")

    raw = result.stdout.strip()
    if not raw:
        return ChatCliResult(response_text="", success=False, error="CLI produced no output")

    if interactive_mode:
        return ChatCliResult(
            response_text=raw,
            success=True,
            metadata={
                "platform": "claude_code",
                "execution_mode": "interactive",
            },
        )

    # stream-json: response is NDJSON, the final `result` event holds the
    # cost + usage data and ``result.result`` is the assistant's full
    # answer. Find the LAST non-empty line and parse that (the only line
    # with subtype="success"). Fall back to legacy single-line json
    # parse if the line isn't a result envelope.
    if _stream_enabled:
        last_line = ""
        for ln in reversed(raw.splitlines()):
            if ln.strip():
                last_line = ln.strip()
                break
        try:
            data = json.loads(last_line)
            if data.get("type") != "result":
                # Final non-empty line wasn't the result envelope —
                # could be a trailing assistant message. Walk back to
                # find the result event.
                for ln in reversed(raw.splitlines()):
                    if not ln.strip():
                        continue
                    try:
                        obj = json.loads(ln)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(obj, dict) and obj.get("type") == "result":
                        data = obj
                        break
            text = data.get("result") or data.get("response") or data.get("content") or data.get("text") or ""
            usage = data.get("usage") or {}
            meta = {
                "platform": "claude_code",
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "model": data.get("model"),
                "claude_session_id": data.get("session_id", ""),
                "cost_usd": data.get("total_cost_usd", 0),
            }
            if not text:
                # Edge case: no result envelope captured. Return raw so
                # the user sees something rather than a silent failure.
                text = raw
            return ChatCliResult(response_text=text, success=True, metadata=meta)
        except (json.JSONDecodeError, AttributeError):
            return ChatCliResult(
                response_text=raw,
                success=True,
                metadata={"platform": "claude_code"},
            )

    # Legacy single-line `--output-format json` path (flag OFF).
    try:
        data = json.loads(raw)
        text = data.get("result") or data.get("response") or data.get("content") or data.get("text") or raw
        meta = {
            "platform": "claude_code",
            "input_tokens": (data.get("usage") or {}).get("input_tokens", 0),
            "output_tokens": (data.get("usage") or {}).get("output_tokens", 0),
            "model": data.get("model"),
            "claude_session_id": data.get("session_id", ""),
            "cost_usd": data.get("total_cost_usd", 0),
        }
        return ChatCliResult(response_text=text, success=True, metadata=meta)
    except json.JSONDecodeError:
        return ChatCliResult(
            response_text=raw,
            success=True,
            metadata={"platform": "claude_code"},
        )
