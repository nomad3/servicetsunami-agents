"""Tests for ``url_intent_router.extract_learning_url`` (T4.2).

Spec §7 patterns:
    * YouTube — ``youtube.com/watch?v=`` + ``youtube.com/shorts/`` +
      ``m.youtube.com`` mobile variant
    * youtu.be short links
    * Instagram — ``/reel/``, ``/reels/``, ``/p/``

Plus the integration shape: surrounding text, no-match returns ``None``,
mixed positioning returns the leftmost match.
"""
from __future__ import annotations

import pytest

from app.services.url_intent_router import extract_learning_url


# ── Positive matches ──────────────────────────────────────────────
@pytest.mark.parametrize(
    "text,expected",
    [
        # YouTube — watch URL
        (
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        ),
        # YouTube — naked youtube.com (no www)
        (
            "https://youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtube.com/watch?v=dQw4w9WgXcQ",
        ),
        # YouTube — shorts
        (
            "https://www.youtube.com/shorts/abcdefghijk",
            "https://www.youtube.com/shorts/abcdefghijk",
        ),
        # YouTube — mobile m.youtube.com
        (
            "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
        ),
        # youtu.be — short link
        ("https://youtu.be/dQw4w9WgXcQ", "https://youtu.be/dQw4w9WgXcQ"),
        ("http://youtu.be/dQw4w9WgXcQ", "http://youtu.be/dQw4w9WgXcQ"),
        # Instagram — reel
        (
            "https://www.instagram.com/reel/Cabc123_def/",
            "https://www.instagram.com/reel/Cabc123_def",
        ),
        # Instagram — reels (plural)
        (
            "https://www.instagram.com/reels/Cabc123_def/",
            "https://www.instagram.com/reels/Cabc123_def",
        ),
        # Instagram — /p/ post
        (
            "https://www.instagram.com/p/Cabc123_def/",
            "https://www.instagram.com/p/Cabc123_def",
        ),
        # Instagram — bare instagram.com no www
        (
            "https://instagram.com/reel/Cabc123_def/",
            "https://instagram.com/reel/Cabc123_def",
        ),
    ],
)
def test_extracts_known_learning_urls(text: str, expected: str) -> None:
    assert extract_learning_url(text) == expected


# ── Embedded URL in surrounding text ──────────────────────────────
def test_extracts_url_embedded_in_message() -> None:
    text = "hey luna can you learn from https://youtu.be/dQw4w9WgXcQ please?"
    assert extract_learning_url(text) == "https://youtu.be/dQw4w9WgXcQ"


def test_extracts_leftmost_when_multiple_present() -> None:
    text = (
        "first https://youtu.be/AAAAAAAAAAA then "
        "https://www.instagram.com/reel/BBB123"
    )
    assert extract_learning_url(text) == "https://youtu.be/AAAAAAAAAAA"


# ── Negative cases ────────────────────────────────────────────────
@pytest.mark.parametrize(
    "text",
    [
        "",
        None,
        "just a plain text message",
        "check this site https://example.com/video",
        # YouTube domain but no watch/shorts path
        "https://www.youtube.com/feed/subscriptions",
        # youtu.be but ID too short
        "https://youtu.be/short",
        # Instagram but unsupported path (stories)
        "https://www.instagram.com/stories/someuser/123",
    ],
)
def test_no_match_returns_none(text: str | None) -> None:
    assert extract_learning_url(text) is None


# ── _detect_inbound_media integration (T4.2) ───────────────────────
from types import SimpleNamespace  # noqa: E402


def test_detect_inbound_media_surfaces_learning_url_when_no_attachment() -> None:
    """A plain text WhatsApp message carrying a YouTube link should be
    classified as ``learning_url`` so the caller can dispatch via
    LearningService instead of routing through the normal chat pipeline.
    """
    from app.services.whatsapp_service import _detect_inbound_media

    msg = SimpleNamespace(imageMessage=None, audioMessage=None, documentMessage=None)
    text = "hey can you learn this https://youtu.be/dQw4w9WgXcQ"

    media_type, media_mime, media_caption = _detect_inbound_media(msg, text)

    assert media_type == "learning_url"
    assert media_mime == "https://youtu.be/dQw4w9WgXcQ"
    assert media_caption == text


def test_detect_inbound_media_prefers_attachment_over_learning_url() -> None:
    """When both an image attachment and a URL are present, image wins —
    learning_url is a fallback for *text-only* messages. Keeps Luna's
    image pipeline (vision / caption) intact for chats that happen to
    drop a YouTube link in the caption."""
    from app.services.whatsapp_service import _detect_inbound_media

    msg = SimpleNamespace(
        imageMessage=SimpleNamespace(mimetype="image/jpeg", caption=""),
        audioMessage=None,
        documentMessage=None,
    )
    text = "look at this https://youtu.be/dQw4w9WgXcQ"

    media_type, media_mime, _ = _detect_inbound_media(msg, text)

    assert media_type == "image"
    assert media_mime == "image/jpeg"


def test_detect_inbound_media_no_url_returns_none() -> None:
    """Pure text without a learning URL still returns the original
    ``(None, None, text)`` tuple — no regression on the chat path."""
    from app.services.whatsapp_service import _detect_inbound_media

    msg = SimpleNamespace(imageMessage=None, audioMessage=None, documentMessage=None)
    media_type, media_mime, media_caption = _detect_inbound_media(msg, "ping")

    assert media_type is None
    assert media_mime is None
    assert media_caption == "ping"
