import logging
import os
import re
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
URL_RE = re.compile(r"https://auth\.openai\.com/codex/device")
CODE_RE = re.compile(r"\b[A-Z0-9]{4}-[A-Z0-9]{4,5}\b")


@dataclass
class CodexLoginState:
    login_id: str
    tenant_id: str
    status: str = "starting"
    verification_url: Optional[str] = None
    user_code: Optional[str] = None
    error: Optional[str] = None
    connected: bool = False
    started_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    completed_at: Optional[str] = None
    codex_home: Optional[str] = None
    process: Optional[subprocess.Popen] = field(default=None, repr=False, compare=False)


class CodexAuthManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._by_tenant: Dict[str, CodexLoginState] = {}

    def get_state(self, tenant_id: str) -> Optional[CodexLoginState]:
        with self._lock:
            return self._by_tenant.get(tenant_id)

    def start_login(self, tenant_id: str) -> CodexLoginState:
        with self._lock:
            existing = self._by_tenant.get(tenant_id)
            if existing and existing.status in {"starting", "pending"} and existing.process:
                return existing

            if existing and existing.process and existing.process.poll() is None:
                try:
                    existing.process.terminate()
                except Exception:
                    logger.debug("Failed to terminate previous Codex device-auth process", exc_info=True)

            login_id = str(uuid.uuid4())
            codex_home = tempfile.mkdtemp(prefix=f"codex-device-{tenant_id[:8]}-")
            state = CodexLoginState(
                login_id=login_id,
                tenant_id=tenant_id,
                codex_home=codex_home,
            )
            self._by_tenant[tenant_id] = state

        threading.Thread(target=self._run_login, args=(state,), daemon=True).start()
        return state

    def cancel_login(self, tenant_id: str) -> Optional[CodexLoginState]:
        with self._lock:
            state = self._by_tenant.get(tenant_id)
        if not state:
            return None

        if state.process and state.process.poll() is None:
            try:
                state.process.terminate()
            except Exception:
                logger.debug("Failed to terminate Codex device-auth process", exc_info=True)

        state.status = "cancelled"
        state.error = "Login cancelled"
        state.completed_at = datetime.utcnow().isoformat()
        return state

    def _run_login(self, state: CodexLoginState) -> None:
        cmd = ["codex", "login", "--device-auth"]
        env = {**os.environ, "CODEX_HOME": state.codex_home or ""}

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
            )
        except FileNotFoundError:
            state.status = "failed"
            state.error = "Codex CLI not found on the API service"
            state.completed_at = datetime.utcnow().isoformat()
            return
        except Exception as exc:
            state.status = "failed"
            state.error = str(exc)
            state.completed_at = datetime.utcnow().isoformat()
            return

        state.process = proc

        try:
            proc.communicate(timeout=3)
            initial_output = ""
        except subprocess.TimeoutExpired as exc:
            initial_output = self._ensure_text(exc.output)
        except Exception as exc:
            state.status = "failed"
            state.error = str(exc)
            state.completed_at = datetime.utcnow().isoformat()
            return

        self._parse_initial_output(state, initial_output)

        try:
            remaining_output, _ = proc.communicate()
        except Exception as exc:
            state.status = "failed"
            state.error = str(exc)
            state.completed_at = datetime.utcnow().isoformat()
            self._cleanup_state_home(state)
            return

        remaining_output = self._ensure_text(remaining_output)
        combined_output = (initial_output or "") + (remaining_output or "")
        auth_path = os.path.join(state.codex_home or "", "auth.json")

        if proc.returncode == 0 and os.path.exists(auth_path):
            try:
                self._persist_auth_json(state.tenant_id, auth_path)
                state.status = "connected"
                state.connected = True
                state.error = None
            except Exception as exc:
                logger.exception("Failed to persist Codex auth.json")
                state.status = "failed"
                state.error = f"Failed to store auth.json: {exc}"
        elif state.status != "cancelled":
            state.status = "failed"
            cleaned = self._clean_output(combined_output)
            state.error = cleaned[-500:] if cleaned else "Codex device authorization failed"

        state.completed_at = datetime.utcnow().isoformat()
        self._cleanup_state_home(state)

    def _parse_initial_output(self, state: CodexLoginState, output: str) -> None:
        cleaned = self._clean_output(output)
        url_match = URL_RE.search(cleaned)
        code_match = CODE_RE.search(cleaned)

        if url_match:
            state.verification_url = url_match.group(0)
        if code_match:
            state.user_code = code_match.group(0)

        if state.verification_url and state.user_code:
            state.status = "pending"
        elif not cleaned.strip():
            state.status = "starting"
        else:
            state.status = "failed"
            state.error = "Failed to read verification URL from Codex CLI output"

    def _persist_auth_json(self, tenant_id: str, auth_path: str) -> None:
        db: Session = SessionLocal()
        try:
            tid = uuid.UUID(tenant_id)
            config = (
                db.query(IntegrationConfig)
                .filter(
                    IntegrationConfig.tenant_id == tid,
                    IntegrationConfig.integration_name == "codex",
                )
                .first()
            )
            if not config:
                config = IntegrationConfig(
                    tenant_id=tid,
                    integration_name="codex",
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

            with open(auth_path) as f:
                auth_json = f.read()

            store_credential(
                db,
                integration_config_id=config.id,
                tenant_id=tid,
                credential_key="auth_json",
                plaintext_value=auth_json,
                credential_type="oauth_token",
            )

            legacy = (
                db.query(IntegrationCredential)
                .filter(
                    IntegrationCredential.integration_config_id == config.id,
                    IntegrationCredential.tenant_id == tid,
                    IntegrationCredential.credential_key == "session_token",
                    IntegrationCredential.status == "active",
                )
                .all()
            )
            for item in legacy:
                item.status = "revoked"
            db.commit()
        finally:
            db.close()

    @staticmethod
    def _ensure_text(output: Optional[object]) -> str:
        if isinstance(output, bytes):
            return output.decode("utf-8", errors="ignore")
        return output or ""

    @staticmethod
    def _clean_output(output: Optional[object]) -> str:
        return ANSI_RE.sub("", CodexAuthManager._ensure_text(output))

    @staticmethod
    def _cleanup_state_home(state: CodexLoginState) -> None:
        if state.codex_home and os.path.isdir(state.codex_home):
            shutil.rmtree(state.codex_home, ignore_errors=True)


manager = CodexAuthManager()


def _serialize_state(state: Optional[CodexLoginState], connected: bool = False) -> dict:
    if not state:
        return {
            "status": "connected" if connected else "idle",
            "connected": connected,
            "verification_url": None,
            "user_code": None,
            "login_id": None,
            "error": None,
            "started_at": None,
            "completed_at": None,
        }

    return {
        "login_id": state.login_id,
        "status": state.status,
        "verification_url": state.verification_url,
        "user_code": state.user_code,
        "error": state.error,
        "connected": bool(state.connected or connected),
        "started_at": state.started_at,
        "completed_at": state.completed_at,
    }


def _tenant_has_codex_credential(db: Session, tenant_id: uuid.UUID) -> bool:
    config = (
        db.query(IntegrationConfig)
        .filter(
            IntegrationConfig.tenant_id == tenant_id,
            IntegrationConfig.integration_name == "codex",
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
            IntegrationCredential.credential_key.in_(["auth_json", "session_token"]),
            IntegrationCredential.status == "active",
        )
        .first()
    )
    return credential is not None


@router.post("/start")
def start_codex_device_auth(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    state = manager.start_login(str(current_user.tenant_id))
    for _ in range(40):
        if state.status in {"pending", "failed", "connected"} or state.verification_url or state.user_code:
            break
        time.sleep(0.1)
    connected = _tenant_has_codex_credential(db, current_user.tenant_id)
    return _serialize_state(state, connected=connected)


@router.get("/status")
def get_codex_device_auth_status(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    state = manager.get_state(str(current_user.tenant_id))
    connected = _tenant_has_codex_credential(db, current_user.tenant_id)
    return _serialize_state(state, connected=connected)


@router.post("/cancel")
def cancel_codex_device_auth(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    state = manager.cancel_login(str(current_user.tenant_id))
    if not state:
        raise HTTPException(status_code=404, detail="No active Codex login flow")
    connected = _tenant_has_codex_credential(db, current_user.tenant_id)
    return _serialize_state(state, connected=connected)
