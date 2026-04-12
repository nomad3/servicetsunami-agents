"""Gemini CLI OAuth login flow.

Same pattern as codex_auth.py and claude_auth.py — spawns `gemini` in a
tenant-scoped HOME directory with NO_BROWSER=true, captures the verification
URL from stdout via a pty, exposes it via API, and waits for the user to
paste back the auth code. Persists the resulting OAuth credentials to the
encrypted vault.

Why this is needed:
- Gemini CLI's Cloud Code Private API requires a token issued by Gemini's
  own OAuth client (681255809395-...), NOT our platform's client (553113309640-...).
- A refresh_token is bound to the OAuth client that issued it — cross-client
  refresh fails with "unauthorized_client".
- Therefore the only way to get a working Gemini CLI token is to run the
  Gemini CLI auth flow itself, which uses its built-in client_id.
"""
import errno
import fcntl
import json
import logging
import os
import pty
import re
import select
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
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
URL_RE = re.compile(r"https://accounts\.google\.com/o/oauth2/[^\s]+")


@dataclass
class GeminiLoginState:
    login_id: str
    tenant_id: str
    status: str = "starting"
    verification_url: Optional[str] = None
    error: Optional[str] = None
    connected: bool = False
    started_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    completed_at: Optional[str] = None
    gemini_home: Optional[str] = None
    process: Optional[subprocess.Popen] = field(default=None, repr=False, compare=False)
    pty_fd: Optional[int] = field(default=None, repr=False, compare=False)
    output_buffer: str = field(default="", repr=False, compare=False)


class GeminiAuthManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._by_tenant: Dict[str, GeminiLoginState] = {}

    def get_state(self, tenant_id: str) -> Optional[GeminiLoginState]:
        with self._lock:
            return self._by_tenant.get(tenant_id)

    def start_login(self, tenant_id: str) -> GeminiLoginState:
        with self._lock:
            existing = self._by_tenant.get(tenant_id)
            if existing and existing.status in {"starting", "pending"} and existing.process:
                return existing

            if existing and existing.process and existing.process.poll() is None:
                try:
                    existing.process.terminate()
                except Exception:
                    logger.debug("Failed to terminate previous Gemini auth process", exc_info=True)

            login_id = str(uuid.uuid4())
            gemini_home = tempfile.mkdtemp(prefix=f"gemini-auth-{tenant_id[:8]}-")
            state = GeminiLoginState(
                login_id=login_id,
                tenant_id=tenant_id,
                gemini_home=gemini_home,
            )
            self._by_tenant[tenant_id] = state

        threading.Thread(target=self._run_login, args=(state,), daemon=True).start()
        return state

    def cancel_login(self, tenant_id: str) -> Optional[GeminiLoginState]:
        with self._lock:
            state = self._by_tenant.get(tenant_id)
        if not state:
            return None

        if state.process and state.process.poll() is None:
            try:
                state.process.terminate()
            except Exception:
                logger.debug("Failed to terminate Gemini auth process", exc_info=True)

        state.status = "cancelled"
        state.error = "Login cancelled"
        state.completed_at = datetime.utcnow().isoformat()
        return state

    def submit_code(self, tenant_id: str, code: str) -> Optional[GeminiLoginState]:
        """Pipe an auth code into the running gemini process via its pty."""
        with self._lock:
            state = self._by_tenant.get(tenant_id)
        if not state or not state.pty_fd:
            return None
        try:
            os.write(state.pty_fd, (code.strip() + "\n").encode("utf-8"))
        except Exception as exc:
            logger.warning("Failed to write code to gemini pty: %s", exc)
            state.error = f"Failed to submit code: {exc}"
            state.status = "failed"
        return state

    def _run_login(self, state: GeminiLoginState) -> None:
        # Pre-create .gemini directory with settings.json (oauth-personal)
        # and projects.json so gemini doesn't fail before reaching auth.
        gemini_dir = os.path.join(state.gemini_home or "", ".gemini")
        os.makedirs(gemini_dir, exist_ok=True)
        with open(os.path.join(gemini_dir, "settings.json"), "w") as f:
            json.dump({
                "security": {
                    "auth": {"selectedType": "oauth-personal"}
                }
            }, f, indent=2)
        with open(os.path.join(gemini_dir, "projects.json"), "w") as f:
            json.dump({"projects": {}}, f, indent=2)

        # Spawn gemini in a pty so isInteractive() returns true.
        # NO_BROWSER=true forces it into the authWithUserCode flow which
        # prints a verification URL and waits for the user to paste a code.
        env = {
            **os.environ,
            "HOME": state.gemini_home or "",
            "NO_BROWSER": "true",
            "TERM": "xterm-256color",
        }
        # Drop project vars so it doesn't try Code Assist enterprise validation
        for k in ("GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_PROJECT_ID", "GEMINI_PROJECT_ID"):
            env.pop(k, None)

        try:
            master_fd, slave_fd = pty.openpty()
            proc = subprocess.Popen(
                ["gemini"],
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                env=env,
                close_fds=True,
            )
            os.close(slave_fd)
            # Make master_fd non-blocking
            flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
            fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        except FileNotFoundError:
            state.status = "failed"
            state.error = "Gemini CLI not found on the API service"
            state.completed_at = datetime.utcnow().isoformat()
            self._cleanup_state_home(state)
            return
        except Exception as exc:
            state.status = "failed"
            state.error = str(exc)
            state.completed_at = datetime.utcnow().isoformat()
            self._cleanup_state_home(state)
            return

        state.process = proc
        state.pty_fd = master_fd

        # Read output for up to 30 seconds, looking for the verification URL
        deadline = time.time() + 30
        while time.time() < deadline:
            if proc.poll() is not None:
                break
            try:
                ready, _, _ = select.select([master_fd], [], [], 0.5)
                if master_fd in ready:
                    chunk = os.read(master_fd, 4096).decode("utf-8", errors="replace")
                    state.output_buffer += chunk
            except OSError as e:
                if e.errno not in (errno.EAGAIN, errno.EIO):
                    logger.warning("Error reading gemini pty: %s", e)
                    break
                continue

            cleaned = self._clean_output(state.output_buffer)
            url_match = URL_RE.search(cleaned)
            if url_match and not state.verification_url:
                state.verification_url = url_match.group(0)
                state.status = "pending"
                logger.info("Gemini auth URL captured for tenant %s", state.tenant_id[:8])
                # Don't break — keep reading in background to capture the
                # post-code-submission output

        if state.status == "starting":
            state.status = "failed"
            state.error = "Failed to capture verification URL from Gemini CLI within 30s"
            try:
                proc.terminate()
            except Exception:
                pass
            self._cleanup_state_home(state)
            state.completed_at = datetime.utcnow().isoformat()
            return

        # Continue reading output in the background while waiting for code submission
        threading.Thread(target=self._read_until_done, args=(state,), daemon=True).start()

    def _read_until_done(self, state: GeminiLoginState) -> None:
        """Drain pty output until oauth_creds.json appears or the process exits.

        gemini-cli's NO_BROWSER auth writes oauth_creds.json the moment the
        auth code is exchanged for tokens, but the process keeps running
        (it drops into its REPL). We must watch for the file rather than
        waiting for proc to exit.
        """
        deadline = time.time() + 600  # 10 minute window for user to paste code
        proc = state.process
        master_fd = state.pty_fd
        if not proc or master_fd is None:
            return

        creds_path = os.path.join(state.gemini_home or "", ".gemini", "oauth_creds.json")

        while time.time() < deadline:
            if proc.poll() is not None:
                break
            if os.path.exists(creds_path):
                # Give the file a moment to be fully flushed
                time.sleep(0.3)
                break
            try:
                ready, _, _ = select.select([master_fd], [], [], 1.0)
                if master_fd in ready:
                    chunk = os.read(master_fd, 4096).decode("utf-8", errors="replace")
                    state.output_buffer += chunk
            except OSError as e:
                if e.errno not in (errno.EAGAIN, errno.EIO):
                    break
                continue

        if os.path.exists(creds_path):
            try:
                self._persist_creds(state.tenant_id, creds_path)
                state.status = "connected"
                state.connected = True
                state.error = None
                logger.info("Gemini CLI credentials persisted for tenant %s", state.tenant_id[:8])
            except Exception as exc:
                logger.exception("Failed to persist Gemini oauth_creds.json")
                state.status = "failed"
                state.error = f"Failed to store credentials: {exc}"
        elif state.status not in {"cancelled", "connected"}:
            state.status = "failed"
            cleaned = self._clean_output(state.output_buffer)
            state.error = cleaned[-500:] if cleaned else "Gemini auth flow did not produce credentials"

        state.completed_at = datetime.utcnow().isoformat()
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
        self._cleanup_state_home(state)

    def _persist_creds(self, tenant_id: str, creds_path: str) -> None:
        db: Session = SessionLocal()
        try:
            tid = uuid.UUID(tenant_id)
            config = (
                db.query(IntegrationConfig)
                .filter(
                    IntegrationConfig.tenant_id == tid,
                    IntegrationConfig.integration_name == "gemini_cli",
                )
                .first()
            )
            if not config:
                config = IntegrationConfig(
                    tenant_id=tid,
                    integration_name="gemini_cli",
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

            with open(creds_path) as f:
                creds_json = f.read()
            creds = json.loads(creds_json)

            # Store the full oauth_creds.json blob
            store_credential(
                db,
                integration_config_id=config.id,
                tenant_id=tid,
                credential_key="oauth_creds",
                plaintext_value=creds_json,
                credential_type="oauth_token",
            )
            # Also store individual fields for convenience (the code-worker
            # reads these via _fetch_integration_credentials)
            if creds.get("access_token"):
                store_credential(
                    db,
                    integration_config_id=config.id,
                    tenant_id=tid,
                    credential_key="oauth_token",
                    plaintext_value=creds["access_token"],
                    credential_type="oauth_token",
                )
            if creds.get("refresh_token"):
                store_credential(
                    db,
                    integration_config_id=config.id,
                    tenant_id=tid,
                    credential_key="refresh_token",
                    plaintext_value=creds["refresh_token"],
                    credential_type="oauth_token",
                )
        finally:
            db.close()

    @staticmethod
    def _ensure_text(output: Optional[object]) -> str:
        if isinstance(output, bytes):
            return output.decode("utf-8", errors="ignore")
        return output or ""

    @staticmethod
    def _clean_output(output: Optional[object]) -> str:
        return ANSI_RE.sub("", GeminiAuthManager._ensure_text(output))

    @staticmethod
    def _cleanup_state_home(state: GeminiLoginState) -> None:
        if state.gemini_home and os.path.isdir(state.gemini_home):
            shutil.rmtree(state.gemini_home, ignore_errors=True)


manager = GeminiAuthManager()


def _serialize_state(state: Optional[GeminiLoginState], connected: bool = False) -> dict:
    """Serialize manager state. The `connected` flag is the DB truth and is
    authoritative — manager state is in-memory cache and may be stale after
    a disconnect, so we never let an old state.connected override a fresh DB
    "no creds" answer.
    """
    if not state:
        return {
            "status": "connected" if connected else "idle",
            "connected": connected,
            "verification_url": None,
            "login_id": None,
            "error": None,
            "started_at": None,
            "completed_at": None,
        }

    # Sync stale manager flag with DB truth so a revoke is reflected immediately.
    if not connected and state.connected:
        state.connected = False
        if state.status == "connected":
            state.status = "idle"

    return {
        "login_id": state.login_id,
        "status": state.status,
        "verification_url": state.verification_url,
        "error": state.error,
        "connected": connected,
        "started_at": state.started_at,
        "completed_at": state.completed_at,
    }


def _tenant_has_gemini_credential(db: Session, tenant_id: uuid.UUID) -> bool:
    config = (
        db.query(IntegrationConfig)
        .filter(
            IntegrationConfig.tenant_id == tenant_id,
            IntegrationConfig.integration_name == "gemini_cli",
            IntegrationConfig.enabled.is_(True),
        )
        .first()
    )
    if not config:
        return False

    credential = (
        db.query(IntegrationCredential.id)
        .filter(
            IntegrationCredential.integration_config_id == config.id,
            IntegrationCredential.tenant_id == tenant_id,
            IntegrationCredential.credential_key.in_(["oauth_creds", "oauth_token"]),
            IntegrationCredential.status == "active",
        )
        .first()
    )
    return credential is not None


class SubmitCodeBody(BaseModel):
    code: str


@router.post("/start")
def start_gemini_auth(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    state = manager.start_login(str(current_user.tenant_id))
    # Wait briefly for the URL to appear in the output
    for _ in range(60):
        if state.status in {"pending", "failed", "connected"} or state.verification_url:
            break
        time.sleep(0.5)
    connected = _tenant_has_gemini_credential(db, current_user.tenant_id)
    return _serialize_state(state, connected=connected)


@router.get("/status")
def get_gemini_auth_status(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    state = manager.get_state(str(current_user.tenant_id))
    connected = _tenant_has_gemini_credential(db, current_user.tenant_id)
    return _serialize_state(state, connected=connected)


@router.post("/submit-code")
def submit_gemini_auth_code(
    body: SubmitCodeBody,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    state = manager.submit_code(str(current_user.tenant_id), body.code)
    if not state:
        raise HTTPException(status_code=404, detail="No active Gemini login flow")
    connected = _tenant_has_gemini_credential(db, current_user.tenant_id)
    return _serialize_state(state, connected=connected)


@router.post("/cancel")
def cancel_gemini_auth(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    state = manager.cancel_login(str(current_user.tenant_id))
    if not state:
        raise HTTPException(status_code=404, detail="No active Gemini login flow")
    connected = _tenant_has_gemini_credential(db, current_user.tenant_id)
    return _serialize_state(state, connected=connected)


@router.post("/disconnect")
def disconnect_gemini_auth(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Revoke all Gemini CLI credentials for the current tenant and reset
    in-memory manager state. Returns idle status.
    """
    tid = current_user.tenant_id
    config = (
        db.query(IntegrationConfig)
        .filter(
            IntegrationConfig.tenant_id == tid,
            IntegrationConfig.integration_name == "gemini_cli",
        )
        .first()
    )
    revoked_count = 0
    if config:
        active_creds = (
            db.query(IntegrationCredential)
            .filter(
                IntegrationCredential.integration_config_id == config.id,
                IntegrationCredential.tenant_id == tid,
                IntegrationCredential.status == "active",
            )
            .all()
        )
        for cred in active_creds:
            cred.status = "revoked"
            revoked_count += 1
        db.commit()

    # Reset in-memory manager state for this tenant
    with manager._lock:
        state = manager._by_tenant.get(str(tid))
        if state:
            if state.process and state.process.poll() is None:
                try:
                    state.process.terminate()
                except Exception:
                    pass
            manager._by_tenant.pop(str(tid), None)

    return {
        "status": "idle",
        "connected": False,
        "verification_url": None,
        "login_id": None,
        "error": None,
        "started_at": None,
        "completed_at": None,
        "revoked": revoked_count,
    }
