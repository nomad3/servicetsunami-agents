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


def clamp_pad(value: float) -> float:
    """Clamp a single PAD component to [-1, 1]."""
    if value < PAD_MIN:
        return PAD_MIN
    if value > PAD_MAX:
        return PAD_MAX
    return value


# Backwards-compatible alias for legacy callers; new code uses clamp_pad.
_clamp = clamp_pad


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
        object.__setattr__(self, "pleasure", clamp_pad(self.pleasure))
        object.__setattr__(self, "arousal", clamp_pad(self.arousal))
        object.__setattr__(self, "dominance", clamp_pad(self.dominance))

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
        p, a, d = clamp_pad(pleasure), clamp_pad(arousal), clamp_pad(dominance)
        label = _pad_to_mood_label(p, a, d)
        ts = (now or datetime.now(timezone.utc)).isoformat()
        return cls(pleasure=p, arousal=a, dominance=d, label=label, updated_at=ts)

    @classmethod
    def from_dict(cls, data: dict) -> "PADVector":
        """Hydrate from the JSONB column. Tolerant of missing label /
        updated_at so older rows (none exist in Phase 1 PR A, but
        defensive) don't crash.

        Note: `label` and `updated_at` in the input are intentionally
        discarded — both are re-derived from the PAD components. If
        you've manually edited a stored JSONB blob to set a label, that
        edit is overwritten on hydrate. The label is a pure function of
        the vector, so any divergence is a bug to detect, not a value
        to preserve.
        """
        p = float(data.get("pleasure", 0.0))
        a = float(data.get("arousal", 0.0))
        d = float(data.get("dominance", 0.0))
        return cls.from_components(p, a, d)

    # ── Serialisation ─────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return asdict(self)

# Eps used by the label mapper to classify "near-origin" as `neutral`
# rather than picking a corner. Tuned so the 8 octants stay distinct.
NEAR_ORIGIN_EPS = 0.15


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
        abs(pleasure) < NEAR_ORIGIN_EPS
        and abs(arousal) < NEAR_ORIGIN_EPS
        and abs(dominance) < NEAR_ORIGIN_EPS
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
