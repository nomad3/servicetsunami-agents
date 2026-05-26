"""Tests for src.mcp_tools.learning.

Registry-shape coverage from T1.2 plus the T2.1 ``extract_media`` body
tests (happy path, duration cap, and yt-dlp error → typed-exception
mapping). T2.2–T2.7 will append their own sections.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.mcp_tools import learning
from src.mcp_tools.learning import (
    CODE_REVIEWER_AGENT_ID,
    DraftForbiddenShellout,
    DraftInvalid,
    MediaAntiScrape,
    MediaGeoBlocked,
    MediaNotFound,
    MediaPrivate,
    MediaTooLong,
    ReviewTimeout,
    ReviewerNotProvisioned,
    SlugExhausted,
    diffuse_learning,
    dispatch_skill_review,
    extract_media,
    install_skill,
    run_synthetic_test,
    synthesize_skill_draft,
    transcribe_url,
)


EXPECTED_TOOLS = {
    "extract_media",
    "transcribe_url",
    "synthesize_skill_draft",
    "dispatch_skill_review",
    "run_synthetic_test",
    "install_skill",
    "diffuse_learning",
}


def test_learning_module_exports_7_tools():
    assert set(learning.TOOLS.keys()) == EXPECTED_TOOLS


@pytest.mark.parametrize("tool", sorted(EXPECTED_TOOLS))
def test_each_tool_callable(tool):
    assert callable(learning.TOOLS[tool])


# ── T2.1: extract_media ────────────────────────────────────────────────
async def test_extract_media_happy_path(tmp_path):
    fake_audio = tmp_path / "abc.m4a"
    with patch("src.mcp_tools.learning._probe_duration") as probe, patch(
        "src.mcp_tools.learning._run_yt_dlp"
    ) as run:
        probe.return_value = 90
        run.return_value = {
            "title": "Demo",
            "duration": 90,
            "uploader": "Acme",
            "extractor": "youtube",
            "_filename": str(fake_audio),
        }
        result = await extract_media("https://youtu.be/abc123")
    assert result["audio_path"] == str(fake_audio)
    assert result["metadata"]["title"] == "Demo"
    assert result["metadata"]["duration_s"] == 90
    assert result["metadata"]["uploader"] == "Acme"
    assert result["metadata"]["source_platform"] == "youtube"


async def test_extract_media_too_long():
    with patch("src.mcp_tools.learning._probe_duration") as probe:
        probe.return_value = 1200  # 20 min > 900s cap
        with pytest.raises(MediaTooLong):
            await extract_media("https://youtu.be/abc123", max_duration_s=900)


@pytest.mark.parametrize(
    "stderr,exc",
    [
        ("ERROR: Private video. Sign in if you've been granted access.", MediaPrivate),
        ("ERROR: Video unavailable", MediaNotFound),
        ("ERROR: This video is not available in your country", MediaGeoBlocked),
        ("ERROR: Unable to download webpage: HTTP Error 429: Too Many Requests", MediaAntiScrape),
    ],
)
async def test_extract_media_error_mapping(stderr, exc):
    with patch("src.mcp_tools.learning._probe_duration") as probe, patch(
        "src.mcp_tools.learning._run_yt_dlp"
    ) as run:
        probe.return_value = 60
        run.side_effect = RuntimeError(stderr)
        with pytest.raises(exc):
            await extract_media("https://example.com/x")


# ── T2.2: transcribe_url ───────────────────────────────────────────────
async def test_transcribe_url_calls_existing_client(tmp_path):
    audio = tmp_path / "x.m4a"
    audio.write_bytes(b"\x00" * 100)
    with patch("src.mcp_tools.learning._transcribe_bytes_async") as transcribe:
        transcribe.return_value = {
            "transcript": "hello",
            "duration_ms": 1500,
            "engine": "whisper",
        }
        result = await transcribe_url(str(audio))
    assert result["transcript"] == "hello"
    assert result["engine"] == "whisper"
    assert result["duration_ms"] == 1500
    # Confirm the helper received the on-disk bytes verbatim — guards
    # against future refactors that might slurp the file twice or pass
    # a path instead of bytes.
    transcribe.assert_awaited_once_with(b"\x00" * 100)


async def test_transcribe_url_missing_file():
    with pytest.raises(FileNotFoundError):
        await transcribe_url("/nonexistent/path.m4a")


# ── T2.3: synthesize_skill_draft ───────────────────────────────────────
# All four tests patch ``_llm_synthesize`` directly so the anthropic SDK
# is never touched at test time — the unit under test is the validation
# logic (frontmatter parse, engine check, forbidden-shellout scan), not
# the LLM call itself. T3.x activity tests cover the network hop.
async def test_synthesize_returns_valid_draft():
    with patch("src.mcp_tools.learning._llm_synthesize") as llm:
        llm.return_value = (
            "---\nname: Fix Printer Error 41\nengine: markdown\n"
            "category: support\ntags: [printer]\n"
            "auto_trigger: \"Fix printer error 41\"\n"
            "inputs: []\n---\nUnplug the printer and ...",
            {"input": {"code": 41}, "expected": {"resolved": True}},
        )
        result = await synthesize_skill_draft("transcript text", "https://x.com/v")
    assert result["engine"] == "markdown"
    assert result["slug"] == "fix-printer-error-41"
    assert result["synthetic_test_input"] == {"code": 41}
    assert result["synthetic_test_expected"] == {"resolved": True}


async def test_synthesize_parses_invalid_draft_raises():
    with patch("src.mcp_tools.learning._llm_synthesize") as llm:
        llm.return_value = ("not valid yaml at all", {})
        with pytest.raises(DraftInvalid):
            await synthesize_skill_draft("t", "u")


async def test_synthesize_emits_python_when_clearly_deterministic():
    with patch("src.mcp_tools.learning._llm_synthesize") as llm:
        llm.return_value = (
            "---\nname: Mod-7 Compute\nengine: python\nscript: compute.py\n"
            "category: data\ntags: []\nauto_trigger: \"Compute mod-7\"\n"
            "inputs:\n  - name: x\n    type: number\n    description: input\n"
            "    required: true\n---\n",
            {"input": {"x": 14}, "expected": {"y": 0}},
        )
        result = await synthesize_skill_draft("given x compute x mod 7", "u")
    assert result["engine"] == "python"
    assert result["slug"] == "mod-7-compute"


async def test_synthesize_forbids_ytdlp_in_python_draft():
    with patch("src.mcp_tools.learning._llm_synthesize") as llm:
        llm.return_value = (
            "---\nname: bad\nengine: python\nscript: bad.py\n---\n"
            "import subprocess; subprocess.run(['yt-dlp', '...'])",
            {},
        )
        with pytest.raises(DraftForbiddenShellout):
            await synthesize_skill_draft("t", "u")


# ── T2.4: dispatch_skill_review ────────────────────────────────────────
async def test_dispatch_review_approved():
    """Happy path: reviewer returns approved verdict — surface it verbatim
    and stamp reviewer_agent_id so downstream audit knows who voted."""
    with patch("src.mcp_tools.learning._dispatch_agent") as d:
        async def _ok(*_a, **_k):
            return {"verdict": "approved", "findings": []}
        d.side_effect = _ok
        r = await dispatch_skill_review("md", "t", "u", {}, {})
    assert r["verdict"] == "approved"
    assert r["findings"] == []
    assert r["reviewer_agent_id"] == CODE_REVIEWER_AGENT_ID


async def test_dispatch_review_reviewer_not_provisioned():
    """404 from the agent dispatch endpoint = reviewer agent not in
    registry. Workflow §3 maps this to cache+notify, not retry."""
    fake_response = MagicMock(status_code=404)
    async def _404(*_a, **_k):
        raise httpx.HTTPStatusError(
            "404", request=MagicMock(), response=fake_response,
        )
    with patch("src.mcp_tools.learning._dispatch_agent", side_effect=_404):
        with pytest.raises(ReviewerNotProvisioned):
            await dispatch_skill_review("md", "t", "u", {}, {})


async def test_dispatch_review_timeout():
    """asyncio.TimeoutError bubbles through the wait_for wrapper as
    ReviewTimeout so the workflow can branch on the typed error."""
    async def _hang(*_a, **_k):
        raise asyncio.TimeoutError()
    with patch("src.mcp_tools.learning._dispatch_agent", side_effect=_hang):
        with pytest.raises(ReviewTimeout):
            await dispatch_skill_review("md", "t", "u", {}, {})


# ── T2.5: run_synthetic_test ───────────────────────────────────────────
# All three tests patch ``_execute_draft`` directly. The execute-draft
# endpoint on the api side is a separate deliverable (T4.4d) and won't
# exist when T2.5 ships — mocking the helper decouples the rollout and
# keeps the unit-under-test the subset-match + error-envelope logic.
async def test_run_synthetic_test_pass():
    """Subset match: actual carries the expected keys (plus extras) →
    passed=True. The extra ``extra: 1`` field is allowed; the skill is
    free to return more than the test pins."""
    async def _ok(*_a, **_k):
        return {"resolved": True, "extra": 1}
    with patch("src.mcp_tools.learning._execute_draft", side_effect=_ok):
        r = await run_synthetic_test("md", {"code": 41}, {"resolved": True})
    assert r["passed"] is True
    assert r["actual_output"] == {"resolved": True, "extra": 1}
    assert r["error"] is None


async def test_run_synthetic_test_fail_value_mismatch():
    """Value mismatch on a pinned key → passed=False, but the actual
    output is still surfaced so the reviewer / workflow can see the
    drift and decide how to react (revise vs reject)."""
    async def _wrong(*_a, **_k):
        return {"resolved": False}
    with patch("src.mcp_tools.learning._execute_draft", side_effect=_wrong):
        r = await run_synthetic_test("md", {"code": 41}, {"resolved": True})
    assert r["passed"] is False
    assert r["actual_output"] == {"resolved": False}
    assert "resolved" in r["actual_output"]
    assert r["error"] is None


async def test_run_synthetic_test_execution_error():
    """Execution exception is captured as data, not re-raised. The
    workflow needs a structured ``error`` field to branch into
    quarantine — raising here would collapse the branching."""
    async def _boom(*_a, **_k):
        raise RuntimeError("syntax error")
    with patch("src.mcp_tools.learning._execute_draft", side_effect=_boom):
        r = await run_synthetic_test("md", {}, {})
    assert r["passed"] is False
    assert r["actual_output"] is None
    assert "syntax error" in r["error"]


# ── T2.6: install_skill ────────────────────────────────────────────────
# All three tests patch ``_install_via_api`` directly — the api endpoint
# (POST /api/v1/skills/install-learned) is a T4.4e deliverable and won't
# exist when T2.6 ships. The unit-under-test here is the provenance
# injection + slug-collision retry policy, not the live wire.
async def test_install_skill_injects_provenance():
    """Provenance block (spec §1.6) gets spliced into the frontmatter
    before the install POST. Asserts both the marker key and a couple of
    the spec-required leaf values made it through the regex injection."""
    md_in = "---\nname: Test\nengine: markdown\n---\nbody"

    async def _fake(**kw):
        return {"skill_id": "s1", "path": "/x/test/skill.md"}

    with patch("src.mcp_tools.learning._install_via_api", side_effect=_fake) as ins:
        await install_skill(
            md_in,
            "test",
            "tenant1",
            source_url="https://x.com/v",
            reviewer_agent_id="755796a4-4cc4-4d1c-99e5-dd9c4f7d0f22",
            transcript_sha256="abc" * 21 + "abc",
            learned_by_agent_id="cfb6dd14-aaaa-bbbb-cccc-ddddeeeeffff",
        )
    sent_md = ins.call_args.kwargs["skill_md"]
    assert "provenance:" in sent_md
    assert "source_url: https://x.com/v" in sent_md
    assert "transcript_sha256:" in sent_md
    assert "reviewer_agent_id: 755796a4" in sent_md
    assert "learned_by_agent_id: cfb6dd14" in sent_md
    # Provenance must land inside the frontmatter, not appended past the
    # closing ``---``; the install path's downstream YAML parser would
    # otherwise silently lose the block.
    assert sent_md.index("provenance:") < sent_md.index("\n---\n")


async def test_install_skill_slug_conflict_retries():
    """Two consecutive 409s force the loop to retry with ``-v2`` then
    ``-v3``; the third call succeeds and the returned path reflects the
    suffixed slug. Verifies both the retry policy and that the suffix is
    threaded through to ``_install_via_api`` (not silently dropped)."""
    seen_slugs: list[str] = []

    async def _fake(**kw):
        slug = kw["slug"]
        seen_slugs.append(slug)
        if len(seen_slugs) < 3:
            raise httpx.HTTPStatusError(
                "409",
                request=MagicMock(),
                response=MagicMock(status_code=409),
            )
        return {"skill_id": "s", "path": f"/x/{slug}/skill.md"}

    with patch("src.mcp_tools.learning._install_via_api", side_effect=_fake):
        r = await install_skill(
            "---\nname: X\nengine: markdown\n---\n",
            "test",
            "tenant1",
            "https://x.com/v",
            "755796a4-4cc4-4d1c-99e5-dd9c4f7d0f22",
            "abc" * 21 + "abc",
            "cfb6dd14-aaaa-bbbb-cccc-ddddeeeeffff",
        )
    assert seen_slugs == ["test", "test-v2", "test-v3"]
    assert r["path"].endswith("/test-v3/skill.md")


async def test_install_skill_exhausts_slug_retries():
    """Five consecutive 409s — the bare slug plus ``-v2``..``-v5`` — must
    raise ``SlugExhausted`` rather than looping forever or surfacing a
    generic httpx error. The shim maps SlugExhausted → 409 (see status
    table at top of learning.py) so the workflow can branch on it."""
    async def _fake(**kw):
        raise httpx.HTTPStatusError(
            "409",
            request=MagicMock(),
            response=MagicMock(status_code=409),
        )

    with patch("src.mcp_tools.learning._install_via_api", side_effect=_fake) as ins:
        with pytest.raises(SlugExhausted):
            await install_skill(
                "---\nname: X\nengine: markdown\n---\n",
                "test",
                "tenant1",
                "https://x.com/v",
                "755796a4-4cc4-4d1c-99e5-dd9c4f7d0f22",
                "abc" * 21 + "abc",
                "cfb6dd14-aaaa-bbbb-cccc-ddddeeeeffff",
            )
    # Bare slug + 4 suffixed attempts = 5 calls total (SLUG_MAX_RETRIES).
    assert ins.call_count == 5


# ── T2.7: diffuse_learning ────────────────────────────────────────────
# diffuse_learning records a tenant-scoped KG observation describing the
# newly-installed capability so peer agents can discover it via
# semantic recall. Tests patch ``_record_observation`` so they don't
# need the (not-yet-shipped) observation endpoint live. The function
# MUST soft-fail on any underlying exception — workflow caller treats
# the soft-fail as "skill is installed and usable, diffusion is just
# delayed". Raising here would abort install, which is the wrong call.
async def test_diffuse_success():
    """Happy path: ``_record_observation`` returns an obs id, we surface
    it verbatim with ``soft_failed=False`` so the workflow records the
    success and moves on without enqueuing a retry."""
    with patch("src.mcp_tools.learning._record_observation") as r:
        async def _ok(text, metadata):
            return {"observation_id": "obs-1"}
        r.side_effect = _ok
        result = await diffuse_learning(
            "skill-1", "https://x.com/v", ["fix-printer"]
        )
        assert result["observation_id"] == "obs-1"
        assert result["soft_failed"] is False


async def test_diffuse_soft_fails_on_kg_down():
    """KG endpoint unreachable → ``_record_observation`` raises
    ``httpx.HTTPError``. We swallow it, return ``soft_failed=True`` +
    the error text, and never re-raise. The workflow caches the
    pending diffusion (§1.11) but does NOT abort install — the skill
    is still usable, semantic recall is just delayed."""
    with patch("src.mcp_tools.learning._record_observation") as r:
        async def _boom(text, metadata):
            raise httpx.HTTPError("KG unavailable")
        r.side_effect = _boom
        result = await diffuse_learning(
            "skill-1", "https://x.com/v", ["fix-printer"]
        )
        assert result["observation_id"] is None
        assert result["soft_failed"] is True
        assert "KG unavailable" in result["error"]
