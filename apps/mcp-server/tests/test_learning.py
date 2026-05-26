"""Tests for src.mcp_tools.learning.

Registry-shape coverage from T1.2 plus the T2.1 ``extract_media`` body
tests (happy path, duration cap, and yt-dlp error → typed-exception
mapping). T2.2–T2.7 will append their own sections.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.mcp_tools import learning
from src.mcp_tools.learning import (
    MediaAntiScrape,
    MediaGeoBlocked,
    MediaNotFound,
    MediaPrivate,
    MediaTooLong,
    extract_media,
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
