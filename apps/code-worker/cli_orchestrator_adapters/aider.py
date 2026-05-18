"""AiderAdapter — wraps cli_executors.aider.execute_aider_chat.

Phase 2 worker-side ProviderAdapter for the ``aider`` platform (Wave
2c). Aider is a Python CLI binary (``pip install aider-chat``) so
preflight does the standard two-step:

  1. ``aider`` binary on ``$PATH``.
  2. Credentials present in the tenant vault (the integration card
     stores a ``model`` slug + the matching provider API key).

Unlike Kimi/Qwen there is no cloud-API reachability probe at
preflight — Aider can target ~30 different LiteLLM-supported endpoints
depending on the model slug, and probing them all is silly. Any
upstream auth/billing error surfaces as a non-zero exit at run time
and is mapped via ``classify_error``.
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
from cli_orchestrator.preflight import check_binary_on_path
from cli_orchestrator.redaction import redact
from cli_orchestrator.status import Status

from cli_executors.aider import execute_aider_chat

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


class AiderAdapter:
    name = "aider"

    def preflight(self, req: ExecutionRequest) -> PreflightResult:
        # 1. Binary on $PATH
        with time_preflight_helper(self.name, "binary_on_path"):
            br = check_binary_on_path("aider")
        if not br.ok:
            return br

        # 2. Credentials present in the tenant vault. The shared operator
        # env-var path (``AIDER_MODEL_API_KEY`` or any of the known
        # provider keys) is an acceptable fallback — if either resolves
        # at run time the executor proceeds.
        tenant_id = req.tenant_id or (req.payload or {}).get("tenant_id") or ""
        if tenant_id:
            with time_preflight_helper(self.name, "credentials_present"):
                cr = check_credential_for_platform(
                    PreflightDeps.get(), tenant_id, self.name,
                )
            if not cr.ok:
                # Vault miss — accept ONLY if the operator wired a
                # shared key into the worker container env. We check
                # the generic alias plus the common provider envs to
                # match the executor's fallback chain.
                if any(os.environ.get(k) for k in (
                    "AIDER_MODEL_API_KEY",
                    "ANTHROPIC_API_KEY",
                    "OPENAI_API_KEY",
                    "DEEPSEEK_API_KEY",
                    "GEMINI_API_KEY",
                )):
                    return PreflightResult.succeed()
                return cr

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
            cli_result = execute_aider_chat(task_input, session_dir)
        except BaseException as exc:  # noqa: BLE001
            status = self.classify_error(stderr=None, exit_code=None, exc=exc)
            err = redact(str(exc) or exc.__class__.__name__)
            logger.warning(
                "AiderAdapter.run raised — classified as %s: %s",
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


__all__ = ["AiderAdapter"]
