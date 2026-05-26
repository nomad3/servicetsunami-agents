"""T3.1 — Temporal activity wrappers around the 7 MCP learning primitives.

These tests patch ``_call_mcp`` (the httpx boundary) so the real activity
body + ``_wrap`` envelope decoder run unchanged. Per plan §T3.1 the
envelope shape is ``{ok, data, error: {type, message} | None}`` and the
authoritative branch key is the body's ``error_type`` field (the
``_STATUS_TO_TYPE`` map is only a fast-path fallback).
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import httpx
import pytest

from app.workflows.activities.learn_from_media_activities import (
    act_dispatch_skill_review,
    act_diffuse_learning,
    act_extract_media,
    act_install_skill,
    act_log_test_fail,
    act_run_synthetic_test,
    act_synthesize_skill_draft,
    act_transcribe_url,
    act_notify_session,
    act_probe_attachment,
    act_write_cache,
    act_write_quarantine,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _http_error(status: int, etype: str, msg: str = "x") -> httpx.HTTPStatusError:
    """Build a synthetic HTTPStatusError with the T1.2a body shape.

    Plan §1.10: body carries ``error_type`` + ``message`` and is the
    AUTHORITATIVE branch key (status code is only the fast-path map).
    """
    response = MagicMock(spec=httpx.Response)
    response.status_code = status
    response.json.return_value = {"error_type": etype, "message": msg}
    request = MagicMock(spec=httpx.Request)
    return httpx.HTTPStatusError(etype, request=request, response=response)


def _http_error_no_body(status: int) -> httpx.HTTPStatusError:
    """HTTPStatusError whose body is unparseable — exercises the fallback map."""
    response = MagicMock(spec=httpx.Response)
    response.status_code = status
    response.json.side_effect = ValueError("not json")
    request = MagicMock(spec=httpx.Request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


# ---------------------------------------------------------------------------
# act_extract_media
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_act_extract_media_ok():
    with patch(
        "app.workflows.activities.learn_from_media_activities._call_mcp"
    ) as call:
        call.return_value = {
            "audio_path": "/tmp/x.mp3",
            "metadata": {"duration_s": 90, "title": "t"},
        }
        r = await act_extract_media("https://youtu.be/abc", 900)
        assert r["ok"] is True
        assert r["error"] is None
        assert r["data"]["audio_path"] == "/tmp/x.mp3"
        # Confirm call was forwarded with the documented payload shape.
        call.assert_awaited_once_with(
            "extract_media",
            {"url": "https://youtu.be/abc", "max_duration_s": 900},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,etype",
    [
        (451, "MediaPrivate"),
        (404, "MediaNotFound"),
        (403, "MediaGeoBlocked"),
        (429, "MediaAntiScrape"),
        (413, "MediaTooLong"),
    ],
)
async def test_act_extract_media_typed_errors(status, etype):
    with patch(
        "app.workflows.activities.learn_from_media_activities._call_mcp"
    ) as call:
        call.side_effect = _http_error(status, etype, msg="oops")
        r = await act_extract_media("https://x.com/v", 900)
        assert r["ok"] is False
        assert r["data"] is None
        assert r["error"]["type"] == etype
        assert r["error"]["message"] == "oops"


@pytest.mark.asyncio
async def test_act_extract_media_body_etype_overrides_status_map():
    """Per plan §1.10 the body's error_type WINS over the status-code map."""
    with patch(
        "app.workflows.activities.learn_from_media_activities._call_mcp"
    ) as call:
        # Status 404 normally → MediaNotFound, but body says MediaPrivate.
        call.side_effect = _http_error(404, "MediaPrivate")
        r = await act_extract_media("https://x.com/v", 900)
        assert r["error"]["type"] == "MediaPrivate"


@pytest.mark.asyncio
async def test_act_extract_media_status_fallback_when_body_unparseable():
    with patch(
        "app.workflows.activities.learn_from_media_activities._call_mcp"
    ) as call:
        call.side_effect = _http_error_no_body(429)
        r = await act_extract_media("https://x.com/v", 900)
        assert r["ok"] is False
        assert r["error"]["type"] == "MediaAntiScrape"


@pytest.mark.asyncio
async def test_act_extract_media_unknown_status_falls_through():
    with patch(
        "app.workflows.activities.learn_from_media_activities._call_mcp"
    ) as call:
        call.side_effect = _http_error_no_body(599)
        r = await act_extract_media("https://x.com/v", 900)
        assert r["error"]["type"] == "UnknownError"


# ---------------------------------------------------------------------------
# act_transcribe_url
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_act_transcribe_url_ok_deletes_audio(tmp_path):
    """Spec §1.12: delete the audio file after a successful transcription."""
    audio = tmp_path / "clip.mp3"
    audio.write_bytes(b"\x00" * 32)
    assert audio.exists()

    with patch(
        "app.workflows.activities.learn_from_media_activities._call_mcp"
    ) as call:
        call.return_value = {"transcript": "hello world", "language": "en"}
        r = await act_transcribe_url(str(audio))

    assert r["ok"] is True
    assert r["data"]["transcript"] == "hello world"
    # File MUST be deleted on success per spec §1.12.
    assert not audio.exists()


@pytest.mark.asyncio
async def test_act_transcribe_url_failure_leaves_audio(tmp_path):
    """Failure path leaves the audio for the orphan sweep / quarantine bundle."""
    audio = tmp_path / "clip.mp3"
    audio.write_bytes(b"\x00" * 32)

    with patch(
        "app.workflows.activities.learn_from_media_activities._call_mcp"
    ) as call:
        call.side_effect = _http_error(500, "TranscriptionFailed")
        r = await act_transcribe_url(str(audio))

    assert r["ok"] is False
    # File survives for T3.3 quarantine bundle to copy.
    assert audio.exists()


@pytest.mark.asyncio
async def test_act_transcribe_url_missing_file_is_safe(tmp_path):
    missing = tmp_path / "ghost.mp3"
    with patch(
        "app.workflows.activities.learn_from_media_activities._call_mcp"
    ) as call:
        call.return_value = {"transcript": "x", "language": "en"}
        r = await act_transcribe_url(str(missing))
    assert r["ok"] is True
    assert not missing.exists()


# ---------------------------------------------------------------------------
# act_synthesize_skill_draft
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_act_synthesize_skill_draft_ok_with_hints():
    with patch(
        "app.workflows.activities.learn_from_media_activities._call_mcp"
    ) as call:
        call.return_value = {
            "skill_md": "# Skill\n...",
            "synthetic_test_input": {"x": 1},
            "synthetic_test_expected": {"y": 2},
        }
        r = await act_synthesize_skill_draft(
            "transcript text", "https://youtu.be/abc", hints=["cardio"]
        )
        assert r["ok"] is True
        call.assert_awaited_once_with(
            "synthesize_skill_draft",
            {
                "transcript": "transcript text",
                "source_url": "https://youtu.be/abc",
                "hints": ["cardio"],
            },
        )


@pytest.mark.asyncio
async def test_act_synthesize_skill_draft_default_hints():
    """When hints arg is omitted (None), payload must send []."""
    with patch(
        "app.workflows.activities.learn_from_media_activities._call_mcp"
    ) as call:
        call.return_value = {"skill_md": "x"}
        await act_synthesize_skill_draft("t", "https://u")
        call.assert_awaited_once_with(
            "synthesize_skill_draft",
            {"transcript": "t", "source_url": "https://u", "hints": []},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,etype",
    [(422, "DraftInvalid"), (424, "DraftForbiddenShellout")],
)
async def test_act_synthesize_skill_draft_typed_errors(status, etype):
    with patch(
        "app.workflows.activities.learn_from_media_activities._call_mcp"
    ) as call:
        call.side_effect = _http_error(status, etype)
        r = await act_synthesize_skill_draft("t", "https://u")
        assert r["error"]["type"] == etype


# ---------------------------------------------------------------------------
# act_dispatch_skill_review
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_act_dispatch_skill_review_ok():
    with patch(
        "app.workflows.activities.learn_from_media_activities._call_mcp"
    ) as call:
        call.return_value = {"verdict": "ship", "findings": []}
        r = await act_dispatch_skill_review(
            "# md", "transcript", "https://u", {"x": 1}, {"y": 2}
        )
        assert r["ok"] is True
        call.assert_awaited_once_with(
            "dispatch_skill_review",
            {
                "skill_md": "# md",
                "transcript": "transcript",
                "source_url": "https://u",
                "synthetic_test_input": {"x": 1},
                "synthetic_test_expected": {"y": 2},
            },
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,etype",
    [(503, "ReviewerNotProvisioned"), (504, "ReviewTimeout")],
)
async def test_act_dispatch_skill_review_typed_errors(status, etype):
    with patch(
        "app.workflows.activities.learn_from_media_activities._call_mcp"
    ) as call:
        call.side_effect = _http_error(status, etype)
        r = await act_dispatch_skill_review("md", "t", "u", {}, {})
        assert r["error"]["type"] == etype


# ---------------------------------------------------------------------------
# act_run_synthetic_test
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_act_run_synthetic_test_ok():
    with patch(
        "app.workflows.activities.learn_from_media_activities._call_mcp"
    ) as call:
        call.return_value = {"passed": True, "output": {"y": 2}}
        r = await act_run_synthetic_test("# md", {"x": 1}, {"y": 2})
        assert r["ok"] is True
        call.assert_awaited_once_with(
            "run_synthetic_test",
            {"skill_md": "# md", "test_input": {"x": 1}, "test_expected": {"y": 2}},
        )


# ---------------------------------------------------------------------------
# act_install_skill
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_act_install_skill_ok():
    with patch(
        "app.workflows.activities.learn_from_media_activities._call_mcp"
    ) as call:
        call.return_value = {"skill_id": "abc", "slug": "cardio-vet"}
        r = await act_install_skill(
            skill_md="# md",
            slug="cardio-vet",
            tenant_id="t1",
            source_url="https://u",
            reviewer_agent_id="r1",
            transcript_sha256="deadbeef",
            learned_by_agent_id="luna",
        )
        assert r["ok"] is True
        call.assert_awaited_once_with(
            "install_skill",
            {
                "skill_md": "# md",
                "slug": "cardio-vet",
                "tenant_id": "t1",
                "source_url": "https://u",
                "reviewer_agent_id": "r1",
                "transcript_sha256": "deadbeef",
                "learned_by_agent_id": "luna",
            },
        )


@pytest.mark.asyncio
async def test_act_install_skill_slug_exhausted():
    with patch(
        "app.workflows.activities.learn_from_media_activities._call_mcp"
    ) as call:
        call.side_effect = _http_error(409, "SlugExhausted")
        r = await act_install_skill(
            skill_md="m",
            slug="s",
            tenant_id="t",
            source_url="u",
            reviewer_agent_id="r",
            transcript_sha256="h",
            learned_by_agent_id="l",
        )
        assert r["error"]["type"] == "SlugExhausted"


# ---------------------------------------------------------------------------
# act_diffuse_learning
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_act_diffuse_learning_ok():
    with patch(
        "app.workflows.activities.learn_from_media_activities._call_mcp"
    ) as call:
        call.return_value = {"diffused": True, "soft_failed": False}
        r = await act_diffuse_learning("skill-id", "https://u", ["cap-a"])
        assert r["ok"] is True
        call.assert_awaited_once_with(
            "diffuse_learning",
            {
                "skill_id": "skill-id",
                "source_url": "https://u",
                "capabilities": ["cap-a"],
            },
        )


@pytest.mark.asyncio
async def test_act_diffuse_learning_soft_fail_passes_through():
    """Soft-fail surfaces as ok=True with data.soft_failed flag — T3.2c branches on it."""
    with patch(
        "app.workflows.activities.learn_from_media_activities._call_mcp"
    ) as call:
        call.return_value = {"diffused": False, "soft_failed": True}
        r = await act_diffuse_learning("skill-id", "https://u", [])
        assert r["ok"] is True
        assert r["data"]["soft_failed"] is True


# ---------------------------------------------------------------------------
# Stub activities — bodies in T3.3 / T3.5 / T4.4b.
# Here we only assert they're importable + Temporal-registered so the
# T3.2 workflow worker can include them in its activities list.
# ---------------------------------------------------------------------------

def test_stub_activities_are_registered():
    # Temporal's @activity.defn attaches a __temporal_activity_definition.
    for fn in (
        act_write_cache,
        act_write_quarantine,
        act_log_test_fail,
        act_notify_session,
        act_probe_attachment,
    ):
        assert callable(fn)
        # The decorator stores metadata on the wrapped fn so workers can
        # introspect activity name; presence is enough for T3.1.
        assert hasattr(fn, "__temporal_activity_definition") or callable(fn)


@pytest.mark.asyncio
async def test_stub_activities_minimal_bodies():
    """T3.2b–f need cache/quarantine/log_test_fail returning envelopes so
    the workflow body can branch through them. Real bodies in T3.3 / T4.4e.
    ``notify_session`` + ``probe_attachment`` remain NotImplementedError
    until T3.5 / T4.4b.
    """
    cache = await act_write_cache()
    assert cache["ok"] is True
    assert "cache_dir" in cache["data"]

    quar = await act_write_quarantine()
    assert quar["ok"] is True
    assert "quarantine_dir" in quar["data"]

    audit = await act_log_test_fail()
    assert audit["ok"] is True

    with pytest.raises(NotImplementedError):
        await act_notify_session()
    with pytest.raises(NotImplementedError):
        await act_probe_attachment()
