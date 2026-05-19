from __future__ import annotations

import uuid
from functools import lru_cache
from typing import Any, Dict, Optional

from temporalio.client import Client, WorkflowHandle
from temporalio.service import RPCError

from app.core.config import settings


class TemporalNotConfiguredError(RuntimeError):
    """Raised when Temporal connectivity is not configured."""


def _temporal_endpoint_key() -> tuple[Optional[str], str]:
    return settings.TEMPORAL_ADDRESS, settings.TEMPORAL_NAMESPACE


@lru_cache
def _temporal_endpoint_cached() -> tuple[Optional[str], str]:
    return _temporal_endpoint_key()


async def _get_temporal_client() -> Client:
    address, namespace = _temporal_endpoint_cached()
    if not address:
        raise TemporalNotConfiguredError("Temporal address is not configured.")
    try:
        return await Client.connect(address, namespace=namespace)
    except RPCError as exc:  # pragma: no cover - network failure path
        raise RuntimeError(f"Unable to connect to Temporal at {address}: {exc}") from exc


async def start_workflow(
    *,
    workflow_type: str,
    tenant_id: uuid.UUID,
    task_queue: str,
    arguments: Dict[str, Any] | None = None,
    workflow_id: str | None = None,
    memo: Dict[str, Any] | None = None,
) -> WorkflowHandle:
    client = await _get_temporal_client()
    workflow_arguments = arguments or {}
    resolved_workflow_id = workflow_id or f"{tenant_id}-{uuid.uuid4()}"
    # Temporal stores memo as a dict of serializable values; always attach tenant_id
    memo_payload: Dict[str, Any] = {**(memo or {}), "tenant_id": str(tenant_id)}
    handle = await client.start_workflow(
        workflow_type,
        workflow_arguments,
        id=resolved_workflow_id,
        task_queue=task_queue,
        memo=memo_payload,
    )
    return handle


async def describe_workflow(*, workflow_id: str, run_id: str | None = None) -> Dict[str, Any]:
    client = await _get_temporal_client()
    handle = client.get_workflow_handle(workflow_id=workflow_id, run_id=run_id)
    description = await handle.describe()

    # temporalio>=1.10 dropped the `.workflow_execution_info` wrapper —
    # the fields live directly on WorkflowExecutionDescription now.
    # WorkflowType also flattens to a bare string `description.workflow_type`
    # instead of `.type.name`. Surfaced when alpha run --fanout started
    # actually dispatching real Temporal workflows in PR #573.
    return {
        "workflow_id": description.id,
        "run_id": description.run_id,
        "type": description.workflow_type,
        "status": description.status.name if description.status else None,
        "start_time": description.start_time.isoformat() if description.start_time else None,
        "close_time": description.close_time.isoformat() if description.close_time else None,
        "history_length": description.history_length,
        "memo": description.memo or {},
    }


async def fetch_workflow_result(workflow_id: str, *, run_id: str | None = None) -> Any:
    """Round-1 review H1 follow-up: public helper for retrieving a
    completed workflow's return value. Centralizes the
    `_get_temporal_client` access so route handlers don't reach into
    private SDK lifecycle. Raises on non-terminal workflows; callers
    should `describe_workflow` first."""
    client = await _get_temporal_client()
    handle = client.get_workflow_handle(workflow_id=workflow_id, run_id=run_id)
    return await handle.result()


async def cancel_workflow(workflow_id: str, *, run_id: str | None = None) -> None:
    """Round-1 review H1 follow-up: public helper for cancellation.
    Same centralization rationale as `fetch_workflow_result`."""
    client = await _get_temporal_client()
    handle = client.get_workflow_handle(workflow_id=workflow_id, run_id=run_id)
    await handle.cancel()
