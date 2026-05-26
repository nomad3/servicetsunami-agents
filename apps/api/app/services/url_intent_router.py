"""URL intent routing for Luna Learn (T4.2).

Scans inbound message text for media URLs that should trigger the
``LearnFromMediaWorkflow``. Currently recognized sources:

  * YouTube (full + shorts + mobile variants + youtu.be short links)
  * Instagram (reel / reels / p)

Kept as a pure helper so both the WhatsApp inbound path
(``_detect_inbound_media`` in ``whatsapp_service.py``) and any future
chat-channel routers (Telegram, Signal, Den web UI) can share the same
regex contract without dragging the WhatsApp service into their imports.
The HTTP dispatch surface that consumes the extracted URL lives in
``LearningService.dispatch`` (T4.1a).
"""
from __future__ import annotations

import re

# Spec §7 — URL patterns. 11-char video IDs for YouTube; IG IDs are
# variable length so the slug class is `[A-Za-z0-9_-]+`.
YOUTUBE_RE = re.compile(
    r"https?://(?:www\.|m\.)?youtube\.com/(?:watch\?v=|shorts/)[A-Za-z0-9_-]{11}"
)
YOUTU_BE_RE = re.compile(r"https?://youtu\.be/[A-Za-z0-9_-]{11}")
INSTAGRAM_RE = re.compile(
    r"https?://(?:www\.)?instagram\.com/(?:reel|reels|p)/[A-Za-z0-9_-]+"
)

_LEARNING_PATTERNS = (YOUTUBE_RE, YOUTU_BE_RE, INSTAGRAM_RE)


def extract_learning_url(text: str | None) -> str | None:
    """Return the first matching learning URL in ``text``, or ``None``.

    Scans across all known patterns and returns the leftmost (earliest)
    match so that a message like ``"check this http://youtu.be/AAA and
    https://instagram.com/p/BBB"`` returns the youtu.be link rather than
    being order-dependent on the regex tuple.
    """
    if not text:
        return None

    earliest: tuple[int, str] | None = None
    for pattern in _LEARNING_PATTERNS:
        match = pattern.search(text)
        if match is None:
            continue
        if earliest is None or match.start() < earliest[0]:
            earliest = (match.start(), match.group(0))

    return earliest[1] if earliest else None
