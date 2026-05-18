"""GlmAdapter — wraps cli_executors.glm.execute_glm_chat.

Phase 2 worker-side ProviderAdapter for the ``glm`` platform (Wave 2b).
Like Kimi, GLM has no local CLI binary: the executor talks to Zhipu's
OpenAI-compatible HTTP endpoint directly via httpx. (Zhipu publishes a
Python developer CLI on GitHub, but it is not a runtime dependency and
we do not bake it into the image.)

Preflight collapses to a single check: do we have a Zhipu API key in
the tenant vault (or a shared env-var fallback)? Cloud reachability is
not probed up-front — Zhipu surfaces billing/quota errors as HTTP 4xx
at request time, which ``classify_error`` maps into the canonical
Status enum.
"""
from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass
from typing import Optional

from cli_orchestrator.adapters.base import (
    ExecutionRequest,
    ExecutionResult,
    PreflightResult,
)
from cli_orchestrator.classifier import classify
from cli_orchestrator.redaction import redact
from cli_orchestrator.status import Status

from cli_executors.glm import execute_glm_chat

from ._common import (
    check_credential_for_platform,
    map_chat_cli_result_to_execution_result,
    time_preflight_helper,
    truncate,
)
from .preflight_deps import PreflightDeps


logger = logging.getLogger(__name__)


@dataclass
class _MinimalChatCliInput:
    platform: str
    message: str
    tenant_id: str
    instruction_md_content: str = ""
    mcp_config: str = ""
    image_b64: str = ""
    image_mime: str = ""
    session_id: str = ""
    model: str = ""
    allowed_tools: str = ""


class GlmAdapter:
    name = "glm"

    def preflight(self, req: ExecutionRequest) -> PreflightResult:
        # 1. Credentials present in the tenant vault. The shared
        # ``ZHIPU_API_KEY`` operator env var acts as a fallback —
        # if either path resolves, preflight passes.
        tenant_id = req.tenant_id or (req.payload or {}).get("tenant_id") or ""
        if tenant_id:
            with time_preflight_helper(self.name, "credentials_present"):
                cr = check_credential_for_platform(
                    PreflightDeps.get(), tenant_id, self.name,
                )
            if not cr.ok:
                # Vault miss — accept ONLY if the operator wired a
                # shared key into the worker container env.
                if os.environ.get("ZHIPU_API_KEY"):
                    return PreflightResult.succeed()
                return cr

        # 2. Cloud API reachability — not probed at preflight.
        # Zhipu surfaces billing/quota errors as HTTP 4xx at request
        # time; ``classify_error`` maps them downstream.

        return PreflightResult.succeed()

    def classify_error(
        self,
        stderr: Optional[str],
        exit_code: Optional[int],
        exc: Optional[BaseException],
    ) -> Status:
        return classify(stderr, exit_code, exc)

    def run(self, req: ExecutionRequest) -> ExecutionResult:
        run_id = req.run_id or str(uuid.uuid4())
        payload = req.payload or {}
        session_dir = payload.get("session_dir") or "/tmp"
        task_input = _MinimalChatCliInput(
            platform=self.name,
            message=payload.get("message", ""),
            tenant_id=req.tenant_id or payload.get("tenant_id", ""),
            instruction_md_content=payload.get("instruction_md_content", ""),
            mcp_config=payload.get("mcp_config", ""),
            image_b64=payload.get("image_b64", ""),
            image_mime=payload.get("image_mime", ""),
            session_id=payload.get("session_id", ""),
            model=payload.get("model", ""),
            allowed_tools=payload.get("allowed_tools", ""),
        )
        try:
            cli_result = execute_glm_chat(task_input, session_dir)
        except BaseException as exc:  # noqa: BLE001
            status = self.classify_error(stderr=None, exit_code=None, exc=exc)
            err = redact(str(exc) or exc.__class__.__name__)
            logger.warning(
                "GlmAdapter.run raised — classified as %s: %s",
                status.value, err,
            )
            return ExecutionResult(
                status=status,
                platform=self.name,
                response_text="",
                error_message=err,
                stderr_summary=truncate(err),
                platform_attempted=[self.name],
                attempt_count=1,
                run_id=run_id,
            )
        return map_chat_cli_result_to_execution_result(
            cli_result=cli_result, platform=self.name, run_id=run_id,
        )


__all__ = ["GlmAdapter"]
