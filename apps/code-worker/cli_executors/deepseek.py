"""DeepSeek chat executor — Wave 2a, HTTP-direct.

DeepSeek (深度求索) ships DeepSeek-V3 (general coding-tuned) and
DeepSeek-R1 (reasoning-trace) models under the MIT license (Lane B per
``docs/plans/2026-05-18-cli-integration-catalog.md`` — commercial
resale permitted). The hosted wire surface is OpenAI-compatible:

  * Base URL: ``https://api.deepseek.com/v1``.
  * Auth: ``Authorization: Bearer <DEEPSEEK_API_KEY>``.
  * Default model: ``deepseek-chat`` (the V3.5 coding-tuned variant).
    The reasoning variant ``deepseek-reasoner`` (R1) additionally
    streams a ``reasoning_content`` field on each delta — we route
    those into the ``reasoning`` chunk_kind so the Den's thinking pane
    surfaces them separately from the user-visible answer.

There is no local DeepSeek CLI binary. Like the Kimi executor (Wave
1c), we talk to the OpenAI-compatible HTTP endpoint directly via
httpx — already a code-worker dependency — so the image stays small
and the wire shape stays stable.

The executor uses the OpenAI Chat Completions streaming protocol
(``stream=true``), parses Server-Sent Events (``data: {...}`` lines),
extracts ``choices[0].delta.content`` (+ ``reasoning_content``)
chunks, and feeds them into the existing ``SessionEventEmitter`` so
the terminal/Den surfaces see live tokens just like the other CLIs.

Credentials flow mirrors the rest of the suite:

  * Vault lookup via ``_fetch_integration_credentials("deepseek", ...)``;
    the integration card stores ``api_key`` (+ optional ``base_url`` /
    ``model`` overrides).
  * Env-var fallback (``DEEPSEEK_API_KEY``) so an operator-shared key
    works without per-tenant wiring.
  * Tenant HOME redirected onto the workspaces volume — kept for
    parity even though we no longer spawn a subprocess; future tool
    handlers may write into HOME and we want them rooted on the
    tenant-scoped volume.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Iterable, List, Optional, Tuple

import httpx

import cli_runtime
from session_event_emitter import SessionEventEmitter

logger = logging.getLogger(__name__)


# Default model + base URL. Both are overridable per-tenant by storing
# a ``model`` or ``base_url`` credential in the integration vault, or
# per container by setting ``DEEPSEEK_MODEL`` / ``DEEPSEEK_BASE_URL``
# env vars. ``deepseek-chat`` is V3.5 (coding-tuned). For reasoning
# traces a tenant can pin ``deepseek-reasoner`` (R1) via the model
# field on either the integration card or the ChatCliInput.
_DEFAULT_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
_DEFAULT_BASE_URL = os.environ.get(
    "DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1",
)

# Overall request budget. Long DeepSeek-R1 reasoning turns can take
# 60-120s; cap at 20 minutes to match the subprocess-era 1500s timeout
# the other CLIs use.
_REQUEST_TIMEOUT_SECS = 1200.0


def _parse_sse_stream(
    lines: Iterable[bytes],
) -> Iterable[Dict[str, Any]]:
    """Yield decoded JSON payloads from an OpenAI-style SSE stream.

    OpenAI/DeepSeek stream chunks look like::

        data: {"id":"...","choices":[{"delta":{"content":"hel"}}]}
        data: {"id":"...","choices":[{"delta":{"content":"lo"}}]}
        data: [DONE]

    Blank lines separate events; ``[DONE]`` terminates the stream.
    Anything that isn't a ``data:`` line is ignored (comments, retries,
    keepalives).
    """
    for raw in lines:
        if not raw:
            continue
        if isinstance(raw, bytes):
            try:
                line = raw.decode("utf-8", errors="replace")
            except Exception:
                continue
        else:
            line = raw
        line = line.strip()
        if not line or not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if payload == "[DONE]":
            return
        try:
            yield json.loads(payload)
        except json.JSONDecodeError:
            logger.debug("DeepSeek SSE: undecodable payload %r", payload[:120])
            continue


def _extract_delta(
    chunk: Dict[str, Any],
) -> Tuple[str, str, Optional[List[Dict[str, Any]]], Optional[Dict[str, Any]]]:
    """Pull (content, reasoning, tool_calls, usage) from one SSE chunk.

    DeepSeek keeps strict OpenAI compatibility with one addition for
    the R1 reasoner — ``delta.reasoning_content`` carries the model's
    chain-of-thought tokens separately from the user-visible answer.
    Shape:

      choices[0].delta = {
          "content": "...",            # streamed assistant tokens
          "reasoning_content": "...",  # R1 thinking stream
          "tool_calls": [...],         # streamed function call args
      }
      usage = {prompt_tokens, completion_tokens, total_tokens}
                                       # only on the final chunk
    """
    content = ""
    reasoning = ""
    tool_calls: Optional[List[Dict[str, Any]]] = None
    choices = chunk.get("choices") or []
    if choices:
        delta = (choices[0] or {}).get("delta") or {}
        content = delta.get("content") or ""
        reasoning = delta.get("reasoning_content") or ""
        if delta.get("tool_calls"):
            tool_calls = delta["tool_calls"]
    usage = chunk.get("usage")
    return content, reasoning, tool_calls, usage


def execute_deepseek_chat(task_input, session_dir: str):
    from workflows import (
        _fetch_integration_credentials,
        _INTEGRATION_NOT_CONNECTED_MESSAGES,
        ChatCliResult,
    )

    # ── credential resolution ─────────────────────────────────────────
    # Order: tenant vault wins; env var is a fall-back so an operator
    # can wire a shared DEEPSEEK_API_KEY into the container without
    # touching every tenant.
    api_key = ""
    base_url = _DEFAULT_BASE_URL
    model = _DEFAULT_MODEL
    try:
        creds = _fetch_integration_credentials("deepseek", task_input.tenant_id)
        api_key = creds.get("api_key", "") or ""
        base_url = creds.get("base_url", "") or base_url
        model = creds.get("model", "") or model
    except Exception as exc:
        logger.info("DeepSeek vault lookup failed (%s); falling back to env", exc)

    if not api_key:
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")

    if not api_key:
        return ChatCliResult(
            response_text="",
            success=False,
            error=_INTEGRATION_NOT_CONNECTED_MESSAGES.get(
                "deepseek",
                "DeepSeek is not connected. Please connect your DeepSeek "
                "account in Settings → Integrations.",
            ),
        )

    # Allow per-tenant override of the model via ChatCliInput.model —
    # same convention the other executors use when an agent pins a
    # specific variant (e.g. deepseek-reasoner for R1 traces).
    requested_model = getattr(task_input, "model", "") or ""
    if requested_model:
        model = requested_model

    # ── tenant HOME on workspaces volume (kept for parity with the
    # subprocess executors — no fork here, but future tool handlers may
    # write transient files and should land on the tenant volume).
    try:
        tenant_home = str(cli_runtime.tenant_home_dir(task_input.tenant_id))
    except (ValueError, OSError) as exc:
        logger.warning(
            "tenant_home_dir(%s) failed (%s); HOME falls back to session_dir=%s",
            task_input.tenant_id, exc, session_dir,
        )
        tenant_home = session_dir
    os.makedirs(tenant_home, exist_ok=True)

    # ── compose the request body ──────────────────────────────────────
    system_prompt = (task_input.instruction_md_content or "").strip()
    user_message = task_input.message or ""

    messages: List[Dict[str, Any]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_message})

    body: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
    }

    # Tool/function specs — defensive: the field is not currently on
    # ChatCliInput, but the orchestrator layer may attach it at dispatch
    # time. Accept either an already-list-of-dicts or a JSON-encoded
    # string.
    tool_specs = getattr(task_input, "tool_specs", None)
    if isinstance(tool_specs, str) and tool_specs.strip():
        try:
            tool_specs = json.loads(tool_specs)
        except json.JSONDecodeError:
            logger.warning("DeepSeek: ignoring malformed tool_specs JSON")
            tool_specs = None
    if isinstance(tool_specs, list) and tool_specs:
        body["tools"] = tool_specs

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }

    # ── streaming emitter ────────────────────────────────────────────
    emitter = SessionEventEmitter(
        chat_session_id=getattr(task_input, "chat_session_id", "") or "",
        tenant_id=task_input.tenant_id,
        platform="deepseek",
        attempt=getattr(task_input, "attempt", 1) or 1,
    )

    if emitter.enabled:
        emitter.emit_chunk(
            "lifecycle",
            f"→ DeepSeek HTTP request: {model}\n",
            raw={"event": "request_started", "platform": "deepseek", "model": model},
        )

    completion_url = f"{base_url.rstrip('/')}/chat/completions"

    full_text_parts: List[str] = []
    usage_block: Optional[Dict[str, Any]] = None
    pending_tool_calls: List[Dict[str, Any]] = []

    try:
        with httpx.Client(timeout=_REQUEST_TIMEOUT_SECS) as client:
            with client.stream(
                "POST", completion_url, headers=headers, json=body,
            ) as resp:
                if resp.status_code >= 400:
                    err_body = b"".join(resp.iter_bytes()).decode(
                        "utf-8", errors="replace",
                    )
                    snippet = err_body[:2000]
                    logger.warning(
                        "DeepSeek HTTP %s on %s: %s",
                        resp.status_code, completion_url, snippet[:500],
                    )
                    emitter.emit_chunk(
                        "lifecycle_error",
                        f"✗ DeepSeek HTTP {resp.status_code}\n",
                        raw={"event": "http_error", "status": resp.status_code},
                    )
                    return ChatCliResult(
                        response_text="",
                        success=False,
                        error=f"DeepSeek HTTP {resp.status_code}: {snippet}",
                        metadata={"platform": "deepseek", "model": model},
                    )

                for chunk in _parse_sse_stream(resp.iter_lines()):
                    content, reasoning, tool_calls, usage = _extract_delta(chunk)
                    if reasoning:
                        emitter.emit_chunk("reasoning", reasoning)
                    if content:
                        full_text_parts.append(content)
                        emitter.emit_chunk("text", content)
                    if tool_calls:
                        for tc in tool_calls:
                            name = ((tc or {}).get("function") or {}).get("name") or ""
                            args = ((tc or {}).get("function") or {}).get("arguments") or ""
                            label = name or f"call#{(tc or {}).get('index', '?')}"
                            emitter.emit_chunk(
                                "tool_use",
                                f"→ Tool({label}) {args[:240]}\n",
                                raw={"tool_call_delta": tc},
                            )
                            pending_tool_calls.append(tc)
                    if usage:
                        usage_block = usage
    except httpx.HTTPError as exc:
        msg = f"DeepSeek HTTP error: {exc}"
        logger.warning(msg)
        if emitter.enabled:
            emitter.emit_chunk(
                "lifecycle_error",
                f"✗ {msg}\n",
                raw={"event": "http_error", "kind": exc.__class__.__name__},
            )
        emitter.close()
        return ChatCliResult(
            response_text="",
            success=False,
            error=msg,
            metadata={"platform": "deepseek", "model": model},
        )
    except Exception as exc:  # noqa: BLE001 — final defensive net
        msg = f"DeepSeek unexpected error: {exc}"
        logger.exception(msg)
        if emitter.enabled:
            emitter.emit_chunk(
                "lifecycle_error",
                f"✗ {msg}\n",
                raw={"event": "unexpected_error", "kind": exc.__class__.__name__},
            )
        emitter.close()
        return ChatCliResult(
            response_text="",
            success=False,
            error=msg,
            metadata={"platform": "deepseek", "model": model},
        )

    full_text = "".join(full_text_parts).strip()

    metadata: Dict[str, Any] = {
        "platform": "deepseek",
        "model": model,
    }
    if usage_block:
        metadata["input_tokens"] = (
            usage_block.get("prompt_tokens")
            or usage_block.get("input_tokens")
            or 0
        )
        metadata["output_tokens"] = (
            usage_block.get("completion_tokens")
            or usage_block.get("output_tokens")
            or 0
        )
    if pending_tool_calls:
        metadata["tool_calls"] = pending_tool_calls

    if emitter.enabled:
        emitter.emit_chunk(
            "lifecycle",
            "✓ DeepSeek request complete\n",
            raw={
                "event": "request_finished",
                "platform": "deepseek",
                "model": model,
                "usage": usage_block or {},
            },
        )
    emitter.close()

    if not full_text and not pending_tool_calls:
        return ChatCliResult(
            response_text="",
            success=False,
            error="DeepSeek produced no output",
            metadata=metadata,
        )

    return ChatCliResult(
        response_text=full_text,
        success=True,
        metadata=metadata,
    )
