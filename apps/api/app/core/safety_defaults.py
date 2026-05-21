"""Platform Safety Floor — code-owned configuration.

Per Luna's design call (§12 #4), the fail-open/closed map and tier-3
enforcement flag MUST live in code, NOT the DB. Operators must not be
able to flip these via SQL or any UI surface. Changes require a PR +
deploy.

Design: docs/plans/2026-05-21-platform-safety-floor-design.md
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CategoryPolicy:
    """Per-category configuration for the platform safety floor.

    ``fail_closed`` — when True, a crash in the detection pipeline for
        this category MUST refuse the message. When False, a crash
        fails open (logged + chat proceeds). Reserved True for
        categories whose slip-through risk is existential to the
        company (CSAM, mass harm).

    ``tier3_enforcement`` — when True, tier-3 LLM-classifier blocks
        the message. When False (the default for first 14 days per
        §12 #7), tier-3 logs the would-have-blocked decision to
        ``platform_safety_events`` with ``enforcement_mode='shadow'``
        but lets the message through. The count-only operator view
        excludes shadow rows.

    ``human_readable`` — surfaced in the operator-facing refusal
        message ("...this looks like it may relate to <human_readable>")
        without revealing the specific trigger. Coarse-grained on
        purpose so attackers can't probe.
    """

    fail_closed: bool
    tier3_enforcement: bool
    human_readable: str


# Per-category platform-floor policy.
#
# Existential categories (CSAM / child safety / mass-harm synthesis /
# terrorism planning) are fail-closed: a buggy detector for these MUST
# refuse rather than let through. Slip-through risk is existential.
#
# Soft categories (election interference at scale, bulk malware,
# targeted doxing) are fail-open: a buggy detector should NOT brick
# the platform for legitimate users. Slip-through risk is reputational
# but recoverable.
#
# Every category ships with ``tier3_enforcement=False`` for the first
# 14 days per §12 #7 (Luna's shadow-mode call). After the precision
# audit clears >98%, this flips to True via a config-only deploy.
PLATFORM_SAFETY_CATEGORIES: dict[str, CategoryPolicy] = {
    "csam": CategoryPolicy(
        fail_closed=True,
        tier3_enforcement=False,
        human_readable="child safety",
    ),
    "child_safety": CategoryPolicy(
        fail_closed=True,
        tier3_enforcement=False,
        human_readable="child safety",
    ),
    "mass_harm_synthesis": CategoryPolicy(
        fail_closed=True,
        tier3_enforcement=False,
        human_readable="mass-harm content",
    ),
    "terrorism_planning": CategoryPolicy(
        fail_closed=True,
        tier3_enforcement=False,
        human_readable="violent extremism",
    ),
    "election_interference_bulk": CategoryPolicy(
        fail_closed=False,
        tier3_enforcement=False,
        human_readable="election integrity",
    ),
    "bulk_malware": CategoryPolicy(
        fail_closed=False,
        tier3_enforcement=False,
        human_readable="malware authoring",
    ),
    "targeted_doxing": CategoryPolicy(
        fail_closed=False,
        tier3_enforcement=False,
        human_readable="targeted privacy harm",
    ),
}


VALID_CATEGORIES = frozenset(PLATFORM_SAFETY_CATEGORIES.keys())


# The user-facing refusal message template. Renders the category's
# ``human_readable`` field but NOT the trigger phrase (§9 — false-
# positive UX).
REFUSAL_MESSAGE_TEMPLATE = (
    "I can't help with that — this looks like it may relate to "
    "{category_label}. If you believe this is a mistake, contact "
    "platform support."
)


def category_for_label(category: str) -> CategoryPolicy:
    """Lookup wrapper that raises a clear error on unknown categories.

    Helps catch typos at call sites — a misspelled category would
    otherwise default to fail-open + soft policy, which is the wrong
    failure mode for the floor.
    """
    if category not in PLATFORM_SAFETY_CATEGORIES:
        raise ValueError(
            f"unknown platform safety category {category!r}; "
            f"valid: {sorted(VALID_CATEGORIES)}"
        )
    return PLATFORM_SAFETY_CATEGORIES[category]


__all__ = [
    "CategoryPolicy",
    "PLATFORM_SAFETY_CATEGORIES",
    "VALID_CATEGORIES",
    "REFUSAL_MESSAGE_TEMPLATE",
    "category_for_label",
]
