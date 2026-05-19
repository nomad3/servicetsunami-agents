"""PAD (Pleasure-Arousal-Dominance) emotion schema.

Phase 1 PR A — schema only, no callers yet. See
docs/plans/2026-05-19-emotions-engine-prototype-design.md.

Three dimensions, each in [-1, 1]:
- pleasure: valence, positive vs. negative
- arousal:  intensity, calm vs. activated
- dominance: agency, submissive vs. dominant

The label is a derive-on-read helper that maps to
luna_presence_service.VALID_MOODS for compatibility with the four
existing readers of the legacy `mood String(30)` column. The label is
also persisted on the JSONB so downstream consumers don't have to
re-derive it on every read.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional


# Hard bounds. Inputs outside [-1, 1] are clamped, not rejected — the
# appraise / decay math can transiently overshoot.
PAD_MIN = -1.0
PAD_MAX = 1.0


def _clamp(value: float) -> float:
    if value < PAD_MIN:
        return PAD_MIN
    if value > PAD_MAX:
        return PAD_MAX
    return value


@dataclass(frozen=True)
class PADVector:
    """Immutable PAD vector with derive-on-read mood label.

    Construct via the from_* factories — the constructor clamps to
    [-1, 1] but callers should generally rely on EmotionEngine.appraise /
    EmotionEngine.decay rather than instantiating directly.
    """

    pleasure: float
    arousal: float
    dominance: float
    label: str
    updated_at: str  # ISO 8601 UTC

    def __post_init__(self) -> None:
        # Frozen dataclass — go through object.__setattr__ to clamp.
        object.__setattr__(self, "pleasure", _clamp(self.pleasure))
        object.__setattr__(self, "arousal", _clamp(self.arousal))
        object.__setattr__(self, "dominance", _clamp(self.dominance))

    # ── Factories ─────────────────────────────────────────────────────

    @classmethod
    def neutral(cls) -> "PADVector":
        """Flat neutral baseline. Used for agents without a personalised
        affect_baseline yet (Phase 1 default — Phase 2 adds
        persona-derived seeding)."""
        return cls.from_components(pleasure=0.0, arousal=0.0, dominance=0.0)

    @classmethod
    def from_components(
        cls,
        pleasure: float,
        arousal: float,
        dominance: float,
        *,
        now: Optional[datetime] = None,
    ) -> "PADVector":
        p, a, d = _clamp(pleasure), _clamp(arousal), _clamp(dominance)
        label = _pad_to_mood_label(p, a, d)
        ts = (now or datetime.now(timezone.utc)).isoformat()
        return cls(pleasure=p, arousal=a, dominance=d, label=label, updated_at=ts)

    @classmethod
    def from_dict(cls, data: dict) -> "PADVector":
        """Hydrate from the JSONB column. Tolerant of missing label /
        updated_at so older rows (none exist in Phase 1 PR A, but
        defensive) don't crash."""
        p = float(data.get("pleasure", 0.0))
        a = float(data.get("arousal", 0.0))
        d = float(data.get("dominance", 0.0))
        return cls.from_components(p, a, d)

    # ── Serialisation ─────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return asdict(self)

    # ── Convenience ───────────────────────────────────────────────────

    def is_near_origin(self, eps: float = 0.15) -> bool:
        """Approximate test for "near-neutral". Used by the label mapper
        to pick `neutral` over a discrete-corner label when the vector
        has no clear corner. Eps tuned by design doc § "style mapping
        returns the expected discrete-corner label for each PAD octant"
        — small enough that the eight corners stay distinct."""
        return (
            abs(self.pleasure) < eps
            and abs(self.arousal) < eps
            and abs(self.dominance) < eps
        )


# ── Mood label derivation ─────────────────────────────────────────────
#
# Maps a PAD point to one of luna_presence_service.VALID_MOODS:
#     {"calm", "warm", "playful", "serious", "empathetic", "neutral"}
#
# Eight octants → six labels (some octants collapse).
#
#   High P, low A, high D → "calm"        (composed, in control)
#   High P, low A, low D  → "warm"        (relaxed, friendly)
#   High P, high A, *     → "playful"     (excited, regardless of D)
#   Low P, high A, low D  → "empathetic"  (concerned, deferential)
#   Low P, low A, low D   → "empathetic"  (sad/contemplative)
#   Low P, low A, high D  → "serious"     (stern)
#   Low P, high A, high D → "serious"     (irritated, dominant)
#   Near origin           → "neutral"


def _pad_to_mood_label(pleasure: float, arousal: float, dominance: float) -> str:
    """See module docstring. Pure function; no dependency on PADVector
    so the migration / model layer can call it without circular import."""
    if (
        abs(pleasure) < 0.15
        and abs(arousal) < 0.15
        and abs(dominance) < 0.15
    ):
        return "neutral"

    if pleasure >= 0:
        if arousal >= 0:
            return "playful"
        return "calm" if dominance >= 0 else "warm"
    # pleasure < 0
    if dominance >= 0:
        return "serious"
    return "empathetic"
