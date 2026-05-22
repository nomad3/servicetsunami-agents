"""Platform Safety Floor — pure detection.

Sits ABOVE the operator value layer (#647). Always-on. No kill-switch.
No break-glass on the hot chat path. Catches things that are illegal
or cause mass harm regardless of which tenant or operator is using
the platform.

Design: docs/plans/2026-05-21-platform-safety-floor-design.md
Luna sign-off: §12 [ Luna Signed Off — Platform Safety Floor §12 ]

PR 1 of the 8-PR sequence: tier-1 regex + the framework. Tier 2
(embedding) lands in PR 4, tier 3 (LLM classifier) in PR 5. Until
those land, ``consult`` returns ``allow`` for everything tier 1
doesn't match.

This module is PURE — no DB, no logging, no IO. The IO wrapper in
``platform_safety_io.py`` records audit events + handles fail-open /
fail-closed policy per category.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from app.core.safety_defaults import (
    PLATFORM_SAFETY_CATEGORIES,
    REFUSAL_MESSAGE_TEMPLATE,
    category_for_label,
)


# ── Verdict shape ────────────────────────────────────────────────────


@dataclass(frozen=True)
class PlatformSafetyVerdict:
    """Result of one ``consult()`` call.

    ``decision``:
      - 'allow' — proceed to the operator value layer + dispatch
      - 'block' — refuse the message immediately; never reach LLM

    ``category`` is set on block; None on allow.
    ``detection_tier`` is which tier produced the verdict (1/2/3).
    ``confidence`` is set on tier 2+ (None for binary tier 1).
    ``trigger_id`` is an opaque short id for the matched pattern so
        the audit log can record WHICH pattern fired without storing
        the regex itself in user-visible places.
    """

    decision: str  # 'allow' | 'block'
    category: Optional[str] = None
    detection_tier: int = 1
    confidence: Optional[float] = None
    trigger_id: Optional[str] = None

    @classmethod
    def allow(cls) -> "PlatformSafetyVerdict":
        return cls(decision="allow")

    @classmethod
    def block(
        cls,
        *,
        category: str,
        detection_tier: int,
        confidence: Optional[float] = None,
        trigger_id: Optional[str] = None,
    ) -> "PlatformSafetyVerdict":
        # Validate category at construction so a typo in a caller
        # surfaces here, not at the database insert.
        category_for_label(category)
        return cls(
            decision="block",
            category=category,
            detection_tier=detection_tier,
            confidence=confidence,
            trigger_id=trigger_id,
        )

    def to_refusal_message(self) -> str:
        """Render the operator-facing refusal string. NEVER includes
        the trigger or pattern — only the coarse-grained
        ``human_readable`` category label per §9."""
        if self.decision != "block" or not self.category:
            return ""
        policy = category_for_label(self.category)
        return REFUSAL_MESSAGE_TEMPLATE.format(
            category_label=policy.human_readable,
        )

    def to_dict(self) -> dict:
        """Client-safe serialization. ``trigger_id`` is intentionally
        OMITTED — it's a platform-admin-only opaque pattern id (see
        §9 + the class docstring) and would leak the
        detection-pattern catalogue if it appeared on the chat
        client's ChatMessage.context. The audit row (written by
        ``platform_safety_io._record_event``) carries trigger_id;
        client-visible metadata does not.

        Use ``to_admin_dict()`` when you need the trigger_id (admin
        endpoint, server-side log)."""
        return {
            "decision": self.decision,
            "category": self.category,
            "detection_tier": self.detection_tier,
            "confidence": self.confidence,
        }

    def to_admin_dict(self) -> dict:
        """Admin-only serialization including ``trigger_id``. NEVER
        feed this into a client-visible response field."""
        return {
            **self.to_dict(),
            "trigger_id": self.trigger_id,
        }


# ── Tier 1: regex patterns ───────────────────────────────────────────
#
# Each entry is (compiled_pattern, category, trigger_id). The
# trigger_id is the opaque short label used in audit logs — operators
# never see it; only platform admins do.
#
# PR 1 ships with a SMALL number of demonstrably-defensible patterns
# for the framework. The real corpus is curated in a private path
# and loaded via the corpus loader landing in PR 4 (tier 2). The
# patterns here are intentionally conservative — false-positive cost
# is high on the floor.
#
# IMPORTANT for reviewers: this file ships to a public repo. Do NOT
# add CSAM-specific terminology or detailed mass-harm instructions
# here. Those belong in the private corpus loaded at runtime. The
# patterns below cover obvious cases where the terminology itself is
# already public (e.g. bioweapon names) AND there is no legitimate-
# discourse use case for the specific phrasing matched.


def _compile(pattern: str) -> re.Pattern[str]:
    """Compile a tier-1 pattern. Case-insensitive + DOTALL + unicode
    so simple obfuscation (line breaks, mixed case) doesn't bypass."""
    return re.compile(pattern, re.IGNORECASE | re.DOTALL | re.UNICODE)


# Tier 1 patterns. Each tuple: (compiled, category, trigger_id).
# trigger_id is stable across versions so the platform-admin audit
# dashboard can track patterns over time.
_TIER1_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    # Mass-harm synthesis — "how to synthesize <bioweapon>" phrasing.
    # Bioweapon names are publicly known (CDC, OPCW, etc); pattern
    # only fires when paired with operational verbs that indicate
    # synthesis/production intent vs historical/policy discussion.
    (
        _compile(
            r"\b(synthes[ie]z[a-z]*|manufactur[a-z]*|produc[a-z]*|"
            r"weaponiz[a-z]*)\b[\s\S]{0,80}\b"
            r"(anthrax|smallpox|sarin|vx[\s_-]*nerve|ricin|"
            r"botulin(?:um)?|tabun|novichok)\b"
        ),
        "mass_harm_synthesis",
        "mh-001-bioweapon-synthesis-verb",
    ),
    # Mass-harm synthesis — nuclear weapon construction terminology
    # paired with production intent.
    (
        _compile(
            r"\b(build|construct|assembl[a-z]*|fabricat[a-z]*)\b"
            r"[\s\S]{0,60}\b"
            r"(nuclear[\s_-]*weapon|atomic[\s_-]*bomb|"
            r"fission[\s_-]*device|enriched[\s_-]*uranium[\s_-]*"
            r"(?:weapon|bomb|device))\b"
        ),
        "mass_harm_synthesis",
        "mh-002-nuclear-construction-verb",
    ),
    # Bulk malware authoring — request for ransomware kits with
    # explicit deployment intent.
    (
        _compile(
            r"\b(write|build|generate|create)\b[\s\S]{0,40}\b"
            r"(ransomware|wiper[\s_-]*malware|polymorphic[\s_-]*"
            r"(?:crypter|virus))\b[\s\S]{0,80}\b"
            r"(deploy|distribute|spread|infect|target)\b"
        ),
        "bulk_malware",
        "bm-001-ransomware-deploy-intent",
    ),
    # ── Spanish-language tier-1 patterns (PR 8 — design §8) ─────────
    # Cover the same intent shapes in Spanish so a multilingual
    # tenant gets line-speed coverage in either language. Tier 2's
    # multilingual embeddings cover the long tail across all
    # languages we may host; tier 1 stays narrow + bilingual for
    # the cheap path.
    (
        _compile(
            # (Review PR 8 NIT) `elaborar` is the most natural
            # Spanish verb for synthesizing a substance — added
            # alongside sintetizar/fabricar/producir/armamentizar.
            # Skipping `preparar` (too broad — "prepare a
            # presentation about anthrax" would false-positive);
            # tier 2 catches that via embedding.
            r"\b(sintetiz[a-z]*|fabric[a-z]*|produc[a-z]*|"
            r"armament[a-z]*|elabor[a-záéíóú]+)\b"
            r"[\s\S]{0,80}\b"
            r"(ántrax|antrax|viruela|sarín|sarin|"
            r"ricina|botulinum|tabún|tabun|novichok)\b"
        ),
        "mass_harm_synthesis",
        "mh-001-es-bioweapon-synthesis-verb",
    ),
    (
        _compile(
            r"\b(constru[ií]r|ensamblar|fabricar|montar)\b"
            r"[\s\S]{0,60}\b"
            r"(arma[\s_-]*nuclear|bomba[\s_-]*at[óo]mica|"
            r"dispositivo[\s_-]*de[\s_-]*fisi[óo]n|"
            r"uranio[\s_-]*enriquecido)\b"
        ),
        "mass_harm_synthesis",
        "mh-002-es-nuclear-construction-verb",
    ),
    (
        _compile(
            # Conjugated verb stems — match all common forms
            # (escribir/escribe/escribió/escriba/escribiendo etc.)
            r"\b(escrib[a-záéíóú]+|gener[a-záéíóú]+|cre[a-záéíóú]+|"
            r"constru[a-záéíóú]+|fabric[a-záéíóú]+)\b"
            r"[\s\S]{0,40}\b"
            r"(ransomware|malware[\s_-]*destructivo|"
            r"crypter[\s_-]*polim[óo]rfico)\b[\s\S]{0,80}\b"
            r"(despleg[a-záéíóú]+|distribu[ií]r|distribuy[a-záéíóú]+|"
            r"propag[a-záéíóú]+|infect[a-záéíóú]+|atac[a-záéíóú]+)\b"
        ),
        "bulk_malware",
        "bm-001-es-ransomware-deploy-intent",
    ),

    # NOTE: CSAM, child-safety grooming, and detailed terror-planning
    # patterns are NOT in this public file. They are loaded at runtime
    # from the curated private corpus (PR 4 mechanism). Including them
    # here would itself create a catalog. The framework handles them
    # via the same dispatch — only the pattern source differs.
]


# ── Pure consult ─────────────────────────────────────────────────────


def consult(message: str, *, embed_fn=None) -> PlatformSafetyVerdict:
    """Layered detection: tier 1 → tier 2.

    Pure function from the IO layer's perspective — no DB, no
    logging, no DB-backed audit. ``embed_fn`` is the only IO seam
    (the tier-2 embedding call), injectable for tests.

    Tier 1 runs unconditionally (regex, line-speed, ~1ms p99).
    Tier 2 runs ONLY when tier 1 misses AND the message hits a
    ``candidate_categories()`` pre-screen. ~10-30ms when it runs;
    skipped (~99% of turns) when the pre-screen misses.

    Returns the FIRST matching pattern's verdict at tier 1; the
    highest-similarity corpus hit above its threshold at tier 2.

    Empty/whitespace-only messages allow trivially.
    """
    if not message or not message.strip():
        return PlatformSafetyVerdict.allow()

    # Tier 1: regex pass
    for pattern, category, trigger_id in _TIER1_PATTERNS:
        if pattern.search(message):
            return PlatformSafetyVerdict.block(
                category=category,
                detection_tier=1,
                trigger_id=trigger_id,
            )

    # Tier 2: embedding pass (escalated only when pre-screen flags
    # the message as potentially sensitive). Catches the long tail
    # tier 1 misses. Empty corpus = no-op (the PR 4 default until
    # operators mount a curated corpus via PLATFORM_SAFETY_CORPUS_PATH).
    from app.services.platform_safety.tier2 import evaluate

    hit = evaluate(message, embed_fn=embed_fn)
    if hit.hit is not None:
        return PlatformSafetyVerdict.block(
            category=hit.hit.category,
            detection_tier=2,
            confidence=hit.confidence,
            trigger_id=hit.hit.trigger_id,
        )

    return PlatformSafetyVerdict.allow()


# ── Module exports ───────────────────────────────────────────────────


__all__ = [
    "PlatformSafetyVerdict",
    "consult",
]
