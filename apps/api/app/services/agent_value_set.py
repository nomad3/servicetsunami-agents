"""Pure value-layer matching engine — PR 1 of #647.

Per docs/plans/2026-05-21-luna-value-layer-design.md, this module
deliberately holds NO side effects:

  - No DB calls.
  - No logging beyond stdlib at module-level (the wrapper logs
    verdicts; this module produces them).
  - No hidden state — every input is in the function signature.

The IO wrapper in ``agent_value_set_io.py`` is what every
production call-site uses: it reads the kill-switch + value-set
from the DB, calls ``consult()`` here, and records the verdict
to the audit log.

Splitting purity from IO is Luna's round-4 review correction —
it keeps ``consult()`` unit-testable with zero fixtures.

The five consultation points (routing, tool, reflection,
user_signal, synthesis) each call ``consult()`` with their own
``point`` label. The matching logic is identical across points;
the label is only carried through for the verdict + audit log.

``protect`` matching is mutation-aware (design §4.2 + §6 round-1
correction): a ``protect`` hit returns ``block`` ONLY when
``intent='mutate'``. Read/mention intents always pass. Otherwise
Luna would deadlock around the very things she's supposed to
safeguard.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


# ── Public types ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class ValueItem:
    """One named value (protect / pursue / avoid).

    The slug is the match key (lowercased substring against the
    action text/tool args/reflection content). ``description``
    is human-readable context shown in audit logs + the operator
    UI. ``evidence_memory_ids`` carries the chain of agent_memory
    rows that justify this value's existence — used by the
    reflection-derived proposal mechanism in Phase 2.

    (Luna review-round 6) __post_init__ normalizes the slug at
    construction so direct ``ValueItem(slug='Production-Main')``
    matches the from_dict path's behavior. Previously only
    from_dict lowercased, leaving slug-case asymmetry between
    operator-API writes (via dict) and unit-test fixtures (via
    constructor).
    """

    slug: str
    description: str
    added_at: str
    added_by: str  # 'operator' | 'reflection' | 'seed'
    evidence_memory_ids: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        # frozen=True forbids attribute assignment, so we go through
        # object.__setattr__ to normalize the slug in place. Same
        # technique the metacog dataclass uses (#617).
        normalized = str(self.slug or "").strip().lower()
        object.__setattr__(self, "slug", normalized)

    def to_dict(self) -> dict:
        return {
            "slug": self.slug,
            "description": self.description,
            "added_at": self.added_at,
            "added_by": self.added_by,
            "evidence_memory_ids": list(self.evidence_memory_ids),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ValueItem":
        return cls(
            slug=str(data.get("slug", "")).strip().lower(),
            description=str(data.get("description", "")),
            added_at=str(data.get("added_at", "")),
            added_by=str(data.get("added_by", "operator")),
            evidence_memory_ids=list(data.get("evidence_memory_ids") or []),
        )


@dataclass(frozen=True)
class AgentValueSet:
    """Per-(tenant, agent) value set. Three named sets + version +
    timestamp. Read by ``read_value_set`` in the IO wrapper; written
    via append-only INSERT (see §4.1)."""

    protect: List[ValueItem] = field(default_factory=list)
    pursue: List[ValueItem] = field(default_factory=list)
    avoid: List[ValueItem] = field(default_factory=list)
    version: int = 1
    updated_at: str = ""
    # Break-glass metadata (#647 PR 6). When set, this version is a
    # time-boxed operator override that auto-expires. read_value_set
    # walks back to the prior non-expired version once expires_at < now.
    # None on ordinary versions.
    expires_at: Optional[str] = None
    break_glass_reason: Optional[str] = None
    break_glass_operator_id: Optional[str] = None

    @classmethod
    def empty(cls) -> "AgentValueSet":
        """The default for any (tenant, agent) without an opt-in
        seed. Locked test: every consult() against this returns
        allow/no_match."""
        return cls()

    def is_empty(self) -> bool:
        return not (self.protect or self.pursue or self.avoid)

    def is_break_glass(self) -> bool:
        """True when this version is an operator break-glass override
        (has expires_at metadata). The pure layer doesn't decide
        expiration — that's the IO layer's job (it knows wall-clock
        time). consult() treats a break-glass version structurally
        identical to any other version, so the match logic stays pure
        + DB-clock-free."""
        return self.expires_at is not None

    def to_dict(self) -> dict:
        d = {
            "protect": [i.to_dict() for i in self.protect],
            "pursue": [i.to_dict() for i in self.pursue],
            "avoid": [i.to_dict() for i in self.avoid],
            "version": self.version,
            "updated_at": self.updated_at,
        }
        # Only emit break-glass fields when set, to keep ordinary
        # versions' JSON tidy + back-compat with existing rows.
        if self.expires_at is not None:
            d["expires_at"] = self.expires_at
        if self.break_glass_reason is not None:
            d["break_glass_reason"] = self.break_glass_reason
        if self.break_glass_operator_id is not None:
            d["break_glass_operator_id"] = self.break_glass_operator_id
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "AgentValueSet":
        return cls(
            protect=[ValueItem.from_dict(d) for d in data.get("protect") or []],
            pursue=[ValueItem.from_dict(d) for d in data.get("pursue") or []],
            avoid=[ValueItem.from_dict(d) for d in data.get("avoid") or []],
            version=int(data.get("version", 1)),
            updated_at=str(data.get("updated_at", "")),
            expires_at=(
                str(data["expires_at"]) if data.get("expires_at") else None
            ),
            break_glass_reason=(
                str(data["break_glass_reason"])
                if data.get("break_glass_reason") else None
            ),
            break_glass_operator_id=(
                str(data["break_glass_operator_id"])
                if data.get("break_glass_operator_id") else None
            ),
        )


@dataclass(frozen=True)
class ValueVerdict:
    """The result of one ``consult()`` call.

    ``decision``:
      - 'allow' — proceed normally
      - 'warn'  — proceed but record the warning + surface in
                  reflection log (used for ``avoid`` matches and
                  for ``protect`` matches with intent='read')
      - 'block' — refuse the action (only ``protect`` + intent='mutate')

    ``matched_item`` is the ValueItem dict that triggered the
    verdict (None for ``allow``). Lets the audit log + operator UI
    show "which value blocked this."
    """

    decision: str  # 'allow' | 'warn' | 'block'
    reason: str
    matched_item: Optional[dict]
    consultation_point: str  # routing | tool | reflection | user_signal | synthesis

    @classmethod
    def allow(cls, *, reason: str, point: str) -> "ValueVerdict":
        return cls("allow", reason, None, point)

    @classmethod
    def warn(cls, *, reason: str, point: str, item: dict) -> "ValueVerdict":
        return cls("warn", reason, item, point)

    @classmethod
    def block(cls, *, reason: str, point: str, item: dict) -> "ValueVerdict":
        return cls("block", reason, item, point)

    def to_dict(self) -> dict:
        return {
            "decision": self.decision,
            "reason": self.reason,
            "matched_item": self.matched_item,
            "consultation_point": self.consultation_point,
        }


# ── Constants ─────────────────────────────────────────────────────────

# Exported as VALID_CONSULTATION_POINTS so external callers (shims
# in the IO wrapper, future Phase 2 callers) can validate their
# point string before passing. Single source of truth.
VALID_CONSULTATION_POINTS = frozenset({
    "routing", "tool", "reflection", "user_signal", "synthesis",
})
VALID_INTENTS = frozenset({"read", "mutate"})

# Internal aliases (kept for backward compat within this module).
_VALID_POINTS = VALID_CONSULTATION_POINTS
_VALID_INTENTS = VALID_INTENTS


# ── Matching ──────────────────────────────────────────────────────────


_WALK_MAX_DEPTH = 4
"""Walker depth cap. Real-world action shapes:
- routing/user_signal: depth 1 (string at depth 1 from root dict).
- tool: depth 3 (root dict → args dict → list → string).
- reflection/synthesis: depth 1 (kind + content strings).

Cap at 4 to safely handle one more level of MCP-tool args nesting
than the deepest observed shape. Adversarially deep dicts are
short-circuited at the cap (review B-tier consideration: a
deliberately-deep adversarial action MUST NOT force an unbounded
walk). Phase 2 may swap the slug substring match for embedding-
cosine; depth becomes moot then."""


def _extract_search_text(action: dict) -> str:
    """Build the lowercased text we search against value-item slugs.

    ``action`` shape varies by consultation point:
      - routing:    {text: <intent text>}
      - tool:       {tool: <name>, args: <dict-or-list>}
      - reflection: {kind: <kind>, content: <text>}
      - user_signal:{text: <user message>}
      - synthesis:  {kind: 'value_proposal', content: <text>}

    Recursive walk into dicts + lists/tuples; strings get
    captured at any depth ≤ ``_WALK_MAX_DEPTH``. Tool action shape
    requires depth ≥ 3 (root dict → args dict → list → string);
    cap at 4 to leave one level of safety margin for nested
    MCP tool args.
    """
    parts: list[str] = []

    def _walk(node: Any, depth: int = 0) -> None:
        if depth > _WALK_MAX_DEPTH:
            return
        if isinstance(node, str):
            parts.append(node)
        elif isinstance(node, dict):
            for v in node.values():
                _walk(v, depth + 1)
        elif isinstance(node, (list, tuple)):
            for v in node:
                _walk(v, depth + 1)

    _walk(action)
    return " ".join(parts).lower()


def _match_items(items: List[ValueItem], search_text: str) -> Optional[ValueItem]:
    """Return the first ValueItem whose slug appears as a substring
    of search_text. Uses simple ``in`` matching — Phase 2 may swap
    for embedding-cosine + slug expansion.

    First-match-wins is intentional: operator orders the items by
    priority when writing the value set."""
    if not search_text:
        return None
    for item in items:
        slug = item.slug.strip().lower()
        if not slug:
            continue
        if slug in search_text:
            return item
    return None


# ── Public consult API ────────────────────────────────────────────────


def consult(
    action: dict,
    value_set: AgentValueSet,
    *,
    point: str,
    intent: str,
    enabled: bool,
) -> ValueVerdict:
    """Pure value-layer match.

    Args:
        action: per-point payload (see ``_extract_search_text``).
        value_set: the (tenant, agent)'s current value set.
        point: one of routing|tool|reflection|user_signal|synthesis.
        intent: 'read' or 'mutate'. Determines whether a protect
                match blocks or warns.
        enabled: kill-switch state. When False every call returns
                 allow/kill_switch_off — the wrapper passes this
                 from ``is_value_layer_enabled``.

    Returns:
        ValueVerdict.

    Raises:
        ValueError on unknown point or intent. Locked test —
        garbage inputs from the wrapper are a programmer error,
        not a runtime concern.

    Locked properties:
      - Pure: identical inputs → identical output.
      - Empty value-set → always allow/empty_value_set.
      - enabled=False → always allow/kill_switch_off.
      - protect + intent='mutate' → block.
      - protect + intent='read' → warn (mention/read is fine but
        operator should see it in the audit log).
      - avoid + any intent → warn (locked decision Q4 round-1).
      - pursue + any intent → allow with the matched_item set
        (so the caller can scale affect delta on pursue hits).
    """
    if point not in _VALID_POINTS:
        raise ValueError(
            f"consult: unknown consultation_point {point!r}; "
            f"must be one of {sorted(_VALID_POINTS)}"
        )
    if intent not in _VALID_INTENTS:
        raise ValueError(
            f"consult: unknown intent {intent!r}; "
            f"must be one of {sorted(_VALID_INTENTS)}"
        )

    if not enabled:
        return ValueVerdict.allow(reason="kill_switch_off", point=point)

    if value_set.is_empty():
        return ValueVerdict.allow(reason="empty_value_set", point=point)

    search_text = _extract_search_text(action)

    # Order: protect first (safety), then avoid (warn), then pursue
    # (informational). First match in each tier wins.
    hit = _match_items(value_set.protect, search_text)
    if hit is not None:
        if intent == "mutate":
            return ValueVerdict.block(
                reason=f"protect_match: {hit.slug}",
                point=point,
                item=hit.to_dict(),
            )
        return ValueVerdict.warn(
            reason=f"protect_match_read_only: {hit.slug}",
            point=point,
            item=hit.to_dict(),
        )

    hit = _match_items(value_set.avoid, search_text)
    if hit is not None:
        return ValueVerdict.warn(
            reason=f"avoid_match: {hit.slug}",
            point=point,
            item=hit.to_dict(),
        )

    hit = _match_items(value_set.pursue, search_text)
    if hit is not None:
        # pursue produces an allow verdict but with the matched_item
        # populated so the caller (e.g. appraise_user_signal_with_values)
        # can scale its PAD delta. Phase 2's value_proposal synthesis
        # also keys off this.
        return ValueVerdict(
            decision="allow",
            reason=f"pursue_match: {hit.slug}",
            matched_item=hit.to_dict(),
            consultation_point=point,
        )

    return ValueVerdict.allow(reason="no_match", point=point)


__all__ = [
    "AgentValueSet",
    "ValueItem",
    "ValueVerdict",
    "consult",
    "VALID_CONSULTATION_POINTS",
    "VALID_INTENTS",
]
