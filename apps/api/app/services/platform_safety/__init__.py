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
        return {
            "decision": self.decision,
            "category": self.category,
            "detection_tier": self.detection_tier,
            "confidence": self.confidence,
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
    # NOTE: CSAM, child-safety grooming, and detailed terror-planning
    # patterns are NOT in this public file. They are loaded at runtime
    # from the curated private corpus (PR 4 mechanism). Including them
    # here would itself create a catalog. The framework handles them
    # via the same dispatch — only the pattern source differs.
]


# ── Pure consult ─────────────────────────────────────────────────────


def consult(message: str) -> PlatformSafetyVerdict:
    """Tier-1 regex screen.

    Pure function — no DB, no logging, no IO. The IO wrapper in
    ``platform_safety_io.py`` is responsible for audit recording +
    fail-open/closed policy on classifier crashes (tier 2+).

    Tier 1 is line-speed: returns in <1ms for typical messages.
    Empty/whitespace-only messages allow trivially.

    Returns the FIRST matching pattern's verdict. We don't continue
    scanning after the first match — a message that hits any tier-1
    pattern is already blocked.
    """
    if not message or not message.strip():
        return PlatformSafetyVerdict.allow()

    for pattern, category, trigger_id in _TIER1_PATTERNS:
        if pattern.search(message):
            return PlatformSafetyVerdict.block(
                category=category,
                detection_tier=1,
                trigger_id=trigger_id,
            )

    return PlatformSafetyVerdict.allow()


# ── Module exports ───────────────────────────────────────────────────


__all__ = [
    "PlatformSafetyVerdict",
    "consult",
]
