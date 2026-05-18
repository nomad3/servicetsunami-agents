"""Tests for the GLM (Zhipu AI) HTTP-direct executor — Wave 2b.

The executor in ``cli_executors/glm.py`` does NOT spawn a subprocess.
It POSTs to Zhipu's OpenAI-compatible BigModel ``/chat/completions``
endpoint and streams the SSE response. These tests stub out
``httpx.Client`` so no real network call ever fires.

Coverage mirrors ``test_kimi_executor.py``:

  * Happy path: SSE stream parsed, content concatenated, usage block
    surfaced into ``metadata``.
  * Tenant overrides for ``base_url`` and ``model``.
  * Missing key → friendly not-connected error.
  * Env-var fallback when vault has no api_key.
  * HTTP 4xx (auth/quota) → ``success=False`` with body snippet.
  * HTTP 5xx (server) → ``success=False`` with body snippet.
  * Network error (``httpx.HTTPError``) → ``success=False`` with reason.
  * SSE parser: ``[DONE]`` terminates; malformed lines skipped.
  * Token usage extraction from final stream chunk.
  * workflows.execute_chat_cli dispatch arm routes platform="glm" here.
"""
from __future__ import annotations

import json
from typing import Iterable, List, Optional

import httpx
import pytest

import workflows as wf
from cli_executors import glm as glm_module


# --------------------------------------------------------------------------- helpers


def _make_input(**overrides) -> wf.ChatCliInput:
    base = dict(
        platform="glm",
        message="hello glm",
        tenant_id="tenant-aaa",
        instruction_md_content="",
        mcp_config="",
        image_b64="",
        image_mime="",
        session_id="",
        model="",
        allowed_tools="",
    )
    base.update(overrides)
    return wf.ChatCliInput(**base)


def _sse_lines(chunks: Iterable[dict], include_done: bool = True) -> List[bytes]:
    """Encode a list of dicts as OpenAI-style SSE ``data:`` byte lines."""
    out: List[bytes] = []
    for c in chunks:
        out.append(f"data: {json.dumps(c)}".encode("utf-8"))
        out.append(b"")
    if include_done:
        out.append(b"data: [DONE]")
    return out


class _FakeStreamResponse:
    """Stand-in for ``httpx.Response`` returned by ``client.stream``."""

    def __init__(
        self,
        *,
        status_code: int = 200,
        lines: Optional[List[bytes]] = None,
        body: bytes = b"",
    ) -> None:
        self.status_code = status_code
        self._lines = lines or []
        self._body = body

    def iter_lines(self) -> Iterable[bytes]:
        for ln in self._lines:
            yield ln

    def iter_bytes(self) -> Iterable[bytes]:
        if self._body:
            yield self._body

    def __enter__(self) -> "_FakeStreamResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class _FakeHttpxClient:
    """Stand-in for ``httpx.Client``.

    Captures the request kwargs each call so tests can assert headers
    + body shape and returns whatever ``response`` the constructor
    was seeded with — or raises ``raise_exc`` if provided.
    """

    last_instance: Optional["_FakeHttpxClient"] = None

    def __init__(
        self,
        *,
        response: Optional[_FakeStreamResponse] = None,
        raise_exc: Optional[BaseException] = None,
    ) -> None:
        self._response = response
        self._raise = raise_exc
        self.calls: List[dict] = []
        _FakeHttpxClient.last_instance = self

    def __enter__(self) -> "_FakeHttpxClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def stream(self, method, url, *, headers=None, json=None, **kwargs):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers or {},
                "json": json,
                "kwargs": kwargs,
            },
        )
        if self._raise is not None:
            raise self._raise
        assert self._response is not None
        return self._response


def _install_fake_client(
    monkeypatch,
    *,
    response: Optional[_FakeStreamResponse] = None,
    raise_exc: Optional[BaseException] = None,
) -> None:
    def _factory(*args, **kwargs):
        return _FakeHttpxClient(response=response, raise_exc=raise_exc)

    monkeypatch.setattr(glm_module.httpx, "Client", _factory)


# --------------------------------------------------------------------------- fixtures


@pytest.fixture(autouse=True)
def _isolate_glm_env(monkeypatch):
    """Strip any inherited ZHIPU_* env so tests don't pick up a real
    key from the dev shell."""
    for var in ("ZHIPU_API_KEY", "ZHIPU_BASE_URL", "GLM_MODEL"):
        monkeypatch.delenv(var, raising=False)
    yield


@pytest.fixture
def _stub_creds(monkeypatch):
    """Vault returns a usable api_key by default."""

    def _fake(integration_name, tenant_id):
        assert integration_name == "glm"
        return {"api_key": "sk-zhipu-FAKE-TEST-KEY"}

    monkeypatch.setattr(wf, "_fetch_integration_credentials", _fake)
    yield


# --------------------------------------------------------------------------- happy path


class TestHappyPath:
    def test_streams_text_and_extracts_usage(self, monkeypatch, tmp_path, _stub_creds):
        chunks = [
            {"choices": [{"delta": {"content": "hel"}}]},
            {"choices": [{"delta": {"content": "lo "}}]},
            {"choices": [{"delta": {"content": "back"}}]},
            {
                "choices": [{"delta": {}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12},
            },
        ]
        _install_fake_client(
            monkeypatch,
            response=_FakeStreamResponse(status_code=200, lines=_sse_lines(chunks)),
        )

        out = wf._execute_glm_chat(_make_input(), session_dir=str(tmp_path))

        assert out.success is True
        assert out.response_text == "hello back"
        assert out.metadata["platform"] == "glm"
        assert out.metadata["input_tokens"] == 5
        assert out.metadata["output_tokens"] == 7
        assert out.metadata["model"] == "glm-4.6"

    def test_request_shape_carries_bearer_token_and_messages(
        self, monkeypatch, tmp_path, _stub_creds,
    ):
        _install_fake_client(
            monkeypatch,
            response=_FakeStreamResponse(
                status_code=200,
                lines=_sse_lines([{"choices": [{"delta": {"content": "ok"}}]}]),
            ),
        )

        wf._execute_glm_chat(_make_input(), session_dir=str(tmp_path))

        call = _FakeHttpxClient.last_instance.calls[0]
        assert call["method"] == "POST"
        assert call["url"] == "https://open.bigmodel.cn/api/paas/v4/chat/completions"
        assert call["headers"]["Authorization"] == "Bearer sk-zhipu-FAKE-TEST-KEY"
        body = call["json"]
        assert body["model"] == "glm-4.6"
        assert body["stream"] is True
        assert body["messages"] == [{"role": "user", "content": "hello glm"}]

    def test_instruction_md_becomes_system_message(
        self, monkeypatch, tmp_path, _stub_creds,
    ):
        _install_fake_client(
            monkeypatch,
            response=_FakeStreamResponse(
                status_code=200,
                lines=_sse_lines([{"choices": [{"delta": {"content": "ok"}}]}]),
            ),
        )

        wf._execute_glm_chat(
            _make_input(instruction_md_content="You are a helpful GLM."),
            session_dir=str(tmp_path),
        )

        body = _FakeHttpxClient.last_instance.calls[0]["json"]
        assert body["messages"][0] == {
            "role": "system",
            "content": "You are a helpful GLM.",
        }
        assert body["messages"][1] == {"role": "user", "content": "hello glm"}

    def test_per_tenant_base_url_and_model_override(self, monkeypatch, tmp_path):
        def _fake(integration_name, tenant_id):
            return {
                "api_key": "sk-zhipu-OVERRIDE",
                "base_url": "https://gateway.example.com/v4",
                "model": "glm-4-air",
            }

        monkeypatch.setattr(wf, "_fetch_integration_credentials", _fake)
        _install_fake_client(
            monkeypatch,
            response=_FakeStreamResponse(
                status_code=200,
                lines=_sse_lines([{"choices": [{"delta": {"content": "ok"}}]}]),
            ),
        )

        out = wf._execute_glm_chat(_make_input(), session_dir=str(tmp_path))

        call = _FakeHttpxClient.last_instance.calls[0]
        assert call["url"] == "https://gateway.example.com/v4/chat/completions"
        assert call["json"]["model"] == "glm-4-air"
        assert out.success is True
        assert out.metadata["model"] == "glm-4-air"

    def test_chatcliinput_model_overrides_vault(self, monkeypatch, tmp_path, _stub_creds):
        _install_fake_client(
            monkeypatch,
            response=_FakeStreamResponse(
                status_code=200,
                lines=_sse_lines([{"choices": [{"delta": {"content": "ok"}}]}]),
            ),
        )

        wf._execute_glm_chat(
            _make_input(model="glm-4-flash"),
            session_dir=str(tmp_path),
        )

        body = _FakeHttpxClient.last_instance.calls[0]["json"]
        assert body["model"] == "glm-4-flash"


# --------------------------------------------------------------------------- credential resolution


class TestCredentialResolution:
    def test_missing_credentials_returns_friendly_error(self, monkeypatch, tmp_path):
        def _miss(*_a, **_kw):
            raise RuntimeError("integration not connected")

        monkeypatch.setattr(wf, "_fetch_integration_credentials", _miss)
        out = wf._execute_glm_chat(_make_input(), session_dir=str(tmp_path))

        assert out.success is False
        err = (out.error or "").lower()
        assert "not connected" in err
        # Must hit the chain-walker's missing_credential regex:
        # ``please connect your`` anchors the phrase.
        assert "please connect your" in err

    def test_env_var_fallback_when_vault_empty(self, monkeypatch, tmp_path):
        """Tenant without per-vault key still routes when operator wired
        a shared ``ZHIPU_API_KEY`` into the worker env."""
        monkeypatch.setattr(
            wf, "_fetch_integration_credentials", lambda *_a, **_kw: {},
        )
        monkeypatch.setenv("ZHIPU_API_KEY", "sk-shared-operator-key")
        _install_fake_client(
            monkeypatch,
            response=_FakeStreamResponse(
                status_code=200,
                lines=_sse_lines([{"choices": [{"delta": {"content": "ok"}}]}]),
            ),
        )

        out = wf._execute_glm_chat(_make_input(), session_dir=str(tmp_path))
        assert out.success is True
        call = _FakeHttpxClient.last_instance.calls[0]
        assert call["headers"]["Authorization"] == "Bearer sk-shared-operator-key"


# --------------------------------------------------------------------------- failure paths


class TestFailurePaths:
    def test_http_4xx_returns_error_with_body_snippet(
        self, monkeypatch, tmp_path, _stub_creds,
    ):
        _install_fake_client(
            monkeypatch,
            response=_FakeStreamResponse(
                status_code=401,
                body=b'{"error": {"message": "invalid api key", "code": "auth_error"}}',
            ),
        )

        out = wf._execute_glm_chat(_make_input(), session_dir=str(tmp_path))

        assert out.success is False
        assert "HTTP 401" in (out.error or "")
        assert "invalid api key" in (out.error or "")
        assert out.metadata["platform"] == "glm"

    def test_http_5xx_returns_error_with_body_snippet(
        self, monkeypatch, tmp_path, _stub_creds,
    ):
        _install_fake_client(
            monkeypatch,
            response=_FakeStreamResponse(
                status_code=503,
                body=b'{"error": "upstream unavailable"}',
            ),
        )

        out = wf._execute_glm_chat(_make_input(), session_dir=str(tmp_path))

        assert out.success is False
        assert "HTTP 503" in (out.error or "")
        assert "upstream unavailable" in (out.error or "")

    def test_network_error_returns_friendly_message(
        self, monkeypatch, tmp_path, _stub_creds,
    ):
        _install_fake_client(
            monkeypatch,
            raise_exc=httpx.ConnectError("connection refused"),
        )

        out = wf._execute_glm_chat(_make_input(), session_dir=str(tmp_path))

        assert out.success is False
        assert "HTTP error" in (out.error or "")
        assert "connection refused" in (out.error or "")

    def test_empty_stream_returns_no_output_error(
        self, monkeypatch, tmp_path, _stub_creds,
    ):
        _install_fake_client(
            monkeypatch,
            response=_FakeStreamResponse(
                status_code=200,
                lines=_sse_lines([{"choices": [{"delta": {}}]}]),
            ),
        )

        out = wf._execute_glm_chat(_make_input(), session_dir=str(tmp_path))

        assert out.success is False
        assert "no output" in (out.error or "").lower()


# --------------------------------------------------------------------------- SSE parser unit tests


class TestSseParser:
    def test_done_terminates_stream(self):
        lines = [
            b'data: {"choices": [{"delta": {"content": "a"}}]}',
            b"",
            b"data: [DONE]",
            b'data: {"choices": [{"delta": {"content": "should-not-emit"}}]}',
        ]
        out = list(glm_module._parse_sse_stream(lines))
        assert len(out) == 1
        assert out[0]["choices"][0]["delta"]["content"] == "a"

    def test_malformed_lines_skipped(self):
        lines = [
            b": keepalive comment",
            b"event: ping",
            b"data: not-json",
            b'data: {"choices": [{"delta": {"content": "good"}}]}',
        ]
        out = list(glm_module._parse_sse_stream(lines))
        assert len(out) == 1
        assert out[0]["choices"][0]["delta"]["content"] == "good"

    def test_blank_lines_and_non_data_lines_ignored(self):
        lines = [b"", b":", b"id: 123", b'data: {"x": 1}']
        out = list(glm_module._parse_sse_stream(lines))
        assert out == [{"x": 1}]


# --------------------------------------------------------------------------- delta extraction


class TestExtractDelta:
    def test_content_and_reasoning_separated(self):
        content, reasoning, tool_calls, usage = glm_module._extract_delta(
            {
                "choices": [
                    {
                        "delta": {
                            "content": "hello",
                            "reasoning_content": "thinking...",
                        },
                    },
                ],
            },
        )
        assert content == "hello"
        assert reasoning == "thinking..."
        assert tool_calls is None
        assert usage is None

    def test_tool_calls_passthrough(self):
        chunk = {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "function": {"name": "search", "arguments": '{"q":'}},
                        ],
                    },
                },
            ],
        }
        content, reasoning, tool_calls, usage = glm_module._extract_delta(chunk)
        assert content == ""
        assert tool_calls is not None
        assert tool_calls[0]["function"]["name"] == "search"

    def test_usage_lifted_from_final_chunk(self):
        chunk = {
            "choices": [{"delta": {}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 4},
        }
        _, _, _, usage = glm_module._extract_delta(chunk)
        assert usage == {"prompt_tokens": 10, "completion_tokens": 4}


# --------------------------------------------------------------------------- workflows.py dispatch integration


class TestDispatchIntegration:
    """Confirm execute_chat_cli routes ``platform="glm"`` to the GLM
    executor — the explicit dispatch arm wired in workflows.py."""

    @pytest.fixture(autouse=True)
    def _isolate_session_dir(self, monkeypatch, tmp_path):
        sessions_root = tmp_path / "st_sessions"
        sessions_root.mkdir()
        import os as _os
        original = _os.path.join

        def patched(*parts):
            if parts and isinstance(parts[0], str) and parts[0].startswith(
                "/home/codeworker/st_sessions"
            ):
                return original(str(sessions_root), *parts[1:])
            return original(*parts)

        monkeypatch.setattr(wf.os.path, "join", patched)
        monkeypatch.setattr(wf, "_fetch_github_token", lambda tid: None)
        monkeypatch.setattr(wf.subprocess, "run", lambda *a, **kw: None)
        yield

    def test_dispatcher_routes_glm_platform_to_glm_executor(self, monkeypatch):
        sentinel = wf.ChatCliResult(response_text="OK-glm", success=True)
        calls: list = []

        def fake_glm(*args, **kwargs):
            calls.append(args)
            return sentinel

        monkeypatch.setattr(wf, "_execute_glm_chat", fake_glm)
        out = wf.execute_chat_cli(_make_input(platform="glm"))
        assert out is sentinel
        assert len(calls) == 1
