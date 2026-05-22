"""Platform Safety Floor — tier 2 (embedding-based detection).

Sits between the tier-1 regex (pure, line-speed, conservative) and
tier 3 (LLM classifier, expensive, most accurate) in the layered
detection pipeline.

Tier 2 runs ONLY on messages that tier 1 flagged as "potentially
sensitive" — a separate, more permissive set of regex pre-screens
that catches phrasing without being strict enough to refuse outright.
Embedding-based detection then compares the message against a
curated corpus of known-harm canonical phrasings; cosine similarity
above a per-category threshold yields a block verdict.

This module is PURE in the same sense as the tier-1 ``consult()``:
the IO wrapper in ``platform_safety_io.py`` handles audit + fail-
open/closed policy. The corpus loader lives here but reads from a
path supplied by env var; the corpus content is curated outside
this public file and mounted at deploy time.

Privacy: this module NEVER stores or logs message text. The
embedding call returns a vector; cosine similarity returns a number;
nothing else.

Design: docs/plans/2026-05-21-platform-safety-floor-design.md §4 + §10
Luna sign-off: §12 #4 (corpus curation via 2FA admin endpoint) +
§12 #7 (shadow mode for first 14 days for tier 3 — tier 2 is
blocking from day one).
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.core.safety_defaults import category_for_label

log = logging.getLogger(__name__)


# ── Corpus shape ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class CorpusEntry:
    """One curated harm-phrasing entry. Stored in the private corpus
    file pointed to by ``PLATFORM_SAFETY_CORPUS_PATH``.

    Each entry carries:
      - ``category``: which platform safety category this entry maps
        to (must be a key in PLATFORM_SAFETY_CATEGORIES)
      - ``vector``: pre-computed 768-dim embedding for the canonical
        phrasing. The corpus author runs the embed step offline; we
        do NOT embed text here at boot.
      - ``trigger_id``: opaque platform-admin-only id (same shape as
        tier-1 trigger ids), surfaced in audit logs but NEVER to the
        client.
    """

    category: str
    vector: tuple[float, ...]
    trigger_id: str


# ── Corpus loader ────────────────────────────────────────────────────


_CORPUS: list[CorpusEntry] = []
_CORPUS_LOADED = False
_CORPUS_PATH_ENV = "PLATFORM_SAFETY_CORPUS_PATH"


def _load_corpus() -> list[CorpusEntry]:
    """Load the curated harm corpus from the path in
    ``PLATFORM_SAFETY_CORPUS_PATH``.

    Format: JSONL, one entry per line:
      {"category": "...", "trigger_id": "...", "vector": [768 floats]}

    Loaded ONCE at module load (lazy). Reload via process restart;
    we deliberately do not provide a hot-reload to avoid attacker-
    triggered corpus swap via filesystem race.

    Empty / missing path → empty corpus. Tier 2 returns allow for
    every message until a corpus is mounted. This is fine for v1 —
    the framework ships now, real corpus mounts at production
    deploy time per the design's "private corpus" approach (§4).
    """
    global _CORPUS, _CORPUS_LOADED
    if _CORPUS_LOADED:
        return _CORPUS

    path = os.environ.get(_CORPUS_PATH_ENV, "").strip()
    if not path:
        log.info(
            "platform_safety.tier2: %s not set; tier 2 will pass-through "
            "until corpus is mounted. Tier 1 + tier 3 still operate.",
            _CORPUS_PATH_ENV,
        )
        _CORPUS_LOADED = True
        return _CORPUS

    p = Path(path)
    if not p.exists():
        log.error(
            "platform_safety.tier2: corpus path %s does not exist; "
            "tier 2 will pass-through. Check the deploy config.",
            path,
        )
        _CORPUS_LOADED = True
        return _CORPUS

    entries: list[CorpusEntry] = []
    try:
        for line_no, raw in enumerate(p.read_text("utf-8").splitlines(), 1):
            raw = raw.strip()
            if not raw or raw.startswith("#"):
                continue
            try:
                obj = json.loads(raw)
                cat = str(obj["category"])
                # Validate at load time so a bad entry surfaces here
                # not on the chat hot path
                category_for_label(cat)
                vec = tuple(float(x) for x in obj["vector"])
                if len(vec) == 0:
                    raise ValueError("empty vector")
                trigger = str(obj.get("trigger_id", f"t2-line-{line_no}"))
                entries.append(
                    CorpusEntry(
                        category=cat, vector=vec, trigger_id=trigger,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                log.error(
                    "platform_safety.tier2: skipping corpus line %d "
                    "(%s): %s",
                    line_no, path, exc,
                )
    except Exception as exc:  # noqa: BLE001
        log.error(
            "platform_safety.tier2: corpus load failed %s: %s; "
            "tier 2 will pass-through. Tier 1 + tier 3 still operate.",
            path, exc,
        )
        _CORPUS_LOADED = True
        return _CORPUS

    log.info(
        "platform_safety.tier2: loaded %d corpus entries from %s",
        len(entries), path,
    )
    _CORPUS = entries
    _CORPUS_LOADED = True
    return _CORPUS


def _reset_corpus_for_tests() -> None:
    """Test-only helper to clear the cached corpus + reload flag.
    Production code MUST NOT call this — corpus is load-once on
    purpose."""
    global _CORPUS, _CORPUS_LOADED
    _CORPUS = []
    _CORPUS_LOADED = False


# ── Pre-screen: tier-1 → tier-2 escalation ───────────────────────────


# Cheap "potentially sensitive" pre-screen. Wider than tier-1
# block-patterns but still narrow enough that 99% of chat turns
# bypass tier 2 entirely (line-speed). Tier 1 catches the obvious;
# this catches the maybe-worth-a-closer-look.
#
# Each pattern is paired with the categories it could escalate to,
# so the tier-2 embedding lookup can scope its corpus search.
_SENSITIVITY_PATTERNS: list[tuple[re.Pattern[str], tuple[str, ...]]] = [
    # English (PR 4)
    (
        re.compile(
            r"\b(weapon|bomb|nuclear|chemical|biological|"
            r"radiologic|explosive|poison|toxin|pathogen)\b",
            re.IGNORECASE,
        ),
        ("mass_harm_synthesis", "terrorism_planning"),
    ),
    (
        re.compile(
            r"\b(malware|exploit|virus|trojan|rootkit|"
            r"ransomware|backdoor|payload|shellcode)\b",
            re.IGNORECASE,
        ),
        ("bulk_malware",),
    ),
    (
        re.compile(
            r"\b(home[\s_-]*address|family[\s_-]*member|"
            r"private[\s_-]*phone|social[\s_-]*security|"
            r"medical[\s_-]*record)\b",
            re.IGNORECASE,
        ),
        ("targeted_doxing",),
    ),
    (
        re.compile(
            r"\b(deepfake|voter[\s_-]*list|election|ballot|"
            r"polling[\s_-]*station)\b",
            re.IGNORECASE,
        ),
        ("election_interference_bulk",),
    ),
    (
        re.compile(
            r"\b(child|minor|underage)\b",
            re.IGNORECASE,
        ),
        ("csam", "child_safety"),
    ),
    # Spanish (PR 8 — design §8). Same intent clusters, ES
    # vocabulary. UNICODE flag is set by re.IGNORECASE on a
    # \b boundary so accents in 'químic[ao]' match cleanly.
    (
        re.compile(
            r"\b(arma|bomba|nuclear|qu[íi]mic[ao]|biol[óo]gic[ao]|"
            r"radiol[óo]gic[ao]|explosivo|veneno|toxina|"
            r"pat[óo]geno)\b",
            re.IGNORECASE | re.UNICODE,
        ),
        ("mass_harm_synthesis", "terrorism_planning"),
    ),
    (
        re.compile(
            r"\b(malware|exploit|virus|troyano|rootkit|"
            r"ransomware|puerta[\s_-]*trasera|payload|shellcode)\b",
            re.IGNORECASE | re.UNICODE,
        ),
        ("bulk_malware",),
    ),
    (
        re.compile(
            r"\b(direcci[óo]n[\s_-]*personal|miembro[\s_-]*de[\s_-]*la[\s_-]*familia|"
            r"tel[ée]fono[\s_-]*privado|historial[\s_-]*m[ée]dico|"
            r"n[úu]mero[\s_-]*de[\s_-]*identidad)\b",
            re.IGNORECASE | re.UNICODE,
        ),
        ("targeted_doxing",),
    ),
    (
        re.compile(
            r"\b(deepfake|lista[\s_-]*electoral|elecci[óo]n|"
            r"boleta|colegio[\s_-]*electoral|votaci[óo]n)\b",
            re.IGNORECASE | re.UNICODE,
        ),
        ("election_interference_bulk",),
    ),
    (
        re.compile(
            r"\b(ni[ñn][oa]|menor|menor[\s_-]*de[\s_-]*edad)\b",
            re.IGNORECASE | re.UNICODE,
        ),
        ("csam", "child_safety"),
    ),
]


def candidate_categories(message: str) -> tuple[str, ...]:
    """Return the union of categories that ANY sensitivity pattern
    matches in ``message``. Empty tuple → tier 2 can be skipped
    entirely for this message.
    """
    if not message:
        return ()
    hits: set[str] = set()
    for pattern, cats in _SENSITIVITY_PATTERNS:
        if pattern.search(message):
            hits.update(cats)
    return tuple(sorted(hits))


# ── Per-category thresholds ──────────────────────────────────────────


# Cosine similarity above this value → block. Tuned conservatively;
# false-positive cost on the floor is high. Existential categories
# get the lowest thresholds (more sensitive).
_CATEGORY_THRESHOLDS: dict[str, float] = {
    "csam": 0.78,
    "child_safety": 0.80,
    "mass_harm_synthesis": 0.82,
    "terrorism_planning": 0.82,
    "election_interference_bulk": 0.86,
    "bulk_malware": 0.86,
    "targeted_doxing": 0.86,
}


def threshold_for(category: str) -> float:
    """Lookup with a defensive default. Unknown category → 0.99
    (effectively never fires) to avoid false positives on drift."""
    return _CATEGORY_THRESHOLDS.get(category, 0.99)


# ── Pure cosine ──────────────────────────────────────────────────────


def cosine_similarity(a, b) -> float:
    """Cosine similarity between two equal-length vectors. Returns
    0.0 on dimension mismatch / zero-norm rather than raising — the
    detection layer must fail soft on malformed inputs."""
    try:
        if len(a) != len(b):
            return 0.0
        dot = 0.0
        na = 0.0
        nb = 0.0
        for x, y in zip(a, b):
            dot += x * y
            na += x * x
            nb += y * y
        denom = math.sqrt(na) * math.sqrt(nb)
        if denom == 0.0:
            return 0.0
        return dot / denom
    except (TypeError, ValueError):
        return 0.0


# ── Tier 2 detection ─────────────────────────────────────────────────


@dataclass(frozen=True)
class Tier2Hit:
    """Outcome of a tier-2 evaluation. ``hit`` is the highest-
    similarity corpus entry above its category's threshold; None when
    nothing matched."""

    hit: Optional[CorpusEntry]
    confidence: float  # 0.0-1.0


def evaluate(
    message: str,
    *,
    embed_fn=None,
) -> Tier2Hit:
    """Embedding-based detection for ``message``.

    Steps:
      1. Pre-screen via `candidate_categories(message)`. Empty → return
         miss immediately (tier 2 skipped).
      2. Embed the message via `embed_fn` (defaults to the production
         embedding_service.embed_text). If embedding fails, return
         miss + log — tier-1 result stands, tier-3 will run.
      3. Scope corpus to entries whose category is in the candidates.
      4. Compute cosine similarity; track the max.
      5. If the max ≥ that entry's category threshold, return a hit.

    Pure-functional except for the embedding call (which IS an IO
    boundary, hence injectable for tests via ``embed_fn``).
    """
    candidates = candidate_categories(message)
    if not candidates:
        return Tier2Hit(hit=None, confidence=0.0)

    corpus = _load_corpus()
    if not corpus:
        return Tier2Hit(hit=None, confidence=0.0)

    # Embedding boundary — inject for tests
    if embed_fn is None:
        from app.services.embedding_service import (
            embed_text,
            EmbeddingServiceUnavailable,
        )

        def _default_embed(t: str):
            try:
                return embed_text(t, task_type="RETRIEVAL_QUERY")
            except EmbeddingServiceUnavailable as exc:
                log.warning(
                    "platform_safety.tier2: embedding service "
                    "unavailable: %s; tier 2 returns miss "
                    "(tier 3 still runs)",
                    exc,
                )
                return None
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "platform_safety.tier2: embed_text raised %s; "
                    "tier 2 returns miss",
                    exc,
                )
                return None

        embed_fn = _default_embed

    msg_vec = embed_fn(message)
    # (Review NIT-2) Defensive against numpy.ndarray returns from a
    # future embedding-service change: truthiness on ndarray raises
    # ValueError for >1 elements. Be explicit about the None-or-empty
    # check instead of relying on `if not msg_vec:`.
    if msg_vec is None or len(msg_vec) == 0:
        return Tier2Hit(hit=None, confidence=0.0)

    # Track the highest similarity AMONG entries that ALSO clear
    # their own per-category threshold. A higher-sim entry below
    # threshold loses to a lower-sim entry above its threshold —
    # that's correct, because the floor's promise is "block at this
    # sensitivity for this category," not "block on max similarity
    # regardless of category." (Review NIT-1 clarification.)
    best_hit: Optional[CorpusEntry] = None
    best_sim = 0.0
    candidate_set = set(candidates)
    for entry in corpus:
        if entry.category not in candidate_set:
            continue
        sim = cosine_similarity(msg_vec, entry.vector)
        if sim > best_sim and sim >= threshold_for(entry.category):
            best_sim = sim
            best_hit = entry

    return Tier2Hit(hit=best_hit, confidence=best_sim)


__all__ = [
    "CorpusEntry",
    "Tier2Hit",
    "candidate_categories",
    "cosine_similarity",
    "evaluate",
    "threshold_for",
]
