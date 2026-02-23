"""
Skill Router Service - Orchestrates skill execution through tenant OpenClaw instances.

Routes skill calls by:
1. Resolving the tenant's running OpenClaw instance (TenantInstance query)
2. Validating SkillConfig (enabled, approval, rate limit)
3. Loading and decrypting credentials via CredentialVault
4. Calling the OpenClaw Gateway (HTTP MVP, WebSocket planned)
5. Logging execution to ExecutionTrace
"""

import uuid
import time
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from threading import Lock
from typing import Dict, Any, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.tenant_instance import TenantInstance
from app.models.skill_config import SkillConfig
from app.models.execution_trace import ExecutionTrace
from app.services.orchestration.credential_vault import retrieve_credentials_for_skill
from app.services.llm.router import LLMRouter

logger = logging.getLogger(__name__)

# Module-level circuit breaker state (shared across SkillRouter instances)
_circuit_breaker_lock = Lock()
_circuit_breaker_state: Dict[str, Dict[str, Any]] = defaultdict(
    lambda: {"failures": 0, "last_failure": None, "open_until": None}
)
CIRCUIT_BREAKER_THRESHOLD = 3
CIRCUIT_BREAKER_WINDOW = timedelta(minutes=5)
CIRCUIT_BREAKER_COOLDOWN = timedelta(minutes=2)


class SkillRouter:
    """Routes skill execution requests through tenant's OpenClaw instance."""

    def __init__(self, db: Session, tenant_id: uuid.UUID):
        self.db = db
        self.tenant_id = tenant_id

    def execute_skill(
        self,
        skill_name: str,
        payload: Dict[str, Any],
        task_id: Optional[uuid.UUID] = None,
        agent_id: Optional[uuid.UUID] = None,
    ) -> Dict[str, Any]:
        """
        Execute a skill through the tenant's OpenClaw instance.

        Steps:
        1. Resolve OpenClaw instance for tenant
        2. Validate skill config (enabled, approval, rate limit)
        3. Load decrypted credentials
        4. Call OpenClaw Gateway
        5. Log to ExecutionTrace

        Returns:
            Dict with status, result, duration_ms
        """
        start = time.time()

        # Step 1: Resolve instance
        instance = self._resolve_instance()
        if not instance:
            return {"status": "error", "error": "No running OpenClaw instance for tenant"}

        # Step 1.5: Circuit breaker check
        cb_error = self._check_circuit_breaker(str(instance.id))
        if cb_error:
            return cb_error

        # Step 2: Validate skill config
        skill_config = self._get_skill_config(skill_name)
        if not skill_config:
            return {"status": "error", "error": f"Skill '{skill_name}' not configured"}
        if not skill_config.enabled:
            return {"status": "error", "error": f"Skill '{skill_name}' is disabled"}
        if skill_config.requires_approval:
            return {"status": "pending_approval", "skill_name": skill_name}

        # Step 3: Load credentials
        credentials = retrieve_credentials_for_skill(
            self.db, skill_config.id, self.tenant_id
        )

        # Step 3.5: Resolve LLM model for this skill
        llm_info = self._resolve_llm(skill_config)

        # Step 4: Call OpenClaw Gateway
        result = self._call_openclaw(
            instance.internal_url,
            skill_name,
            payload,
            credentials,
            llm_info=llm_info,
        )

        # Step 4.5: Track circuit breaker state
        if result.get("status") == "error":
            self._record_failure(str(instance.id))
        else:
            self._record_success(str(instance.id))

        duration_ms = int((time.time() - start) * 1000)

        # Step 5: Log execution trace
        if task_id:
            self._log_trace(
                task_id=task_id,
                agent_id=agent_id,
                step_type="skill_call",
                details={
                    "skill_name": skill_name,
                    "instance_id": str(instance.id),
                    "status": result.get("status"),
                    "duration_ms": duration_ms,
                    "llm": llm_info,
                },
                duration_ms=duration_ms,
            )

        response = {
            "status": result.get("status", "completed"),
            "result": result.get("data"),
            "duration_ms": duration_ms,
        }
        if result.get("error"):
            response["error"] = result["error"]
        return response

    # ── Circuit Breaker Methods ────────────────────────────────────────

    def _check_circuit_breaker(self, instance_id: str) -> Optional[Dict[str, Any]]:
        """
        Check if the circuit breaker is open for the given instance.

        Returns an error dict if the circuit is open (too many recent failures),
        or None if the circuit is closed and the call may proceed.
        Automatically resets the circuit after the cooldown period elapses.
        """
        with _circuit_breaker_lock:
            state = _circuit_breaker_state[instance_id]
            if state["open_until"] is not None:
                if datetime.utcnow() < state["open_until"]:
                    logger.warning(
                        "Circuit breaker OPEN for instance %s until %s",
                        instance_id,
                        state["open_until"].isoformat(),
                    )
                    return {
                        "status": "error",
                        "error": "Circuit breaker open — instance temporarily unavailable",
                        "retry_after": state["open_until"].isoformat(),
                    }
                # Cooldown elapsed — half-open: reset and allow one attempt
                logger.info(
                    "Circuit breaker cooldown elapsed for instance %s, resetting",
                    instance_id,
                )
                state["failures"] = 0
                state["last_failure"] = None
                state["open_until"] = None
        return None

    def _record_failure(self, instance_id: str) -> None:
        """
        Record a failure for the given instance.

        Increments the failure counter. If the threshold is reached within the
        configured window, the circuit breaker opens for the cooldown duration.
        Old failures (outside the window) are ignored by resetting the counter.
        """
        with _circuit_breaker_lock:
            state = _circuit_breaker_state[instance_id]
            now = datetime.utcnow()

            # If the last failure was outside the window, start a fresh count
            if (
                state["last_failure"] is not None
                and now - state["last_failure"] > CIRCUIT_BREAKER_WINDOW
            ):
                state["failures"] = 0

            state["failures"] += 1
            state["last_failure"] = now

            if state["failures"] >= CIRCUIT_BREAKER_THRESHOLD:
                state["open_until"] = now + CIRCUIT_BREAKER_COOLDOWN
                logger.error(
                    "Circuit breaker OPENED for instance %s after %d failures "
                    "(cooldown until %s)",
                    instance_id,
                    state["failures"],
                    state["open_until"].isoformat(),
                )

    def _record_success(self, instance_id: str) -> None:
        """Reset the failure counter for the given instance on success."""
        with _circuit_breaker_lock:
            state = _circuit_breaker_state[instance_id]
            state["failures"] = 0
            state["last_failure"] = None
            state["open_until"] = None

    # ── Health Check ─────────────────────────────────────────────────

    def health_check(self) -> Dict[str, Any]:
        """Check health of tenant's OpenClaw instance via HTTP and WebSocket."""
        instance = self._resolve_instance()
        if not instance:
            return {"status": "no_instance", "healthy": False}

        import requests

        # HTTP check — verifies pod is serving
        http_ok = False
        try:
            response = requests.get(instance.internal_url, timeout=5)
            http_ok = response.status_code < 400
        except Exception:
            pass

        # WebSocket check — verifies gateway is accepting connections
        ws_ok = False
        try:
            import asyncio
            import json as _json

            ws_url = instance.internal_url.replace("http://", "ws://").replace("https://", "wss://")

            async def _ws_ping():
                import websockets
                async with websockets.connect(ws_url, open_timeout=5) as ws:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5)
                    frame = _json.loads(raw)
                    return frame.get("event") == "connect.challenge"

            ws_ok = asyncio.run(_ws_ping())
        except Exception:
            pass

        healthy = http_ok and ws_ok
        status = "healthy" if healthy else ("http_only" if http_ok else "unreachable")

        if not healthy:
            self._record_failure(str(instance.id))

        return {
            "status": status,
            "healthy": healthy,
            "instance_id": str(instance.id),
            "http_ok": http_ok,
            "ws_ok": ws_ok,
        }

    # ── Internal Helpers ─────────────────────────────────────────────

    def _resolve_llm(self, skill_config: SkillConfig) -> Dict[str, Any]:
        """Resolve LLM model configuration for the skill."""
        try:
            llm_router = LLMRouter(self.db)
            if skill_config.llm_config_id:
                model = llm_router.select_model(
                    tenant_id=self.tenant_id,
                    config_id=skill_config.llm_config_id,
                )
            else:
                model = llm_router.select_model(
                    tenant_id=self.tenant_id,
                )

            return {
                "model_name": model.name if model else None,
                "provider": model.provider.name if model and model.provider else None,
            }
        except (ValueError, Exception) as e:
            logger.warning("Could not resolve LLM for skill: %s", str(e))
            return {}

    def _resolve_instance(self) -> Optional[TenantInstance]:
        """Find the tenant's running OpenClaw instance."""
        return (
            self.db.query(TenantInstance)
            .filter(
                TenantInstance.tenant_id == self.tenant_id,
                TenantInstance.instance_type == "openclaw",
                TenantInstance.status == "running",
            )
            .first()
        )

    def _get_skill_config(self, skill_name: str) -> Optional[SkillConfig]:
        """Get skill configuration for the tenant."""
        return (
            self.db.query(SkillConfig)
            .filter(
                SkillConfig.tenant_id == self.tenant_id,
                SkillConfig.skill_name == skill_name,
            )
            .first()
        )

    @staticmethod
    def _sign_device_payload(
        private_key_pem: str,
        device_id: str,
        client_id: str,
        client_mode: str,
        role: str,
        scopes: list,
        signed_at_ms: int,
        token: str,
        nonce: str,
    ) -> str:
        """Sign the OpenClaw device auth payload with Ed25519."""
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
        import base64

        # Build payload: v2|deviceId|clientId|clientMode|role|scopes|signedAtMs|token|nonce
        payload_parts = [
            "v2",
            device_id,
            client_id,
            client_mode,
            role,
            ",".join(scopes),
            str(signed_at_ms),
            token or "",
            nonce,
        ]
        payload_str = "|".join(payload_parts)

        # Sign with Ed25519
        key = load_pem_private_key(private_key_pem.encode(), password=None)
        signature = key.sign(payload_str.encode())

        # Return base64url-encoded signature
        return base64.urlsafe_b64encode(signature).rstrip(b"=").decode()

    def _call_openclaw(
        self,
        internal_url: str,
        skill_name: str,
        payload: Dict[str, Any],
        credentials: Dict[str, str],
        llm_info: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Call OpenClaw Gateway via WebSocket.

        Protocol: connect → challenge-response (Ed25519) → agent method → collect reply.
        """
        import asyncio
        import json as _json

        from app.core.config import settings

        ws_url = internal_url.replace("http://", "ws://").replace("https://", "wss://")
        token = settings.OPENCLAW_GATEWAY_TOKEN
        device_id = settings.OPENCLAW_DEVICE_ID
        private_key = settings.OPENCLAW_DEVICE_PRIVATE_KEY
        public_key = settings.OPENCLAW_DEVICE_PUBLIC_KEY

        if not token:
            return {"status": "error", "error": "OPENCLAW_GATEWAY_TOKEN not configured"}
        if not device_id or not private_key or not public_key:
            return {"status": "error", "error": "OPENCLAW_DEVICE_ID/PRIVATE_KEY/PUBLIC_KEY not configured"}

        async def _execute():
            import websockets

            step = "ws_connect"
            try:
                async with websockets.connect(ws_url, open_timeout=10) as ws:
                    # Step 1: Receive challenge
                    step = "recv_challenge"
                    raw = await asyncio.wait_for(ws.recv(), timeout=10)
                    challenge = _json.loads(raw)
                    if challenge.get("event") != "connect.challenge":
                        return {"status": "error", "error": f"Unexpected frame: {challenge.get('event')}"}

                    nonce = challenge["payload"]["nonce"]

                    # Step 2: Authenticate with Ed25519 device signature
                    step = "authenticate"
                    role = "operator"
                    scopes = ["operator.admin", "operator.approvals", "operator.pairing"]
                    signed_at_ms = int(time.time() * 1000)

                    signature = self._sign_device_payload(
                        private_key_pem=private_key,
                        device_id=device_id,
                        client_id="gateway-client",
                        client_mode="backend",
                        role=role,
                        scopes=scopes,
                        signed_at_ms=signed_at_ms,
                        token=token,
                        nonce=nonce,
                    )

                    connect_req = {
                        "type": "req",
                        "id": f"connect-{uuid.uuid4().hex[:8]}",
                        "method": "connect",
                        "params": {
                            "minProtocol": 3,
                            "maxProtocol": 3,
                            "client": {
                                "id": "gateway-client",
                                "version": "1.0.0",
                                "platform": "linux",
                                "mode": "backend",
                            },
                            "role": role,
                            "scopes": scopes,
                            "auth": {"token": token},
                            "device": {
                                "id": device_id,
                                "publicKey": public_key,
                                "signature": signature,
                                "signedAt": signed_at_ms,
                                "nonce": nonce,
                            },
                        },
                    }
                    await ws.send(_json.dumps(connect_req))
                    step = "recv_auth_response"
                    hello_raw = await asyncio.wait_for(ws.recv(), timeout=10)
                    hello = _json.loads(hello_raw)
                    if not hello.get("ok"):
                        err = hello.get("error", hello)
                        return {"status": "error", "error": f"Auth failed: {err}"}

                    # Log available methods from hello-ok for diagnostics
                    features = hello.get("payload", {}).get("features", {})
                    available_methods = features.get("methods", [])
                    logger.info(
                        "OpenClaw hello-ok: methods=%s events=%s",
                        available_methods,
                        features.get("events", []),
                    )

                    # Step 3: Send skill execution via "agent" method
                    step = "send_skill"
                    exec_id = f"exec-{uuid.uuid4().hex[:8]}"
                    prompt = _json.dumps({
                        "skill": skill_name,
                        "payload": payload,
                        "credentials": credentials,
                        "llm": llm_info or {},
                    })
                    exec_req = {
                        "type": "req",
                        "id": exec_id,
                        "method": "agent",
                        "params": {
                            "message": f"Execute skill '{skill_name}' with payload: {prompt}",
                            "idempotencyKey": exec_id,
                            "sessionKey": "main",
                        },
                    }
                    await ws.send(_json.dumps(exec_req))

                    # Step 4: Collect response (wait for matching res or timeout)
                    step = "collect_response"
                    deadline = asyncio.get_event_loop().time() + 60
                    result_data = None
                    while asyncio.get_event_loop().time() < deadline:
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=5)
                            frame = _json.loads(raw)
                            # Look for the response to our exec request
                            if frame.get("type") == "res" and frame.get("id") == exec_id:
                                if frame.get("ok"):
                                    result_data = frame.get("payload", {})
                                else:
                                    return {"status": "error", "error": str(frame.get("error", "Unknown"))}
                                break
                            # Also accept event frames with results
                            if frame.get("type") == "event" and frame.get("event") in (
                                "session.message", "agent.message", "message",
                            ):
                                result_data = frame.get("payload", {})
                                break
                        except asyncio.TimeoutError:
                            continue

                    if result_data is None:
                        return {"status": "error", "error": "No response from OpenClaw within timeout"}

                    return {"status": "completed", "data": result_data}

            except Exception as inner_e:
                error_msg = f"[step={step}] {type(inner_e).__name__}: {inner_e}"
                logger.error("OpenClaw WS inner error: %s", error_msg)
                return {"status": "error", "error": error_msg}

        try:
            loop = None
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                pass

            if loop and loop.is_running():
                # We're inside an existing event loop (e.g. FastAPI async endpoint)
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    result = pool.submit(asyncio.run, _execute()).result(timeout=90)
            else:
                result = asyncio.run(_execute())

            return result

        except Exception as e:
            error_msg = f"[outer] {type(e).__name__}: {e}"
            logger.error("OpenClaw WebSocket error for skill '%s': %s", skill_name, error_msg)
            return {"status": "error", "error": error_msg}

    def _log_trace(
        self,
        task_id: uuid.UUID,
        step_type: str,
        details: Dict[str, Any],
        duration_ms: int,
        agent_id: Optional[uuid.UUID] = None,
    ):
        """Write an ExecutionTrace record."""
        max_order = (
            self.db.query(func.max(ExecutionTrace.step_order))
            .filter(ExecutionTrace.task_id == task_id)
            .scalar()
        ) or 0

        trace = ExecutionTrace(
            task_id=task_id,
            tenant_id=self.tenant_id,
            step_type=step_type,
            step_order=max_order + 1,
            agent_id=agent_id,
            details=details,
            duration_ms=duration_ms,
        )
        self.db.add(trace)
        self.db.commit()
