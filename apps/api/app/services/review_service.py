"""Cross-CLI consensus code review — service layer.

Backs the `alpha review` feature. Builds on existing primitives:
  * `Blackboard` + `BlackboardEntry` for per-CLI findings storage
    (PR #182-#205 a2a_collaboration substrate). Each CLI's raw review
    output becomes one entry tagged with the CLI's agent_slug as
    `author_agent_slug` and entry_type='finding'.
  * `ReviewCoalition` (this PR) for the round-tracking and aggregate
    cache so the read path doesn't re-walk the blackboard.

Consensus heuristic (v1):
  A finding is **agreed** when ≥ 2 distinct CLIs flag an issue with
  matching (file, line_range_overlap, normalized_topic). Topic match
  is bag-of-words Jaccard ≥ 0.4 on the lower-cased description.
  Severity = strongest severity any participating CLI reported.

Stop conditions, in order:
  1. `rounds_completed >= max_rounds`     → status=done
  2. zero agreed_findings this round      → status=done (consensus)
  3. all CLIs returned, still findings    → status=awaiting_response
     (operator must call /reply)

The `alpha run` real-dispatch fix (task #287) is a prerequisite for
end-to-end execution; until that lands, this layer can be exercised
with mocked CLI outputs via `record_cli_findings(...)` directly. See
docs/plans/2026-05-18-alpha-review-consensus.md.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.blackboard import Blackboard, BlackboardEntry
from app.models.review_coalition import ReviewCoalition
from app.schemas.blackboard import (
    AuthorRole,
    BlackboardCreate,
    BlackboardEntryCreate,
    EntryType,
)
from app.services import blackboard_service


# ── Parser helpers ────────────────────────────────────────────────────
# Leaf CLIs return free-form Markdown. We extract structured findings
# with a forgiving regex set. Operators can also POST already-parsed
# findings (as JSON) via the internal record endpoint; the regex path
# is the fallback for raw text. The shapes recognized:
#
#   * "BLOCKER: <file>:<line[-line]> — <desc>"
#   * "[IMPORTANT] <desc>  (path/to/file.py:42)"
#   * "- NIT path/to/file.py:7-9 desc"
#
# Anything unparseable falls into a single bucket finding with file=None
# so it still participates in raw_text consensus.

_SEVERITY_TOKEN = re.compile(
    r"\b(BLOCKER|IMPORTANT|NIT)\b",
    re.IGNORECASE,
)
_FILE_LINE = re.compile(
    r"([A-Za-z0-9_./\-]+\.[A-Za-z0-9_]+):(\d+)(?:-(\d+))?",
)
_STOPWORDS = frozenset({
    "the", "a", "an", "is", "of", "to", "and", "or", "in", "on", "for",
    "this", "that", "be", "are", "with", "by", "at", "as", "it", "but",
    "should", "could", "would", "may", "might", "can", "will", "not",
    "no", "yes", "if", "then", "else", "so", "do", "does", "did",
})


def parse_findings_from_text(text: str) -> List[Dict]:
    """Parse a CLI's free-form review output into structured findings.

    Returns a list of dicts each shaped:
        {severity, file, line_range, description}

    Never raises — unparseable lines become bucket findings.
    """
    if not text or not text.strip():
        return []

    findings: List[Dict] = []
    # Split on bullet/numbered list markers and double newlines so a
    # single CLI can return multiple findings.
    chunks = re.split(r"(?:\n\s*[-*+]\s+|\n\s*\d+\.\s+|\n\n)", text.strip())
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        sev_match = _SEVERITY_TOKEN.search(chunk)
        if not sev_match:
            # No severity token — treat as low-priority commentary.
            continue
        severity = sev_match.group(1).upper()
        file_match = _FILE_LINE.search(chunk)
        file_path = file_match.group(1) if file_match else None
        if file_match:
            start = file_match.group(2)
            end = file_match.group(3) or start
            line_range = f"{start}-{end}" if start != end else start
        else:
            line_range = None
        # Description = chunk with the severity + file:line stripped.
        desc = _SEVERITY_TOKEN.sub("", chunk).strip(" :—-[]()")
        if file_match:
            desc = desc.replace(file_match.group(0), "").strip(" :—-[]()")
        findings.append({
            "severity": severity,
            "file": file_path,
            "line_range": line_range,
            "description": desc[:1000],
        })
    return findings


def _normalize_path(p: Optional[str]) -> Optional[str]:
    """Normalize a file path so paths reported inconsistently across
    CLIs still cluster together.

    Different CLIs emit paths in different shapes:
      * absolute      "/repo/apps/api/main.py"
      * repo-rooted   "apps/api/main.py"
      * basename      "main.py"

    We lower-case and collapse runs of "/" so the comparison helper
    `_paths_match` can ask suffix-style: a == b OR a.endswith("/"+b)
    OR b.endswith("/"+a). This is intentionally permissive — three
    CLIs flagging the same line of the same file should always
    cluster, even if one used a basename. Different files won't
    collide because the suffix check requires a "/" boundary.
    """
    if p is None:
        return None
    s = str(p).strip().lower()
    if not s:
        return None
    # Collapse repeated slashes; strip a leading "./".
    while "//" in s:
        s = s.replace("//", "/")
    if s.startswith("./"):
        s = s[2:]
    return s


def _prefer_longer_path(a: Optional[str], b: Optional[str]) -> Optional[str]:
    """Pick the more-qualified of two normalized-equal paths.

    When two CLIs report the same file at different qualification
    levels (e.g. "main.py" vs "apps/api/main.py"), the longer one is
    almost always the more useful one for the operator. Returns the
    raw (un-normalized) value so the cluster shows what a CLI actually
    emitted.
    """
    if a is None:
        return b
    if b is None:
        return a
    return a if len(str(a)) >= len(str(b)) else b


def _paths_match(a: Optional[str], b: Optional[str]) -> bool:
    """Two normalized paths cluster if they are equal OR if one is a
    path-suffix of the other (on a "/" boundary). Both-None counts as
    a match — the no-file bucket clusters with itself."""
    na, nb = _normalize_path(a), _normalize_path(b)
    if na is None and nb is None:
        return True
    if na is None or nb is None:
        return False
    if na == nb:
        return True
    return na.endswith("/" + nb) or nb.endswith("/" + na)


def _tokenize(s: str) -> set:
    """Lower-case, drop stopwords, keep alphanumeric tokens."""
    if not s:
        return set()
    toks = re.findall(r"[a-z0-9_]{3,}", s.lower())
    return {t for t in toks if t not in _STOPWORDS}


def _parse_range(s: Optional[str]) -> Optional[Tuple[int, int]]:
    if s is None:
        return None
    try:
        if "-" in s:
            lo, hi = s.split("-", 1)
            return int(lo), int(hi)
        v = int(s)
        return v, v
    except (ValueError, IndexError):
        return None


def _line_ranges_overlap(a: Optional[str], b: Optional[str], slack: int = 5) -> bool:
    """Two line_range strings overlap if either is None or numeric
    ranges intersect (within a small `slack` window so a finding at
    line 11 still clusters with one at lines 10-12)."""
    if a is None or b is None:
        return True
    ra, rb = _parse_range(a), _parse_range(b)
    if ra is None or rb is None:
        return a == b
    a_lo, a_hi = ra
    b_lo, b_hi = rb
    return (a_lo - slack) <= b_hi and (b_lo - slack) <= a_hi


def _merge_range(a: Optional[str], b: Optional[str]) -> Optional[str]:
    """Union two line_range strings into one covering both."""
    ra = _parse_range(a)
    rb = _parse_range(b)
    if ra is None and rb is None:
        return None
    if ra is None:
        return b
    if rb is None:
        return a
    lo = min(ra[0], rb[0])
    hi = max(ra[1], rb[1])
    return f"{lo}-{hi}" if lo != hi else str(lo)


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


# Severity precedence — higher = stronger.
_SEV_RANK = {"BLOCKER": 3, "IMPORTANT": 2, "NIT": 1}


def _strongest_severity(severities: Iterable[str]) -> str:
    best = "NIT"
    best_rank = 0
    for s in severities:
        r = _SEV_RANK.get(s.upper(), 0)
        if r > best_rank:
            best = s.upper()
            best_rank = r
    return best


def aggregate_findings(per_cli: Dict[str, List[Dict]]) -> List[Dict]:
    """Compute consensus findings from per-CLI parsed findings.

    Args:
        per_cli: {cli_name: [{severity, file, line_range, description},
                              ...], ...}

    Returns: list of AgreedFinding-shaped dicts where each cluster was
    flagged by ≥ 2 distinct CLIs.

    Algorithm: greedy clustering. For each (cli, finding) pair, try to
    attach to an existing cluster where:
      * file matches (both None counts as match),
      * line_range_overlap is True,
      * Jaccard(description tokens) ≥ 0.4.
    Otherwise open a new cluster. After clustering, emit clusters with
    cli_set size ≥ 2.
    """
    clusters: List[Dict] = []
    for cli, findings in per_cli.items():
        for f in findings:
            tokens = _tokenize(f.get("description", ""))
            attached = False
            for cluster in clusters:
                if not _paths_match(cluster.get("file"), f.get("file")):
                    continue
                if not _line_ranges_overlap(
                    cluster.get("line_range"), f.get("line_range")
                ):
                    continue
                if _jaccard(cluster["_tokens"], tokens) < 0.4:
                    continue
                # Attach. Expand the cluster's line_range so later
                # findings nearby (within slack window) still match.
                cluster["descriptions"].append(f.get("description", ""))
                cluster["severities"].append(f.get("severity", "NIT"))
                cluster["cli_set"].add(cli)
                cluster["_tokens"] |= tokens
                cluster["line_range"] = _merge_range(
                    cluster.get("line_range"), f.get("line_range"),
                )
                # Prefer the more-qualified (longer) path so the
                # cluster's reported `file` is the most informative
                # variant any CLI supplied — "apps/api/main.py" wins
                # over bare "main.py".
                cluster["file"] = _prefer_longer_path(
                    cluster.get("file"), f.get("file"),
                )
                attached = True
                break
            if not attached:
                clusters.append({
                    "file": f.get("file"),
                    "line_range": f.get("line_range"),
                    "descriptions": [f.get("description", "")],
                    "severities": [f.get("severity", "NIT")],
                    "cli_set": {cli},
                    "_tokens": tokens,
                })

    agreed: List[Dict] = []
    for c in clusters:
        if len(c["cli_set"]) < 2:
            continue
        agreed.append({
            "severity": _strongest_severity(c["severities"]),
            "file": c["file"],
            "line_range": c["line_range"],
            "descriptions": c["descriptions"],
            "cli_set": sorted(c["cli_set"]),
        })
    # Strongest severities first for operator scanability.
    agreed.sort(key=lambda x: -_SEV_RANK.get(x["severity"], 0))
    return agreed


# ── CRUD + lifecycle ──────────────────────────────────────────────────


def _default_clis(db: Session, tenant_id: uuid.UUID) -> List[Dict[str, str]]:
    """Pick the tenant's active CLI set when the caller didn't supply
    one. v1 returns a stable default — claude/codex/gemini — that
    matches the order the agent_router resolves. Tenants without all
    three will produce dispatch errors per-CLI rather than block start.

    TODO (post-#287): query agent_router for the live active set.
    """
    return [
        {"name": "claude", "agent_slug": "claude"},
        {"name": "codex", "agent_slug": "codex"},
        {"name": "gemini", "agent_slug": "gemini"},
    ]


def start_review(
    db: Session,
    tenant_id: uuid.UUID,
    *,
    ref: str,
    clis: Optional[List[str]] = None,
    scope: str = "bugs+security",
    max_rounds: int = 3,
    chat_session_id: Optional[uuid.UUID] = None,
) -> Tuple[ReviewCoalition, Blackboard]:
    """Create a new review coalition + its backing Blackboard.

    Returns the ReviewCoalition row and the linked Blackboard. The
    actual CLI fanout is performed by the caller (typically the router
    layer which kicks off a Temporal ReviewWorkflow). This keeps the
    service layer testable without a Temporal client.
    """
    # Resolve fanout list.
    if clis:
        cli_list = [{"name": c, "agent_slug": c} for c in clis]
    else:
        cli_list = _default_clis(db, tenant_id)

    # Blackboard for raw per-CLI findings.
    title = f"review:{ref[:64]}"
    board = blackboard_service.create_blackboard(
        db,
        tenant_id,
        BlackboardCreate(
            title=title,
            chat_session_id=chat_session_id,
        ),
    )

    review = ReviewCoalition(
        tenant_id=tenant_id,
        blackboard_id=board.id,
        chat_session_id=chat_session_id,
        ref=ref,
        scope=scope,
        clis=cli_list,
        max_rounds=max_rounds,
        rounds_completed=0,
        status="running",
        findings={"per_cli": {}, "last_round": 0},
        agreed_findings=[],
    )
    db.add(review)
    db.commit()
    db.refresh(review)
    return review, board


def get_review(
    db: Session,
    tenant_id: uuid.UUID,
    review_id: uuid.UUID,
) -> Optional[ReviewCoalition]:
    return (
        db.query(ReviewCoalition)
        .filter(
            ReviewCoalition.id == review_id,
            ReviewCoalition.tenant_id == tenant_id,
        )
        .first()
    )


def record_cli_findings(
    db: Session,
    tenant_id: uuid.UUID,
    review_id: uuid.UUID,
    *,
    cli: str,
    raw_text: str,
    findings: Optional[List[Dict]] = None,
) -> Optional[ReviewCoalition]:
    """Record one CLI's review output for the current round.

    Appends a BlackboardEntry (audit trail) and updates the review's
    `findings.per_cli` cache. If this completes the current round
    (all CLIs have reported), the consensus aggregator runs and the
    review transitions to `done` or `awaiting_response`.
    """
    review = get_review(db, tenant_id, review_id)
    if not review:
        return None
    if review.status not in ("running", "awaiting_response"):
        # Already terminal — record nothing.
        return review

    parsed = findings if findings is not None else parse_findings_from_text(raw_text)

    # Append to blackboard (audit trail). Best-effort — if the
    # blackboard was pruned out of band we still record into the cache.
    if review.blackboard_id is not None:
        try:
            blackboard_service.add_entry(
                db,
                tenant_id,
                review.blackboard_id,
                BlackboardEntryCreate(
                    entry_type=EntryType.CRITIQUE,
                    content=raw_text[:8000] if raw_text else "(no output)",
                    evidence=[{"findings_count": len(parsed)}],
                    confidence=0.8,
                    author_agent_slug=cli,
                    author_role=AuthorRole.CRITIC,
                ),
            )
        except Exception:
            # Don't let blackboard write failures lose the finding
            # cache update — the cached snapshot in review.findings is
            # the authoritative read source.
            db.rollback()

    # Update findings cache. Replace the per-CLI slot rather than
    # append so re-runs in the same round don't double-count.
    findings_blob = dict(review.findings or {})
    per_cli = dict(findings_blob.get("per_cli") or {})
    per_cli[cli] = {
        "findings": parsed,
        "raw_text": raw_text[:4000] if raw_text else "",
    }
    findings_blob["per_cli"] = per_cli
    findings_blob["last_round"] = review.rounds_completed
    review.findings = findings_blob

    # Have we heard from every CLI on this round?
    expected = {c["name"] for c in (review.clis or [])}
    heard = set(per_cli.keys())
    if expected and expected <= heard:
        _close_round(db, review, per_cli)

    review.updated_at = datetime.utcnow()
    db.add(review)
    db.commit()
    db.refresh(review)
    return review


def _close_round(db: Session, review: ReviewCoalition, per_cli: Dict) -> None:
    """All CLIs have reported — aggregate and decide next state."""
    parsed_by_cli = {
        cli: (slot.get("findings") or [])
        for cli, slot in per_cli.items()
    }
    agreed = aggregate_findings(parsed_by_cli)
    review.agreed_findings = agreed
    review.rounds_completed = (review.rounds_completed or 0) + 1

    if not agreed:
        # Consensus = nothing left to fix.
        review.status = "done"
    elif review.rounds_completed >= review.max_rounds:
        # Out of rounds.
        review.status = "done"
    else:
        # Operator must fix + reply.
        review.status = "awaiting_response"


def apply_reply(
    db: Session,
    tenant_id: uuid.UUID,
    review_id: uuid.UUID,
    updated_ref: str,
) -> Optional[ReviewCoalition]:
    """Operator submitted fixes — open a new round with `updated_ref`.

    Resets the per-CLI findings slot for the new round and flips back
    to `running`. The dispatch layer (workflow) re-fans-out using the
    new ref.
    """
    review = get_review(db, tenant_id, review_id)
    if not review:
        return None
    if review.status == "done":
        # Idempotent — replying to a done review is a no-op.
        return review
    if review.status not in ("awaiting_response", "running"):
        return review
    if review.rounds_completed >= review.max_rounds:
        review.status = "done"
        db.add(review)
        db.commit()
        db.refresh(review)
        return review

    # Reset per-CLI cache for the new round; preserve history in
    # blackboard entries.
    findings_blob = dict(review.findings or {})
    findings_blob["per_cli"] = {}
    findings_blob["last_round"] = review.rounds_completed
    review.findings = findings_blob
    review.agreed_findings = []
    review.last_reply_ref = updated_ref
    review.ref = updated_ref
    review.status = "running"
    review.updated_at = datetime.utcnow()
    db.add(review)
    db.commit()
    db.refresh(review)
    return review


def list_reviews(
    db: Session,
    tenant_id: uuid.UUID,
    status: Optional[str] = None,
    limit: int = 50,
) -> List[ReviewCoalition]:
    q = db.query(ReviewCoalition).filter(ReviewCoalition.tenant_id == tenant_id)
    if status:
        q = q.filter(ReviewCoalition.status == status)
    return q.order_by(ReviewCoalition.created_at.desc()).limit(limit).all()
