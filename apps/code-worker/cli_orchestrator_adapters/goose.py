"""GooseAdapter — wraps cli_executors.goose.execute_goose_chat.

Phase 2 worker-side ProviderAdapter for the ``goose`` platform (Wave
2d). Goose is Block's Apache-2.0 Rust CLI installed from a GitHub
release into the code-worker image (see ``apps/code-worker/Dockerfile``).

Preflight composition:

  1. Binary on $PATH — ``goose`` must be installed in the image.
  2. Credentials present — the tenant has a Goose integration row
     (with a chosen provider). Missing creds returns NEEDS_AUTH so
     the chain skips past goose without a cooldown.
  3. Cloud reachability — intentionally NOT probed. Goose talks to
     whichever underlying provider the tenant picked, and probing each
     provider would multiply preflight latency. The provider's
     billing/auth errors surface in stderr at runtime and the canonical
     classifier maps them into the right Status.
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
from cli_orchestrator.preflight import check_binary_on_path
from cli_orchestrator.redaction import redact
from cli_orchestrator.status import Status

from cli_executors.goose import execute_goose_chat

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


class GooseAdapter:
    name = "goose"

    def preflight(self, req: ExecutionRequest) -> PreflightResult:
        # 1. Binary on $PATH — the image installs the Rust binary at
        # build time (see Dockerfile).
        with time_preflight_helper(self.name, "binary_on_path"):
            br = check_binary_on_path("goose")
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

        # 3. Cloud reachability — see module docstring; provider auth
        # errors surface via the runtime classifier.

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
            cli_result = execute_goose_chat(task_input, session_dir)
        except BaseException as exc:  # noqa: BLE001
            status = self.classify_error(stderr=None, exit_code=None, exc=exc)
            err = redact(str(exc) or exc.__class__.__name__)
            logger.warning(
                "GooseAdapter.run raised — classified as %s: %s",
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


__all__ = ["GooseAdapter"]
