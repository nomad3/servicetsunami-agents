import asyncio
import hashlib
import hmac
import json
import logging
from typing import Any, Optional

import httpx
from sqlalchemy.orm import Session

from app.models.external_agent import ExternalAgent
from app.models.integration_credential import IntegrationCredential
from app.services.orchestration.credential_vault import retrieve_credential

logger = logging.getLogger(__name__)

# Default budget for an MCP-SSE handshake + single tool call.
_MCP_SSE_DEFAULT_TIMEOUT = 30


class ExternalAgentAdapter:
    def dispatch(self, agent: ExternalAgent, task: str, context: dict, db: Session) -> str:
        """Route task to external agent based on protocol."""
        if agent.protocol == "openai_chat":
            return self._dispatch_openai_chat(agent, task, context, db)
        elif agent.protocol == "mcp_sse":
            return self._dispatch_mcp_sse(agent, task, context, db)
        elif agent.protocol == "webhook":
            return self._dispatch_webhook(agent, task, context, db)
        elif agent.protocol == "a2a":
            return "A2A dispatch not yet implemented for external agent adapter"
        elif agent.protocol == "copilot_extension":
            return "Copilot Extension dispatch not yet implemented"
        else:
            raise RuntimeError(f"Unknown protocol: {agent.protocol}")

    def _dispatch_openai_chat(self, agent: ExternalAgent, task: str, context: dict, db: Session) -> str:
        messages = []
        if context:
            messages.append({"role": "system", "content": str(context)})
        messages.append({"role": "user", "content": task})

        token = self._get_credential(agent, db)
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        body = {
            "model": agent.metadata_.get("model", "gpt-4"),
            "messages": messages,
        }

        try:
            resp = httpx.post(
                f"{agent.endpoint_url.rstrip('/')}/v1/chat/completions",
                json=body,
                headers=headers,
                timeout=agent.metadata_.get("timeout", 30),
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except httpx.HTTPStatusError as e:
            raise RuntimeError(f"openai_chat request failed with status {e.response.status_code}") from e
        except Exception as e:
            raise RuntimeError(f"openai_chat request failed: {e}") from e

    def _dispatch_webhook(self, agent: ExternalAgent, task: str, context: dict, db: Session) -> str:
        payload = {"task": task, "context": context, "callback_url": None}
        body_json = json.dumps(payload)

        headers = {"Content-Type": "application/json"}
        if agent.auth_type == "hmac":
            secret = self._get_credential(agent, db)
            sig = hmac.new(secret.encode(), body_json.encode(), hashlib.sha256).hexdigest()
            headers["X-Signature"] = f"hmac-sha256={sig}"
        else:
            token = self._get_credential(agent, db)
            headers["Authorization"] = f"Bearer {token}"

        try:
            resp = httpx.post(
                f"{agent.endpoint_url.rstrip('/')}/tasks",
                content=body_json,
                headers=headers,
                timeout=agent.metadata_.get("timeout", 30),
            )
            if resp.status_code == 200:
                return str(resp.json())
            raise RuntimeError(f"webhook request failed with status {resp.status_code}")
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"webhook request failed: {e}") from e

    def _dispatch_mcp_sse(self, agent: ExternalAgent, task: str, context: dict, db: Session) -> str:
        """Dispatch a task to a remote MCP server over SSE.

        Most Claude Code / Gemini / Cursor "skills" are exposed as MCP-SSE
        servers. We open one short-lived connection per dispatch:
            connect → initialize → list_tools (cached) → call_tool → close.

        Tool selection precedence:
          1. ``context["tool_name"]`` (caller-supplied — e.g. from Dynamic
             Workflow step config).
          2. ``agent.metadata_["tool_name"]`` (set during Hire so the
             external agent has a default action).
          3. First tool returned by ``list_tools`` — only used when the
             remote server exposes a single primary tool. Multi-tool servers
             with no explicit selection raise.

        Tool arguments precedence:
          1. ``context["arguments"]`` if it's a dict — full structured args.
          2. ``{"input": task}`` fallback — most single-tool agents accept
             a free-text input field.

        The adapter is sync (chat path) but the official MCP SDK is async,
        so we drive it via ``asyncio.run`` per dispatch. Acceptable here:
        external dispatch is low-frequency and we want a fresh connection
        per call to keep the breaker semantics in PR-C clean.
        """
        token = self._get_credential(agent, db)
        timeout_s = int(agent.metadata_.get("timeout", _MCP_SSE_DEFAULT_TIMEOUT) or _MCP_SSE_DEFAULT_TIMEOUT)
        tool_name = (
            (context.get("tool_name") if isinstance(context, dict) else None)
            or agent.metadata_.get("tool_name")
        )
        arguments = (context.get("arguments") if isinstance(context, dict) else None)
        if not isinstance(arguments, dict):
            arguments = {"input": task}

        # Only Bearer-style auth is supported for MCP-SSE today; other
        # auth_types (api_key / hmac / github_app) would need request-level
        # signing the SDK doesn't expose. Skip the Authorization header
        # rather than send the wrong one — the remote will surface the
        # auth failure clearly.
        bearer = token if (token and getattr(agent, "auth_type", "bearer") == "bearer") else ""

        try:
            return _run_async(
                self._mcp_sse_call(
                    endpoint=agent.endpoint_url,
                    bearer=bearer,
                    tool_name=tool_name,
                    arguments=arguments,
                    timeout_s=timeout_s,
                )
            )
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"mcp_sse request failed: {e}") from e

    @staticmethod
    async def _mcp_sse_call(
        *,
        endpoint: str,
        bearer: str,
        tool_name: Optional[str],
        arguments: dict,
        timeout_s: int,
    ) -> str:
        # Imported lazily so api startup doesn't pay the cost when no
        # external MCP agent is registered.
        from mcp import ClientSession
        from mcp.client.sse import sse_client

        headers: dict[str, str] = {}
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"

        # The mcp SDK's sse_client takes the *messages* endpoint URL —
        # remote servers usually expose it at <base>/sse. Honor whatever
        # the user registered; only append /sse if the URL has no path.
        url = endpoint.rstrip("/")
        if not url.endswith("/sse"):
            # Heuristic: if the URL has no path beyond the host, the SSE
            # endpoint is conventionally at /sse. Otherwise trust the user.
            from urllib.parse import urlparse
            if not (urlparse(url).path or "").strip("/"):
                url = url + "/sse"

        async def _do() -> str:
            async with sse_client(url, headers=headers) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()

                    resolved_tool = tool_name
                    if not resolved_tool:
                        listing = await session.list_tools()
                        names = [t.name for t in (listing.tools or [])]
                        if not names:
                            raise RuntimeError("Remote MCP server exposes no tools.")
                        if len(names) > 1:
                            raise RuntimeError(
                                "Remote MCP server exposes multiple tools "
                                f"({names!r}); set agent.metadata_['tool_name'] "
                                "or pass context['tool_name']."
                            )
                        resolved_tool = names[0]

                    result = await session.call_tool(resolved_tool, arguments=arguments)
                    return _stringify_mcp_result(result)

        return await asyncio.wait_for(_do(), timeout=timeout_s)

    def _get_credential(self, agent: ExternalAgent, db: Session) -> str:
        if agent.credential_id is None:
            return ""
        try:
            plaintext = retrieve_credential(db, agent.credential_id, agent.tenant_id)
            return plaintext or ""
        except Exception as e:
            logger.warning("Could not load credential %s for agent %s: %s", agent.credential_id, agent.id, e)
            return ""


def _run_async(coro):
    """Drive an async coroutine from a sync caller, even when an event
    loop is already running (e.g. an async FastAPI handler calling the
    sync adapter). ``asyncio.run`` raises ``RuntimeError`` in that case;
    we detect the active loop and run the coroutine in a thread instead.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No running loop — the common case from sync chat / Temporal
        # activities. Use the simple path.
        return asyncio.run(coro)

    # We're inside a running event loop. Push the coroutine onto a fresh
    # loop in a worker thread so we don't deadlock.
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _stringify_mcp_result(result: Any) -> str:
    """Flatten an MCP CallToolResult into a single string for the chat path.

    The SDK returns ``content`` as a list of typed blocks (TextContent,
    ImageContent, EmbeddedResource…). Adapter callers expect a string,
    matching the openai_chat / webhook contract. We concatenate text
    blocks; non-text blocks fall back to repr so nothing silently drops.
    """
    if result is None:
        return ""
    if getattr(result, "isError", False):
        # Surface the remote error in the same shape the adapter raises for
        # other protocols so the caller's try/except path is uniform.
        msg = getattr(result, "content", None) or "remote MCP tool returned an error"
        raise RuntimeError(f"mcp_sse tool returned error: {_join_content(msg)}")
    return _join_content(getattr(result, "content", None))


def _join_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)
    parts: list[str] = []
    for block in content:
        # TextContent has .text; other block types don't — fall back to repr.
        text = getattr(block, "text", None)
        parts.append(text if isinstance(text, str) else repr(block))
    return "\n".join(parts)


adapter = ExternalAgentAdapter()
