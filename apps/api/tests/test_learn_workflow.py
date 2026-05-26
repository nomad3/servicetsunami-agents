"""T3.2a — happy-path test for LearnFromMediaWorkflow.

Per plan §T3.2 (NEW-IMPORTANT-2): Temporal's ``Worker`` captures activity
function references at construction time, so monkeypatching ``A.act_X``
on the module would NOT affect the in-flight worker. Instead we patch the
``_call_mcp`` HTTP boundary — every real activity calls into ``_wrap``
which calls ``_call_mcp`` — so the real activity bodies + envelope
decoders run, and only the HTTP layer is stubbed.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from app.workflows.activities import learn_from_media_activities as A
from app.workflows.learn_from_media_workflow import LearnFromMediaWorkflow


@pytest.fixture
async def env():
    async with await WorkflowEnvironment.start_time_skipping() as e:
        yield e


@pytest.fixture
async def worker(env):
    async with Worker(
        env.client,
        task_queue="learn-test",
        workflows=[LearnFromMediaWorkflow],
        activities=[
            A.act_extract_media,
            A.act_transcribe_url,
            A.act_synthesize_skill_draft,
            A.act_dispatch_skill_review,
            A.act_run_synthetic_test,
            A.act_install_skill,
            A.act_diffuse_learning,
            A.act_write_cache,
            A.act_write_quarantine,
            A.act_notify_session,
            A.act_probe_attachment,
        ],
    ) as w:
        yield w


def _mock_mcp_responses(monkeypatch, responses: dict[str, dict]):
    """Replace ``A._call_mcp`` with a dispatcher returning per-tool stub data.

    ``responses`` keys are tool names ("extract_media", "transcribe_url",
    ...); values are the raw dict the real tool would return.
    Unknown calls raise ``RuntimeError`` so unexpected MCP traffic surfaces
    as a test failure (per plan §T3.2 scaffolding).
    """

    async def fake(tool: str, payload: dict):
        if tool not in responses:
            raise RuntimeError(f"unexpected MCP call to {tool!r}")
        return responses[tool]

    monkeypatch.setattr(A, "_call_mcp", fake)


@pytest.mark.asyncio
async def test_workflow_happy_path(env, worker, monkeypatch):
    _mock_mcp_responses(
        monkeypatch,
        {
            "extract_media": {
                "audio_path": "/tmp/x.m4a",
                "metadata": {"duration_s": 90, "title": "T"},
            },
            "transcribe_url": {
                "transcript": "hello world",
                "engine": "whisper",
                "duration_ms": 90000,
            },
            "synthesize_skill_draft": {
                "skill_md": (
                    "---\n"
                    "name: Fix Printer\n"
                    "engine: markdown\n"
                    "auto_trigger: \"Fix printer\"\n"
                    "inputs: []\n"
                    "---\n"
                    "Unplug it"
                ),
                "slug": "fix-printer",
                "engine": "markdown",
                "synthetic_test_input": {"x": 1},
                "synthetic_test_expected": {"y": 2},
            },
            "dispatch_skill_review": {
                "verdict": "approved",
                "findings": [],
                "reviewer_agent_id": "755796a4-0000-0000-0000-000000000000",
            },
            "run_synthetic_test": {
                "passed": True,
                "actual_output": {"y": 2},
                "error": None,
            },
            "install_skill": {
                "skill_id": "s1",
                "path": "/x/_tenant/t1/fix-printer/skill.md",
            },
            "diffuse_learning": {"observation_id": "obs1", "soft_failed": False},
        },
    )
    # ``act_notify_session`` would write to the session DB; stub at the
    # DB-write boundary so we never hit a real connection. Use
    # ``raising=False`` because the helper symbol lands in T3.5; for the
    # happy-path test no session_id is supplied, so notify is never called.
    monkeypatch.setattr(
        A, "_write_session_message", lambda *a, **k: None, raising=False
    )
    # ``act_transcribe_url``'s success path deletes the audio file; create
    # it so the unlink doesn't error.
    Path("/tmp/x.m4a").write_bytes(b"x")

    result = await env.client.execute_workflow(
        LearnFromMediaWorkflow.run,
        {
            "source_url": "https://youtu.be/abc123",
            "tenant_id": "t1",
            "actor_user_id": "u1",
        },
        id="test-happy",
        task_queue="learn-test",
    )
    assert result["status"] == "success"
    assert result["skill_id"] == "s1"
    assert "fix-printer" in result["skill_path"]
    assert result["skill_name"] == "Fix Printer"
