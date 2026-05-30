"""Claude Code CLI OAuth login flow.

Same pattern as codex_auth.py — spawns `claude setup-token`, captures
the browser verification URL, waits for user to authenticate, then
persists the resulting long-lived OAuth token (`sk-ant-oat01-…`) to
the encrypted vault under `credential_key='session_token'`.

Historical note: the original implementation (commit `c54f91b3`,
2026-04-05) ran `claude auth login --claudeai` instead. That writes
interactive *subscription session* credentials to `.credentials.json`
— a different token shape from what `CLAUDE_CODE_OAUTH_TOKEN` accepts.
Per Anthropic docs, `CLAUDE_CODE_OAUTH_TOKEN` requires the output of
`claude setup-token` (a long-lived `sk-ant-oat01-…` token the command
prints to stdout and does NOT save to disk). The salvage glob over
`CLAUDE_CONFIG_DIR/.credentials.json` was therefore picking up the
wrong artefact, which Anthropic later rejected with
`401 Invalid bearer token`. PR #531 unmasked the bug by dropping the
inherited `ANTHROPIC_API_KEY` fallback. The fix here switches the
spawned command to `claude setup-token`, captures the token straight
from stdout, validates the shape, and probes it against Anthropic
before persisting.

Design: docs/plans/2026-05-16-oauth-reconnect-token-format-mismatch.md
"""
import json
import logging
import os
import shutil
import re
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api import deps
from app.db.session import SessionLocal
from app.models.integration_config import IntegrationConfig
from app.models.integration_credential import IntegrationCredential
from app.models.user import User
from app.services.orchestration.credential_vault import store_credential

logger = logging.getLogger(__name__)

router = APIRouter()

ANSI_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
URL_RE = re.compile(r"https://claude\.com/[^\s]+")

# Credential keys we recognise as "Claude Code is connected". Either is a
# valid signal; consumers branch on the credential's `credential_type` to
# pick the auth shape (OAuth `session_token` vs Anthropic Console `api_key`).
_CLAUDE_CREDENTIAL_KEYS = ("session_token", "api_key")

# Status values from which the flow can still be cancelled or
# resumed. Past these (`submitting`/`connected`/`failed`/`cancelled`),
# the paste code has been delivered to claude CLI and the action is
# irreversible — cancel becomes a no-op and `/start` is the only way
# to begin a new flow. Single source of truth referenced by:
#   * start_login's "reuse existing" check
#   * cancel_login's guard
#   * _serialize_state.cancellable
_CANCELLABLE_STATUSES = frozenset({"starting", "pending"})


# How long to wait for the user to paste the code into the UI before
# we give up and tear the subprocess down. 10 min is enough for "open
# the URL → log into claude.com → copy the code → switch tabs → paste"
# without forcing a re-`/start` for slow flows. claude CLI's own
# timeout is on the order of a few minutes; if we wait longer than
# that the subprocess will have exited and the code write will no-op.
_OAUTH_PASTE_DEADLINE_SECONDS = 600

# After we write the code to stdin, how long to wait for claude CLI
# to finish the OAuth handshake and exit. Should be fast (<10s on a
# good network).
_OAUTH_FINALIZE_TIMEOUT = 60

# After SIGTERM, how long to wait for the subprocess to exit on its
# own before escalating to SIGKILL. Keeps zombies from accumulating
# when claude CLI is mid-stdin-read and ignores the first signal.
_OAUTH_TERMINATE_TIMEOUT = 5


class ClaudeAuthError(Exception):
    """Domain-level error raised by `ClaudeAuthManager`.

    Separate from `HTTPException` so the manager stays usable from
    non-HTTP callers (CLI re-auth tools, future Temporal activities,
    tests without a TestClient). The `/submit-code` route translates
    these into `HTTPException(status_code=...)`.
    """

    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code

    @property
    def detail(self) -> str:
        return str(self)


# Substrings we recognise in claude CLI stderr/stdout that mean the
# verification code expired or claude.com refused it. When any appears
# in the buffered output we replace the raw CLI noise with a
# UX-friendly message instead of dumping ANSI-stripped CLI output.
#
# Avoid bare substrings (e.g. `"expired"` alone) — they false-positive
# on benign output like `"certificate not expired"` or `"token will be
# expired tomorrow"`. Every entry here is either a full OAuth error
# code or a phrase that only appears in genuine expiry messages.
_CLI_EXPIRY_HINTS = (
    "invalid_grant",
    "authorization_pending",
    "expired_token",
    "code expired",
    "token expired",
    "code has expired",
    "session expired",
)

_CLI_EXPIRY_MESSAGE = (
    "The verification code expired or was rejected by claude.com before we "
    "could submit it. Click Connect to start over."
)


# Matches the long-lived OAuth token `claude setup-token` prints to
# stdout. The shape is `sk-ant-oat01-` followed by a long
# url-safe-base64 string (digits, letters, `-`, `_`). We accept any
# trailing token-character run so we tolerate token-shape changes
# Anthropic might roll forward (longer tokens, extra segments) while
# still rejecting clearly-wrong stdout fragments.
_OAT01_TOKEN_RE = re.compile(r"sk-ant-oat01-[A-Za-z0-9_\-]{20,}")


def _extract_oat01_token(stdout: str) -> Optional[str]:
    """Scan ANSI-stripped subprocess stdout for the long-lived OAuth token.

    `claude setup-token` prints the token on its own line near the
    end of stdout (after the human-friendly preamble). We return the
    first match in the entire buffer — there is only ever one token
    in a successful run. Returns `None` if no token-shaped string is
    found, which `_run_login` treats as a hard failure rather than
    falling through to a salvage path.
    """
    match = _OAT01_TOKEN_RE.search(stdout)
    return match.group(0) if match else None


def _probe_oauth_token_best_effort(token: str) -> bool:
    """Best-effort probe of a candidate `sk-ant-oat01-…` token.

    **Contract (read before tightening this function):**

      * This probe is BEST-EFFORT. It runs `claude --version` with the
        candidate token in env if-and-only-if `claude` is on PATH in
        the api container. In the production api image it is NOT, so
        the `FileNotFoundError` branch fires and this function returns
        `True` without performing any validation. In other words, the
        probe is effectively a no-op in production.

      * The LOAD-BEARING gate is the shape regex (`_OAT01_TOKEN_RE` +
        the `_OAT01_PREFIX` check in `_persist_credentials`). That is
        what actually prevents malformed tokens from reaching the
        vault. The probe only adds value in dev/test where `claude`
        is on PATH — it COMPLEMENTS but does NOT REPLACE the shape
        gate. Do not weaken the shape gate on the assumption that the
        probe catches anything; in prod, it doesn't.

      * Even where `claude` IS on PATH, `claude --version` does not
        hit Anthropic. It only validates the token's on-disk shape
        client-side. Network outages / Anthropic downtime cannot
        cause this probe to fail.

    Wider failure modes (network down, Anthropic outage, probe hang,
    unexpected exception) are accepted as "probe passed" since we
    don't want a transient blip to wedge the login UI when the
    shape check has already confirmed the token is well-formed.

    Returns True on probe success, missing-CLI (best-effort skip),
    timeout, or any other unexpected error; False ONLY when the CLI
    is present, ran to completion, and exited non-zero on this token.
    """
    try:
        result = subprocess.run(
            ["claude", "--version"],
            capture_output=True,
            text=True,
            timeout=_OAUTH_PROBE_TIMEOUT,
            env={
                **{k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"},
                "CLAUDE_CODE_OAUTH_TOKEN": token,
            },
        )
    except FileNotFoundError:
        # Claude CLI not installed in this container — we can't probe
        # but the executor in code-worker will. Don't block persist.
        # In the production api image this is the branch we hit, which
        # is why the shape regex is the real defense — see docstring.
        logger.warning("claude CLI not found for token probe; skipping (best-effort)")
        return True
    except subprocess.TimeoutExpired:
        # Probe hung. Treat as recoverable — most likely an unrelated
        # CLI bug, not a token issue. Persist and let runtime sort it.
        logger.warning("claude --version probe timed out; persisting anyway")
        return True
    except Exception:
        logger.exception("claude --version probe raised; persisting anyway")
        return True
    return result.returncode == 0


# Back-compat alias so existing call sites and tests that reference
# the short name keep working. New code should call the
# `_best_effort` name directly so the contract is obvious at the call
# site (see PR #533 review — the previous name implied a stronger
# guarantee than the function actually provides in prod).
_probe_oauth_token = _probe_oauth_token_best_effort


# Matches any captured `sk-ant-oat01-…` token in arbitrary text so we
# can scrub it before that text flows into a user-facing field. Kept
# separate from `_OAT01_TOKEN_RE` (which gates persistence) because
# the redaction use case wants greedy + permissive, while the
# persistence gate wants the strict ≥20-char minimum.
_OAT01_REDACT_RE = re.compile(r"sk-ant-oat01-[A-Za-z0-9_\-]+")


def _redact_oat01(text: str) -> str:
    """Scrub `sk-ant-oat01-…` tokens from user-facing strings.

    Run on any captured stdout/stderr before it lands in `state.error`
    or the serialized status response — otherwise a non-zero-exit
    AFTER a successful token print would leak the token to whichever
    browser is polling `/claude-auth/status`. The CLI's standard happy
    path prints the token then exits 0, but a non-zero exit after the
    print (network hiccup, late CLI assertion, signal, …) is a real
    failure mode and we must not allow the captured stdout buffer
    containing the printed token to be echoed back to the user.

    Safe on None / empty / token-free input — returns the input
    unchanged in those cases.
    """
    if not text:
        return text
    return _OAT01_REDACT_RE.sub("sk-ant-oat01-<REDACTED>", text)


def _humanise_cli_failure(cleaned_output: str) -> str:
    """Map a claude CLI failure dump to a user-friendly message.

    The CLI emits ANSI-formatted errors that aren't useful to end
    users. We pattern-match common cases and return a concise message;
    falls back to the last 500 chars of the raw output for unfamiliar
    failures so we never swallow a useful error silently.

    Token redaction happens FIRST — before the substring scan, before
    the truncation — so even if a future `_CLI_EXPIRY_HINTS` entry
    matches a string that included the token, the returned message
    can never carry it back to the UI.
    """
    cleaned_output = _redact_oat01(cleaned_output or "")
    haystack = cleaned_output.lower()
    if any(hint in haystack for hint in _CLI_EXPIRY_HINTS):
        return _CLI_EXPIRY_MESSAGE
    return cleaned_output[-500:] if cleaned_output else "Claude authorization failed"


def _snapshot_buf(buf: list) -> str:
    """Snapshot the reader-thread buffer for safe joining.

    `state._output_buf` is appended-to by the background reader thread
    and read by the main thread. `"".join(buf)` iterates the list;
    if `buf.append(...)` fires mid-iteration on CPython we can hit
    `RuntimeError: list changed size during iteration`. Copying with
    `list(buf)` is GIL-atomic and cheap (the list is small — at most
    a few dozen short lines for a healthy OAuth flow).
    """
    return "".join(list(buf))


def _terminate_and_reap(proc: subprocess.Popen) -> None:
    """SIGTERM the subprocess; SIGKILL if it doesn't exit promptly.

    `terminate()` only sends the signal — the parent must `wait()` to
    reap the child, otherwise it lingers as a zombie. We bound the
    grace period at `_OAUTH_TERMINATE_TIMEOUT` then escalate. Safe to
    call when the process is already dead. Defensive against the
    poll/wait/terminate primitives themselves raising (rare but
    documented OSError/EINVAL/ECHILD edges on some kernels).
    """
    try:
        if proc.poll() is not None:
            return
    except Exception:
        # poll() raised — give up rather than risking SIGTERM on an
        # unknown child state.
        return
    try:
        proc.terminate()
    except Exception:
        return
    try:
        proc.wait(timeout=_OAUTH_TERMINATE_TIMEOUT)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
            proc.wait(timeout=2)
        except Exception:
            pass
    except OSError:
        # wait() can surface OSError on already-reaped or
        # cross-process-namespace children. Treat as reaped.
        return


# Long-lived OAuth tokens produced by `claude setup-token` always
# start with this prefix. We fail-closed if the captured stdout
# doesn't match, rather than persisting a malformed credential that
# Anthropic will later reject with 401. This was the entire bug class
# the rewrite is meant to eliminate — better to surface a clear error
# in the UI than silently store garbage.
_OAT01_PREFIX = "sk-ant-oat01-"

# How long the post-login probe (`claude --version` with the just-
# captured token) is allowed to run before we treat the token as
# unhealthy. Should be near-instant for a well-formed token; tight
# bound here keeps the UX snappy and prevents a wedged Anthropic
# endpoint from holding the login state in limbo.
_OAUTH_PROBE_TIMEOUT = 15


@dataclass
class ClaudeLoginState:
    login_id: str
    tenant_id: str
    # Status machine:
    #   starting           — subprocess spawned, waiting for URL
    #   pending            — URL captured, waiting for user to open browser
    #                        and paste the resulting code into the UI
    #   submitting         — `/submit-code` received the paste; we wrote
    #                        it to subprocess stdin; waiting for claude
    #                        CLI to complete and exit
    #   connected          — credentials persisted to the vault
    #   failed / cancelled — terminal error states
    status: str = "starting"
    verification_url: Optional[str] = None
    error: Optional[str] = None
    connected: bool = False
    started_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    completed_at: Optional[str] = None
    claude_home: Optional[str] = None
    process: Optional[subprocess.Popen] = field(default=None, repr=False, compare=False)
    # The long-lived `sk-ant-oat01-…` token captured from
    # `claude setup-token` stdout. Populated by `_run_login` after
    # subprocess exit, consumed by `_persist_credentials`. Not
    # serialised over the wire — the UI only sees boolean
    # `connected` + status flags.
    captured_token: Optional[str] = field(default=None, repr=False, compare=False)
    # Buffer for stdout the reader thread captures while the
    # subprocess runs. We don't use `proc.communicate()` for the
    # whole lifecycle (it consumes stdin / closes pipes), so we
    # collect line-by-line on a background thread instead.
    _output_buf: list = field(default_factory=list, repr=False, compare=False)
    # Signal from `/submit-code` to `_run_login` that the user has
    # pasted a code. The main thread waits on this event before
    # invoking `proc.wait()`.
    _code_submitted: threading.Event = field(
        default_factory=threading.Event, repr=False, compare=False
    )


class ClaudeAuthManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._by_tenant: Dict[str, ClaudeLoginState] = {}

    def get_state(self, tenant_id: str) -> Optional[ClaudeLoginState]:
        with self._lock:
            return self._by_tenant.get(tenant_id)

    def start_login(self, tenant_id: str) -> ClaudeLoginState:
        with self._lock:
            existing = self._by_tenant.get(tenant_id)
            if existing and existing.status in _CANCELLABLE_STATUSES and existing.process:
                return existing

            # Capture the stale process to reap *after* releasing the
            # lock (terminate-and-reap can take up to 7s with the SIGTERM
            # grace + SIGKILL fallback; holding the lock that long
            # blocks every other endpoint).
            stale_proc = existing.process if existing else None
            # Note: if the existing state is `submitting`, the user
            # explicitly chose to restart by clicking Connect again —
            # tear down the mid-handshake subprocess intentionally.
            # This is the symmetric case to `cancel_login`'s guard but
            # with opposite intent (`/cancel` past pending is a no-op
            # because the user "released" the action; `/start` past
            # pending means the user wants to start over).

            login_id = str(uuid.uuid4())
            claude_home = tempfile.mkdtemp(prefix=f"claude-auth-{tenant_id[:8]}-")
            state = ClaudeLoginState(
                login_id=login_id,
                tenant_id=tenant_id,
                claude_home=claude_home,
            )
            self._by_tenant[tenant_id] = state

        # Reap the stale process outside the lock — `_terminate_and_reap`
        # can take up to 7s and we don't want `/status` polls blocked
        # behind it.
        if stale_proc is not None:
            _terminate_and_reap(stale_proc)

        threading.Thread(target=self._run_login, args=(state,), daemon=True).start()
        return state

    def cancel_login(self, tenant_id: str) -> Optional[ClaudeLoginState]:
        """Cancel an in-progress login.

        Two race directions to defend against:

          1. **cancel-then-submit:** cancel runs first, writes
             `state.status = "cancelled"` under the lock. The losing
             submit acquires the lock, sees `cancelled`, raises
             ClaudeAuthError. Net: cancel wins, no data leaked. ✓

          2. **submit-then-cancel:** submit runs first, writes the
             paste code to stdin and sets `state.status = "submitting"`.
             If cancel were unconditional, it would now overwrite
             `submitting` → `cancelled` and terminate the subprocess
             AFTER the data was delivered — UI says "cancelled" but
             the OAuth handshake may already be mid-flight on
             claude.com. The user thinks they aborted but actually
             may have completed the flow.

             Fix: only allow cancel from `starting`/`pending`. Once
             we've moved past `pending`, the paste has been
             delivered, the action is irreversible, and we let
             `_run_login` drive to a terminal state on its own. The
             user can re-`/start` after that.

        Holds `self._lock` around lookup + status mutation so cancel
        and submit can't interleave their writes. Releases before
        `_terminate_and_reap` so a concurrent `/status` poll isn't
        blocked behind the SIGTERM grace period.
        """
        with self._lock:
            state = self._by_tenant.get(tenant_id)
            if not state:
                return None
            # Only cancellable from pre-submit states. Past `pending`
            # the paste has been delivered to claude CLI; tearing it
            # down would race the handshake without preventing the
            # actual server-side OAuth from completing.
            if state.status not in _CANCELLABLE_STATUSES:
                return state
            # Mutate status *first* so any concurrent `submit_code` that
            # acquires the lock after us sees the cancellation and bails.
            state.status = "cancelled"
            state.error = "Login cancelled"
            state.completed_at = datetime.utcnow().isoformat()
            proc = state.process
        # Reap the subprocess outside the lock — terminate-and-wait can
        # take up to _OAUTH_TERMINATE_TIMEOUT and we don't want
        # `/status` or `start_login` blocked behind it.
        if proc is not None:
            _terminate_and_reap(proc)
        return state

    def _run_login(self, state: ClaudeLoginState) -> None:
        """Drive the `claude auth login --claudeai` subprocess.

        `setup-token` and `-p` are blocked for Claude subscription accounts, so
        we use the native login. Headless (no browser) it:
          1. Prints a `https://claude.com/…` authorize URL.
          2. Blocks on stdin reading the verification code the user pasted
             from the browser.
          3. On success writes `.credentials.json` under CLAUDE_CONFIG_DIR
             (no token is printed to stdout) and exits 0.

        Mechanics:
          * Spawn with `stdin=PIPE` so `/submit-code` can write to it.
          * Drain stdout on a background thread, line-by-line, so we can
            detect the authorize URL without blocking or closing stdin.
          * Wait on a `threading.Event` (`state._code_submitted`) for the
            user's paste — set by `/submit-code`.
          * Once the code is written, close stdin to signal EOF and let the
            CLI finish. `proc.wait(timeout=…)` reaps the exit status.
          * On exit, `_install_worker_credentials` copies the native
            `.credentials.json` into the code-worker's shared HOME volume and
            marks the integration connected.
        """
        # `setup-token` is blocked for Claude subscription accounts, so use the
        # native `auth login --claudeai` flow. Headless (no browser) it prints a
        # `https://claude.com/…` authorize URL and reads the pasted code from
        # stdin — same URL/paste shape this manager already drives. On success it
        # writes `.credentials.json` under CLAUDE_CONFIG_DIR, which we then copy
        # into the code-worker's shared HOME volume for interactive PTY auth.
        cmd = ["claude", "auth", "login", "--claudeai"]
        # Drop inherited API-key/token env so claude does the claude.ai
        # subscription login (not the Console/API-key path), mirroring the
        # worker's native auth.
        env = {**os.environ, "CLAUDE_CONFIG_DIR": state.claude_home or ""}
        env.pop("ANTHROPIC_API_KEY", None)
        env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)

        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
                bufsize=1,  # line-buffered so the URL line surfaces immediately
            )
        except FileNotFoundError:
            state.status = "failed"
            state.error = "Claude CLI not found"
            state.completed_at = datetime.utcnow().isoformat()
            return
        except Exception as exc:
            state.status = "failed"
            # Belt-and-suspenders redaction — `exc` here is from
            # `subprocess.Popen` setup which shouldn't contain a token,
            # but scrub defensively so a future change to the spawn
            # path can't accidentally leak via this fallthrough.
            state.error = _redact_oat01(str(exc))
            state.completed_at = datetime.utcnow().isoformat()
            return

        state.process = proc

        # ── stdout reader thread ─────────────────────────────────
        # Reads line-by-line into state._output_buf so we can detect
        # the verification URL without consuming stdin. The thread
        # exits naturally when proc closes stdout (i.e. when claude
        # CLI exits).
        def _drain_stdout():
            try:
                assert proc.stdout is not None
                for line in proc.stdout:
                    state._output_buf.append(line)
                    if not state.verification_url:
                        cleaned = self._clean_output(line)
                        url = URL_RE.search(cleaned)
                        if not url:
                            url = re.search(r"https://[^\s]+", cleaned)
                        if url:
                            state.verification_url = url.group(0)
                            # Promote to "pending" the moment we have a
                            # URL — UI starts polling immediately.
                            if state.status == "starting":
                                state.status = "pending"
            except Exception:
                # Reader thread errors aren't fatal — the main thread
                # observes proc.returncode and surfaces failure via
                # the buffered output.
                logger.debug("claude_auth stdout reader exited", exc_info=True)

        reader = threading.Thread(target=_drain_stdout, daemon=True)
        reader.start()

        # ── Wait for URL (max 10s) ───────────────────────────────
        # If the URL never appears something is wrong with the
        # subprocess (claude CLI may have changed flags, claude_home
        # may be unwritable, etc.). Fail fast.
        url_deadline = time.time() + 10
        while time.time() < url_deadline and state.status == "starting":
            if proc.poll() is not None:
                # Subprocess exited before we got a URL — surface its
                # combined output as the error.
                cleaned = self._clean_output(_snapshot_buf(state._output_buf))
                state.status = "failed"
                state.error = _humanise_cli_failure(cleaned) if cleaned else (
                    "Claude CLI exited before printing a verification URL"
                )
                state.completed_at = datetime.utcnow().isoformat()
                reader.join(timeout=2)
                self._cleanup(state)
                return
            time.sleep(0.2)

        if state.status == "starting":
            # No URL after 10s but proc still running — older fallback.
            cleaned = self._clean_output(_snapshot_buf(state._output_buf))
            self._parse_initial_output(state, cleaned)
            if state.status == "failed":
                _terminate_and_reap(proc)
                state.completed_at = datetime.utcnow().isoformat()
                reader.join(timeout=2)
                self._cleanup(state)
                return

        # ── Wait for /submit-code to fire, or for cancellation ──
        # `state._code_submitted` is set by `claude_auth_submit_code`
        # endpoint after writing the user's pasted code to stdin.
        # While waiting we also poll for the subprocess exiting on
        # its own (timeout / claude.com refused) and for explicit
        # cancellation.
        paste_deadline = time.time() + _OAUTH_PASTE_DEADLINE_SECONDS
        while time.time() < paste_deadline:
            if state.status == "cancelled":
                _terminate_and_reap(proc)
                reader.join(timeout=2)
                self._cleanup(state)
                return
            if state._code_submitted.is_set():
                break
            if proc.poll() is not None:
                # Subprocess died while waiting for paste — claude.com
                # may have refused, or the code TTL expired.
                cleaned = self._clean_output(_snapshot_buf(state._output_buf))
                state.status = "failed"
                state.error = _humanise_cli_failure(cleaned) if cleaned else (
                    "Claude CLI exited while waiting for verification code"
                )
                state.completed_at = datetime.utcnow().isoformat()
                reader.join(timeout=2)
                self._cleanup(state)
                return
            time.sleep(0.5)
        else:
            # Loop fell through without break or return → paste deadline expired.
            _terminate_and_reap(proc)
            state.status = "failed"
            state.error = (
                f"Timed out waiting for verification code ({_OAUTH_PASTE_DEADLINE_SECONDS // 60} min). "
                "Open the verification URL in a browser, copy the code, and try again."
            )
            state.completed_at = datetime.utcnow().isoformat()
            reader.join(timeout=2)
            self._cleanup(state)
            return

        # ── Code was submitted. Wait for proc to finish. ─────────
        try:
            proc.wait(timeout=_OAUTH_FINALIZE_TIMEOUT)
        except subprocess.TimeoutExpired:
            _terminate_and_reap(proc)
            state.status = "failed"
            state.error = (
                f"Claude CLI did not finish within {_OAUTH_FINALIZE_TIMEOUT}s "
                "after the code was submitted. Try again."
            )
            state.completed_at = datetime.utcnow().isoformat()
            reader.join(timeout=2)
            self._cleanup(state)
            return

        # Drain reader thread so any final lines (incl. the printed
        # token on the last stdout line) are in the buffer.
        reader.join(timeout=2)

        if proc.returncode == 0:
            # `claude auth login --claudeai` writes `.credentials.json` under
            # CLAUDE_CONFIG_DIR on success (no token is printed to stdout).
            # Install that native credential into the code-worker's shared HOME
            # volume so interactive PTY sessions pick it up, then mark connected.
            try:
                self._install_worker_credentials(state)
                state.status = "connected"
                state.connected = True
                state.error = None
            except Exception as exc:
                logger.exception("Failed to install Claude worker credentials")
                state.status = "failed"
                state.error = f"Failed to store credentials: {exc}"
        elif state.status != "cancelled":
            cleaned = self._clean_output(_snapshot_buf(state._output_buf))
            state.status = "failed"
            state.error = _humanise_cli_failure(cleaned)

        state.completed_at = datetime.utcnow().isoformat()
        self._cleanup(state)

    def submit_code(self, tenant_id: str, code: str) -> ClaudeLoginState:
        """Pipe the user-pasted verification code to the running
        subprocess's stdin and signal `_run_login` to wait for proc
        exit.

        Called from the `/submit-code` route. Strips whitespace +
        wrapping quotes from the paste, writes one line terminated
        with `\\n` (claude CLI's prompt reads a line; line terminator
        is `\\n` because the api container is Linux-only — never
        Windows), then closes stdin to surface EOF.

        Holds `self._lock` for the **entire** transition (state
        lookup → status guards → stdin write → status mutation →
        event signal) so it cannot interleave with `cancel_login`.
        Without this, both endpoints could race past their respective
        status checks and one would clobber the other's terminal
        state. The lock is released before this returns so callers
        polling `/status` aren't blocked.

        Raises `ClaudeAuthError` on caller errors (no active flow,
        wrong state, dead subprocess); the route handler translates
        these to `HTTPException`. The manager itself stays HTTP-free
        so non-FastAPI callers (CLI re-auth, tests, future Temporal
        activities) can use it directly.
        """
        # Normalise paste outside the lock — pure string ops, no
        # shared state mutated.
        clean_code = code.strip()
        if (clean_code.startswith('"') and clean_code.endswith('"')) or (
            clean_code.startswith("'") and clean_code.endswith("'")
        ):
            clean_code = clean_code[1:-1].strip()
        if not clean_code:
            raise ClaudeAuthError("Verification code is empty.")

        with self._lock:
            state = self._by_tenant.get(tenant_id)
            if not state:
                raise ClaudeAuthError(
                    "No active Claude login flow. Start one with `/start`.",
                    status_code=404,
                )
            if state.status != "pending":
                # Covers: already cancelled, already submitting (double
                # submit), already terminal (connected/failed). Anything
                # that's not 'pending' shouldn't accept a paste.
                raise ClaudeAuthError(
                    f"Login is in state '{state.status}', not 'pending'. Cannot accept code.",
                )
            if not state.process or state.process.poll() is not None:
                raise ClaudeAuthError(
                    "Claude CLI subprocess is no longer running. Re-run `/start`.",
                )

            try:
                assert state.process.stdin is not None
                state.process.stdin.write(clean_code + "\n")
                state.process.stdin.flush()
                state.process.stdin.close()
            except (BrokenPipeError, OSError) as exc:
                # Subprocess died between our state check and the write
                # (race the lock cannot help with — the child can die
                # at any moment). Surface with a hint to restart.
                raise ClaudeAuthError(
                    f"Could not deliver code to Claude CLI: {exc}. Re-run `/start`.",
                )

            # Status mutation + event signal happen under the lock so a
            # racing `cancel_login` either runs entirely before (and we
            # see state.status == 'cancelled' above) or entirely after
            # (and overwrites our 'submitting' cleanly — the cancel is
            # authoritative).
            state.status = "submitting"
            state._code_submitted.set()
            return state

    def _parse_initial_output(self, state: ClaudeLoginState, output: str) -> None:
        cleaned = self._clean_output(output)
        url_match = URL_RE.search(cleaned)

        if url_match:
            state.verification_url = url_match.group(0)
            state.status = "pending"
        elif not cleaned.strip():
            state.status = "starting"
        else:
            # Might contain the URL in raw output
            for line in cleaned.split("\n"):
                if "claude.com" in line and "http" in line:
                    url = re.search(r"https://[^\s]+", line)
                    if url:
                        state.verification_url = url.group(0)
                        state.status = "pending"
                        return
            state.status = "failed"
            state.error = "Could not read verification URL from Claude CLI"

    def _persist_credentials(self, state: ClaudeLoginState) -> None:
        """Persist the captured `sk-ant-oat01-…` token to the vault.

        Replaces the previous salvage-glob over `CLAUDE_CONFIG_DIR`
        that picked up the wrong artefact from `auth login --claudeai`.
        Now we take `state.captured_token` directly from the stdout
        capture in `_run_login`, verify the shape, **probe** it with a
        best-effort `claude --version` call so dev/test catches a
        malformed token early, then write it to the vault under the
        same `credential_key='session_token'` so the executor doesn't
        need to change.

        **Defense layering (read before changing either layer):**

          1. Shape gate — `_OAT01_PREFIX` startswith + the
             `_OAT01_TOKEN_RE` extraction in `_run_login`. This is the
             LOAD-BEARING defense; it runs in every environment
             including production where `claude` is not on PATH.
          2. Probe — `_probe_oauth_token_best_effort`. Runs ONLY if
             `claude` is on PATH (i.e. dev/test). In the prod api
             container the probe is a no-op (returns True without
             validating). It complements but does NOT replace the
             shape gate. See `_probe_oauth_token_best_effort` docstring
             for the full contract.

        Raises on any failure path — the caller (`_run_login`) catches
        and surfaces the message to the UI as `state.error`.
        """
        token = (state.captured_token or "").strip()
        if not token.startswith(_OAT01_PREFIX):
            # Shape gate. `claude setup-token` always prints a token
            # in this shape on success; anything else (empty string,
            # stray log line, ANSI noise that survived stripping) is
            # a bug we want surfaced loudly, not papered over.
            raise RuntimeError(
                f"Captured token does not match expected `{_OAT01_PREFIX}…` shape"
            )

        # ── Best-effort probe of the captured token ──────────────
        # `claude --version` with the token in env doesn't hit
        # Anthropic at all — it's a purely client-side shape check
        # that runs as part of CLI startup when an OAuth token is
        # supplied. The cheap thing we can do entirely offline is
        # exec the CLI with the candidate token in env and confirm it
        # exits 0 — if the token is malformed in a way the CLI itself
        # detects (truncated, wrong segment count) it surfaces here
        # rather than at every executor call afterwards.
        #
        # IMPORTANT: in the production api container `claude` is not
        # on PATH, so this probe returns True without doing anything
        # (see `_probe_oauth_token_best_effort` docstring). The shape
        # gate above is the real defense in prod.
        # Resolve through the module-level alias so tests that
        # `monkeypatch.setattr(ca, "_probe_oauth_token", …)` continue
        # to intercept the call. Both names resolve to
        # `_probe_oauth_token_best_effort` — the short name keeps
        # back-compat, the long name makes the contract obvious to
        # readers (see `_probe_oauth_token_best_effort` docstring).
        if not _probe_oauth_token(token):
            # NB: the CLI did the rejection client-side — `--version`
            # never contacts Anthropic. Word the message accordingly so
            # debug reports don't blame an Anthropic outage for what is
            # actually a token-shape mismatch the CLI caught locally.
            raise RuntimeError(
                "Claude CLI rejected the new OAuth token during probe. "
                "Try Connect again."
            )

        db: Session = SessionLocal()
        try:
            tid = uuid.UUID(state.tenant_id)

            # Find or create integration config
            config = (
                db.query(IntegrationConfig)
                .filter(
                    IntegrationConfig.tenant_id == tid,
                    IntegrationConfig.integration_name == "claude_code",
                )
                .first()
            )
            if not config:
                config = IntegrationConfig(
                    tenant_id=tid,
                    integration_name="claude_code",
                    enabled=True,
                )
                db.add(config)
                db.commit()
                db.refresh(config)
            elif not config.enabled:
                config.enabled = True
                db.add(config)
                db.commit()
                db.refresh(config)

            # Revoke any stale credential from the *other* flow so a
            # later read returns only the freshly-stored row.
            # `store_credential` revokes only same-`credential_key` rows
            # (see credential_vault.store_credential filter), so an
            # `api_key` → OAuth swap would otherwise leave the old
            # `api_key` row active and confuse cli_session_manager.
            _revoke_other_claude_credentials(db, config.id, tid, keep="session_token")

            # Store the long-lived `sk-ant-oat01-…` token. Key name
            # (`session_token`) is preserved for backwards-compat
            # with `cli_executors/claude.py`, which reads this key
            # and exports it as `CLAUDE_CODE_OAUTH_TOKEN`.
            store_credential(
                db,
                integration_config_id=config.id,
                tenant_id=tid,
                credential_key="session_token",
                plaintext_value=token,
                credential_type="oauth_token",
            )

        finally:
            db.close()

    def _install_worker_credentials(self, state: ClaudeLoginState) -> None:
        """Install the native credential `claude auth login` produced into the
        code-worker's shared HOME volume and mark the integration connected.

        Subscription Claude Code can no longer use `setup-token`/`-p`, so chat
        runs through the interactive PTY path which reads
        `$HOME/.claude/.credentials.json` in the worker HOME. The api container
        shares the worker's `claude_sessions` volume (both run as uid 1000), so
        we atomically drop the freshly-minted credential there at 0600. No
        long-lived token exists to store; we record connected state plus a
        marker `session_token` so `cli_executors/claude.py` resolves the tenant
        as an OAuth/subscription tenant and takes the interactive native-auth
        path (where the marker value is dropped, never sent to Anthropic).
        """
        home = state.claude_home or ""
        src = next(
            (
                c
                for c in (
                    os.path.join(home, ".credentials.json"),
                    os.path.join(home, ".claude", ".credentials.json"),
                )
                if os.path.isfile(c)
            ),
            None,
        )
        if not src:
            raise RuntimeError("Login completed but claude wrote no .credentials.json")
        worker_home = os.environ.get("CLAUDE_CODE_WORKER_HOME", "/home/codeworker")
        dst_dir = os.path.join(worker_home, ".claude")
        if not os.path.isdir(dst_dir):
            raise RuntimeError(
                f"Worker credential volume not mounted at {dst_dir} in the api "
                "container — add the claude_sessions volume to the api service."
            )
        with open(src, "rb") as fh:
            payload = fh.read()
        fd, tmp = tempfile.mkstemp(dir=dst_dir, prefix=".credentials.", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(payload)
            os.chmod(tmp, 0o600)
            os.replace(tmp, os.path.join(dst_dir, ".credentials.json"))
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

        db: Session = SessionLocal()
        try:
            tid = uuid.UUID(state.tenant_id)
            config = (
                db.query(IntegrationConfig)
                .filter(
                    IntegrationConfig.tenant_id == tid,
                    IntegrationConfig.integration_name == "claude_code",
                )
                .first()
            )
            if not config:
                config = IntegrationConfig(
                    tenant_id=tid, integration_name="claude_code", enabled=True
                )
                db.add(config)
                db.commit()
                db.refresh(config)
            elif not config.enabled:
                config.enabled = True
                db.add(config)
                db.commit()
                db.refresh(config)
            # Replace any stale api_key row, then store the OAuth marker so the
            # executor resolves kind="oauth" and takes the interactive path.
            _revoke_other_claude_credentials(db, config.id, tid, keep="session_token")
            store_credential(
                db,
                integration_config_id=config.id,
                tenant_id=tid,
                credential_key="session_token",
                plaintext_value="native-worker-login",
                credential_type="oauth_token",
            )
        finally:
            db.close()

    def _cleanup(self, state: ClaudeLoginState) -> None:
        if state.claude_home and os.path.isdir(state.claude_home):
            try:
                shutil.rmtree(state.claude_home, ignore_errors=True)
            except Exception:
                pass

    @staticmethod
    def _ensure_text(output) -> str:
        if isinstance(output, bytes):
            return output.decode("utf-8", errors="ignore")
        return output or ""

    @staticmethod
    def _clean_output(output) -> str:
        return ANSI_RE.sub("", ClaudeAuthManager._ensure_text(output))


_manager = ClaudeAuthManager()


# ── Cross-flow credential housekeeping ──────────────────────────────────────

def _revoke_other_claude_credentials(
    db: Session,
    integration_config_id,
    tenant_id,
    *,
    keep: str,
) -> None:
    """Revoke active claude_code credentials whose `credential_key` is NOT `keep`.

    `store_credential` only revokes rows with the **same** `credential_key`
    (vault filter at credential_vault.py:74-83). The OAuth flow stores
    `credential_key='session_token'` and the API-key flow stores
    `credential_key='api_key'`, so they live in disjoint key namespaces.
    Without this helper, switching flows leaves the other path's row
    active, and `retrieve_credentials_for_skill` returns both — letting
    cli_session_manager silently keep using a stale credential.

    Caller must commit after `store_credential` lands the new row.
    """
    others = [k for k in _CLAUDE_CREDENTIAL_KEYS if k != keep]
    if not others:
        return
    (
        db.query(IntegrationCredential)
        .filter(
            IntegrationCredential.integration_config_id == integration_config_id,
            IntegrationCredential.tenant_id == tenant_id,
            IntegrationCredential.credential_key.in_(others),
            IntegrationCredential.status == "active",
        )
        .update({"status": "revoked"}, synchronize_session=False)
    )


# ── API Routes ──────────────────────────────────────────────────────────────


def _tenant_has_claude_credential(db: Session, tenant_id) -> bool:
    """Check if tenant has a stored Claude Code credential in the vault.

    Recognises **either** the OAuth path (`credential_key='session_token'`,
    written by `_persist_credentials`) **or** the API-key fast-path
    (`credential_key='api_key'`, written by `/api-key`). Either is a
    valid "connected" signal — downstream consumers branch on the
    credential's `credential_type` to pick the auth shape.
    """
    tid = uuid.UUID(str(tenant_id)) if not isinstance(tenant_id, uuid.UUID) else tenant_id
    config = (
        db.query(IntegrationConfig)
        .filter(IntegrationConfig.tenant_id == tid, IntegrationConfig.integration_name == "claude_code")
        .first()
    )
    if not config:
        return False
    cred = (
        db.query(IntegrationCredential)
        .filter(
            IntegrationCredential.integration_config_id == config.id,
            IntegrationCredential.credential_key.in_(_CLAUDE_CREDENTIAL_KEYS),
            IntegrationCredential.status == "active",
        )
        .first()
    )
    return cred is not None


def _serialize_state(state: ClaudeLoginState, connected: bool = False) -> dict:
    # `state.error` should already be redacted at the assignment sites
    # (`_humanise_cli_failure` calls `_redact_oat01`). Belt-and-suspenders
    # here so a future code path that forgets to scrub cannot leak a
    # captured token to the polling browser via `/claude-auth/status`.
    return {
        "login_id": state.login_id if state else None,
        "status": state.status if state else "idle",
        "verification_url": state.verification_url if state else None,
        "connected": connected or (state.connected if state else False),
        "error": _redact_oat01(state.error) if state and state.error else None,
        # Whether the UI should render the "Paste your code" input.
        # Mirrors `status == 'pending'` but spelled out as a boolean
        # so the UI doesn't have to know the exact state-machine
        # vocabulary. `submitting` and `connected` close the input.
        "awaiting_code": bool(state and state.status == "pending"),
        # Whether `/cancel` will actually do anything. Mirrors the
        # `cancel_login` guard so the UI can disable the Cancel button
        # past `pending` (the paste has been delivered, cancel is a
        # no-op). UI shouldn't have to know the state-machine
        # vocabulary to render this correctly.
        "cancellable": bool(state and state.status in _CANCELLABLE_STATUSES),
    }


@router.post("/start")
def claude_auth_start(
    current_user: User = Depends(deps.get_current_active_user),
    db: Session = Depends(deps.get_db),
):
    """Start Claude Code OAuth login flow. Returns verification URL for browser."""
    tenant_id = str(current_user.tenant_id)
    state = _manager.start_login(tenant_id)

    # Wait briefly for URL to appear (sync handler — runs in thread pool)
    for _ in range(10):
        if state.verification_url or state.status in {"failed", "cancelled"}:
            break
        time.sleep(0.5)

    connected = _tenant_has_claude_credential(db, current_user.tenant_id)
    return _serialize_state(state, connected=connected)


@router.get("/status")
def claude_auth_status(
    current_user: User = Depends(deps.get_current_active_user),
    db: Session = Depends(deps.get_db),
):
    """Check Claude Code login flow status."""
    state = _manager.get_state(str(current_user.tenant_id))
    connected = _tenant_has_claude_credential(db, current_user.tenant_id)
    if not state:
        return {"status": "idle", "connected": connected}
    return _serialize_state(state, connected=connected)


@router.post("/cancel")
def claude_auth_cancel(
    current_user: User = Depends(deps.get_current_active_user),
):
    """Cancel an in-progress Claude Code login flow."""
    state = _manager.cancel_login(str(current_user.tenant_id))
    if not state:
        return {"status": "idle", "connected": False}
    return _serialize_state(state)


# ── API-key path (option a) ────────────────────────────────────────────────
# The subscription-OAuth flow above spawns `claude auth login --claudeai`
# inside the api container and tries to read its callback / paste-code,
# which is architecturally broken (the container has no browser and no
# way to receive the OAuth callback or feed a paste-code into the
# subprocess's stdin). For users who'd rather paste an Anthropic
# Console API key (`sk-ant-…`), this endpoint stores it directly in the
# credential vault under the same `claude_code` integration name, so
# downstream consumers see the same credential shape — they don't need
# to care which flow produced it.


class ClaudeApiKeyRequest(BaseModel):
    """Payload for `POST /api/v1/claude-auth/api-key`."""

    api_key: str = Field(
        ...,
        min_length=20,
        description="Anthropic Console API key — typically starts with `sk-ant-`.",
    )


@router.post("/api-key")
def claude_auth_set_api_key(
    body: ClaudeApiKeyRequest,
    current_user: User = Depends(deps.get_current_active_user),
    db: Session = Depends(deps.get_db),
):
    """Store a user-supplied Anthropic API key as the tenant's Claude credential.

    Cheap fast-path for users who can't use the subscription-OAuth flow
    (no browser inside the api container) but have an API key.

    Strips wrapping whitespace and any leading `ANTHROPIC_API_KEY=` /
    `Bearer ` prefix paste-from-shell-history users tend to include.
    Validates the `sk-ant-` prefix as a sanity check — Anthropic API
    keys always use it and the wrong prefix usually means a paste
    mistake (whole `claude.ai` cookie, an OpenAI key, etc.). Rejects
    short strings to catch obvious typos.

    Side-effects:
      * Creates `IntegrationConfig(integration_name='claude_code', enabled=True)`
        if absent, matching `_persist_credentials` so downstream readers
        (orchestration, MCP tools) see the same shape regardless of
        which flow stored the credential.
      * Stores the key in the vault with `credential_type='api_key'`
        (vs the OAuth flow's `oauth_token`). Consumers branch on the
        type when calling Anthropic.
      * `store_credential` revokes prior active credentials *with the
        same `credential_key`*. The OAuth path lives under
        `credential_key='session_token'`, this path under
        `credential_key='api_key'`, so swapping flows would otherwise
        leave the other path's row active. We explicitly revoke the
        cross-flow row via `_revoke_other_claude_credentials` to keep
        downstream reads single-rowed.
    """
    raw = _normalise_api_key_paste(body.api_key)

    if not raw.startswith("sk-ant-"):
        raise HTTPException(
            status_code=400,
            detail=(
                "API key must start with `sk-ant-` — that's the Anthropic Console "
                "prefix. If you pasted a Claude.ai session cookie or a "
                "different provider's key, swap it for an Anthropic Console "
                "API key from console.anthropic.com/settings/keys."
            ),
        )

    tid = current_user.tenant_id
    config = (
        db.query(IntegrationConfig)
        .filter(
            IntegrationConfig.tenant_id == tid,
            IntegrationConfig.integration_name == "claude_code",
        )
        .first()
    )
    if not config:
        config = IntegrationConfig(
            tenant_id=tid,
            integration_name="claude_code",
            enabled=True,
        )
        db.add(config)
        db.commit()
        db.refresh(config)
    elif not config.enabled:
        config.enabled = True
        db.add(config)
        db.commit()
        db.refresh(config)

    # Revoke any stale OAuth `session_token` so downstream readers
    # don't see two active credentials and silently prefer the old one
    # (see _revoke_other_claude_credentials docstring).
    _revoke_other_claude_credentials(db, config.id, tid, keep="api_key")

    store_credential(
        db,
        integration_config_id=config.id,
        tenant_id=tid,
        credential_key="api_key",
        plaintext_value=raw,
        credential_type="api_key",
    )

    return {
        "status": "connected",
        "connected": True,
        "credential_type": "api_key",
    }


# ── Paste-artefact normalisation ────────────────────────────────────────────

# Prefixes a user might accidentally paste alongside the key, matched
# case-insensitively. Order matters: longer/more-specific prefixes come
# first so `export ANTHROPIC_API_KEY=` peels off before
# `ANTHROPIC_API_KEY=`. Each successful match also strips wrapping
# whitespace so YAML-style `KEY:  value` (with extra spaces) works.
_API_KEY_PASTE_PREFIXES = (
    "export ANTHROPIC_API_KEY=",
    "ANTHROPIC_API_KEY=",
    "ANTHROPIC_API_KEY:",
    "x-api-key:",
    "Authorization: Bearer",
    "Authorization:",
    "Bearer",
    "bearer",
)


def _normalise_api_key_paste(raw: str) -> str:
    """Strip wrapping whitespace, common header/.env prefixes, and quotes.

    Idempotent: re-running on the result is a no-op. Case-insensitive
    on prefix matches because users paste shell history (`Bearer`),
    curl examples (`bearer`), and YAML (`x-api-key:`) interchangeably.
    """
    raw = raw.strip()
    raw_lower = raw.lower()
    for prefix in _API_KEY_PASTE_PREFIXES:
        if raw_lower.startswith(prefix.lower()):
            raw = raw[len(prefix):].lstrip(" \t")
            break
    # Strip a *single* layer of wrapping quotes (.env: `KEY="sk-ant-..."`).
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ('"', "'"):
        raw = raw[1:-1].strip()
    return raw


# ── Submit-code path (option b: stdin-forward) ─────────────────────────────


class ClaudeSubmitCodeRequest(BaseModel):
    """Payload for `/submit-code` — the verification code claude.com
    showed the user after they authorized in the browser."""

    code: str = Field(
        ...,
        min_length=4,
        description="The verification code from claude.com (typically a short alphanumeric token).",
    )


@router.post("/submit-code")
def claude_auth_submit_code(
    body: ClaudeSubmitCodeRequest,
    current_user: User = Depends(deps.get_current_active_user),
    db: Session = Depends(deps.get_db),
):
    """Forward a user-pasted verification code to the running claude
    CLI subprocess via stdin.

    This is the SECOND half of the subscription-OAuth flow. The first
    half (`/start`) spawned `claude auth login --claudeai` and surfaced
    the verification URL. The user opened that URL in a browser,
    authorized on claude.com, and got back a code. This endpoint pipes
    that code into the subprocess's stdin so it can complete the
    handshake.

    Why this is a separate endpoint instead of folding into `/start`:
    the `/start` call returns immediately with the URL — we can't
    block it waiting for a paste that might be 10 minutes away.
    Separating "I'm ready" from "here's my code" gives the UI a
    natural two-stage flow that mirrors what the user is actually
    doing.

    Status transitions on success:
      pending → submitting → connected
    Status transitions on the various failure modes are documented in
    `ClaudeAuthManager.submit_code`.
    """
    try:
        state = _manager.submit_code(str(current_user.tenant_id), body.code)
    except ClaudeAuthError as exc:
        # Domain → HTTP boundary. The manager raises ClaudeAuthError so
        # it stays usable from non-FastAPI callers (CLI re-auth tools,
        # tests). Route translates to HTTPException here.
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    connected = _tenant_has_claude_credential(db, current_user.tenant_id)
    return _serialize_state(state, connected=connected)
