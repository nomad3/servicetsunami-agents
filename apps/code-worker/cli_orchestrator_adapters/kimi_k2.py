"""KimiK2Adapter — wraps cli_executors.kimi.execute_kimi_chat.

Phase 2 worker-side ProviderAdapter for the ``kimi_k2`` platform (Wave
1c). The binary on $PATH is ``kimi`` (the ``@moonshotai/kimi-cli`` npm
package installs the binary named ``kimi``). The executor falls back
to ``npx @moonshotai/kimi-cli`` when the global binary isn't on PATH;
preflight here only checks for the globally-installed binary, since
the npx fallback is a local-dev convenience rather than a production
runtime path — production images install the CLI globally in the
worker Dockerfile.

Phase 2 doesn't use this adapter at runtime yet (Phase 3's
ResilientExecutor flip is what actually consults the adapter map), but
registering it now ensures the Kimi platform shows up in the chain at
flip-time instead of silently dropping out.
"""
from __future__ import annotations

import logging
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

from cli_executors.kimi import execute_kimi_chat

from cli_orchestrator.preflight import (
    check_binary_on_path,
)

from ._common import (
    binary_on_path,
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


class KimiK2Adapter:
    name = "kimi_k2"

    def preflight(self, req: ExecutionRequest) -> PreflightResult:
        # 1. Binary on $PATH
        with time_preflight_helper(self.name, "binary_on_path"):
            br = check_binary_on_path("kimi")
        if not br.ok:
            return br

        # 2. Credentials present
        tenant_id = req.tenant_id or (req.payload or {}).get("tenant_id") or ""
        if tenant_id:
            with time_preflight_helper(self.name, "credentials_present"):
                cr = check_credential_for_platform(
                    PreflightDeps.get(), tenant_id, self.name,
                )
            if not cr.ok:
                return cr

        # 3. Cloud API enabled — not probed at preflight. Moonshot's API
        # returns billing/quota errors at request time; the classifier
        # surfaces them from subprocess stderr on the runtime path.

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
            cli_result = execute_kimi_chat(task_input, session_dir)
        except BaseException as exc:  # noqa: BLE001
            status = self.classify_error(stderr=None, exit_code=None, exc=exc)
            err = redact(str(exc) or exc.__class__.__name__)
            logger.warning(
                "KimiK2Adapter.run raised — classified as %s: %s",
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


__all__ = ["KimiK2Adapter"]
