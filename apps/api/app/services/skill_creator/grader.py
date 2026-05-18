"""Service-layer grader for the skill-creator framework.

Given a transcript + outputs directory + a list of expectations, produces a
``GradingResult`` matching ``grading.json`` from ``docs/skill-creator/schemas.md``.

Phase 1 grades every run on the local Ollama floor (``gemma3:4b``) regardless
of the tenant's ``default_cli_platform``. The ``grader_model`` field on the
result records *literally* what ``local_inference.generate_sync`` was called
with — that's the contract reproducibility relies on. Routing the grader
through ``agent_router`` so each tenant grades on their preferred CLI is
deferred to Phase 4 (see open question #5 in the design doc); when it lands
the recorded ``grader_model`` will switch from the local tag to whatever the
CLI dispatch actually invoked, and re-grades will be honestly comparable
because the label always names the model that produced the verdicts.

Failure model
-------------

Phase 1 treats grader output as all-or-nothing: if the LLM returns at least
one well-formed verdict, the grader pairs each expectation with its verdict
(missing ids default to ``passed=false`` with a "did not return a verdict"
reasoning, which is a per-expectation fallback) and returns a partial-pass
result. If the LLM returns NO parseable verdicts at all — unavailable,
garbled output, empty response — the grader raises ``GraderError`` so the
caller can return a 503 instead of a misleading "all failed" 200.

Per-expectation parsing fallback (e.g. one verdict's JSON is malformed →
mark just that one failed without raising) is a future enhancement. It
would require a schema change to carry parse-error reasons per expectation
and is deferred to a later phase.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────
# Schemas — mirrors docs/skill-creator/schemas.md grading.json shape.
# Pydantic models keep the public API self-documenting and let the endpoint
# return them directly via FastAPI's response_model serialization.
# ──────────────────────────────────────────────────────────────────────────


class Expectation(BaseModel):
    """One assertion the grader is asked to judge.

    ``kind`` is a hint only — the grader treats both kinds with the same
    prompt template in Phase 1 (the design doc tracks structured-parsing as
    a Phase 3 enhancement).
    """

    id: str
    description: str
    kind: Literal["assertion", "structured"] = "assertion"


class GradedExpectation(BaseModel):
    """One expectation after grading. Mirrors grading.json::expectations[]."""

    id: str
    description: str
    passed: bool
    reasoning: str


class GradingResult(BaseModel):
    """Full grading.json payload."""

    version: int = 1
    eval_id: str
    run_id: str
    graded_at: str  # RFC 3339 UTC
    grader_model: str
    score: float = Field(..., ge=0.0, le=1.0)
    passed: bool
    expectations: List[GradedExpectation]
    # Count of input expectations that were malformed and dropped before
    # grading (see ``_validate_expectations``). Surfaced so callers can
    # show "2 of your expectations were malformed" without scraping logs.
    dropped_expectations: int = 0


# ──────────────────────────────────────────────────────────────────────────
# Errors
# ──────────────────────────────────────────────────────────────────────────


class GraderError(RuntimeError):
    """Raised when the grader cannot produce any usable verdict.

    The caller turns this into a 503/500 — never a 200 with a bogus score.
    """


# ──────────────────────────────────────────────────────────────────────────
# Grader model
#
# Phase 1 always grades on the local Ollama floor. The ``grader_model``
# field on every result records *literally* what the grader called —
# no per-tenant label rewriting. Phase 4 will replace this constant with
# an ``agent_router`` dispatch that picks per tenant; the label will then
# come from the dispatched CLI's actual model id, so re-grade reproducibility
# is preserved because the label always matches the engine that voted.
# ──────────────────────────────────────────────────────────────────────────


# Local Ollama tag the grader actually calls in Phase 1. Cheap to swap to a
# stronger local model later; we keep it small here so grader latency is
# bounded on the dev machine.
_DEFAULT_LOCAL_MODEL_TAG = "gemma3:4b"


# ──────────────────────────────────────────────────────────────────────────
# Prompt + parser
# ──────────────────────────────────────────────────────────────────────────


_GRADER_SYSTEM_PROMPT = (
    "You are a strict skill evaluator. Given a transcript of a model's response "
    "to a user prompt, plus a list of expectations the response should meet, "
    "produce a JSON object that names which expectations passed.\n\n"
    "Reply ONLY with valid JSON in this exact shape:\n"
    '{"expectations": [{"id": "<id>", "passed": <true|false>, "reasoning": "<one to three sentences>"}, ...]}\n\n'
    "Rules:\n"
    "- Judge each expectation independently — don't let one pass/fail bias others.\n"
    "- ``reasoning`` must cite the transcript content that justified the verdict.\n"
    "- If the transcript doesn't address an expectation at all, mark it failed and say so.\n"
    "- Don't echo the expectation description back as the reasoning — explain WHY in terms of the transcript."
)


def _build_grader_prompt(
    transcript: str,
    outputs_summary: str,
    expectations: List[Expectation],
) -> str:
    expects_block = "\n".join(
        f"- id={e.id} | kind={e.kind} | {e.description}" for e in expectations
    )
    return (
        f"## Transcript\n\n{transcript.strip()}\n\n"
        f"## Output files\n\n{outputs_summary}\n\n"
        f"## Expectations to grade\n\n{expects_block}\n\n"
        "Return the JSON now."
    )


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _extract_json(text: str) -> Optional[dict]:
    """Tolerant JSON extraction.

    Tries (1) raw parse, (2) inside ```json fenced block, (3) first balanced
    ``{...}`` substring. Returns None if nothing parses — the caller treats
    that as "grader gave us nothing" and falls back per-expectation.
    """
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass

    m = _JSON_FENCE_RE.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass

    # First balanced object — handles "Here is the JSON: {...}" preambles.
    start = text.find("{")
    if start >= 0:
        depth = 0
        for i in range(start, len(text)):
            c = text[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except Exception:
                        return None
    return None


def _summarize_outputs(outputs_dir: Optional[Path]) -> str:
    """One-line-per-file summary of the outputs directory.

    The grader doesn't read file bodies in Phase 1 — that would blow up the
    prompt for any non-trivial skill. It just gets a manifest so it can name
    files in its reasoning ("the output file `result.json` had ...").
    """
    if outputs_dir is None:
        return "(no outputs directory provided)"
    try:
        p = Path(outputs_dir)
    except Exception:
        return "(invalid outputs path)"
    if not p.exists() or not p.is_dir():
        return f"(outputs dir not found: {p})"

    lines: List[str] = []
    for entry in sorted(p.rglob("*")):
        if entry.is_file():
            try:
                size = entry.stat().st_size
            except OSError:
                size = -1
            lines.append(f"- {entry.relative_to(p).as_posix()} ({size} bytes)")
    if not lines:
        return "(outputs dir is empty)"
    return "\n".join(lines)


def _validate_expectations(raw: List) -> tuple[List[Expectation], int]:
    """Coerce caller-supplied list-of-dicts into Expectation objects.

    Drops entries that don't match the schema instead of raising — a malformed
    single expectation shouldn't 500 the grader. Returns ``(cleaned, dropped)``
    so the grader can surface a count of dropped expectations in the response
    payload (``GradingResult.dropped_expectations``).
    """
    cleaned: List[Expectation] = []
    dropped = 0
    for item in raw:
        if isinstance(item, Expectation):
            cleaned.append(item)
            continue
        if not isinstance(item, dict):
            logger.warning("grader: dropping non-dict expectation: %r", item)
            dropped += 1
            continue
        try:
            cleaned.append(Expectation(**item))
        except ValidationError as exc:
            logger.warning(
                "grader: dropping malformed expectation %r: %s", item, exc
            )
            dropped += 1
    return cleaned, dropped


# ──────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────


def grade(
    transcript: str,
    outputs_dir: Optional[Path],
    expectations: List,
    *,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
    eval_id: str = "",
    run_id: str = "",
    db=None,
) -> GradingResult:
    """Grade a single run against its expectations.

    Args:
        transcript: The full transcript (assistant + user turns + tool calls)
            of the run being graded. Plain text; markdown is fine.
        outputs_dir: Path to the outputs directory the run produced. May be
            None (the grader will note "no outputs" in the prompt).
        expectations: List of ``Expectation`` (or raw dicts the grader will
            coerce) to judge. Malformed entries are dropped with a log.
        tenant_id: Used to look up the tenant's default CLI platform so the
            grader records which model graded.
        session_id: Used for log correlation. Phase 1 doesn't read it past
            logging — Phase 4+ stores grading rows tagged with the session
            that authored the eval, so we accept it now to avoid signature
            churn later.
        eval_id: Foreign key into ``skill_evals.id``. Echoed in the result.
        run_id: Foreign key into ``skill_eval_runs.id``. Echoed in the
            result. Defaults to empty string so callers grading ad-hoc
            transcripts (CLI / unit-test use) aren't forced to invent one.
        db: SQLAlchemy session, kept on the signature for Phase 4 routing
            but currently unused. Phase 1 grades every run on the local
            ``gemma3:4b`` floor regardless of tenant preference.

    Returns:
        GradingResult mirroring ``docs/skill-creator/schemas.md``::
        ``grading.json``.

    Raises:
        GraderError: when the LLM is unavailable AND no expectations could
            be graded. A partial grade (some expectations parsed, others
            fell back to ``passed=false``) is returned without raising.
    """
    cleaned, dropped = _validate_expectations(expectations)
    if not cleaned:
        # Caller asked us to grade against zero usable expectations. Return
        # a zero-length grading rather than raising so the endpoint can
        # surface "all your expectations were malformed" via the run row.
        return _empty_result(
            eval_id=eval_id,
            run_id=run_id,
            grader_model=_DEFAULT_LOCAL_MODEL_TAG,
            dropped_expectations=dropped,
        )

    grader_model_label = _DEFAULT_LOCAL_MODEL_TAG

    outputs_summary = _summarize_outputs(outputs_dir)
    prompt = _build_grader_prompt(transcript, outputs_summary, cleaned)

    # Late import keeps grader importable in environments without httpx
    # (test collection on machines without the full dev deps).
    from app.services import local_inference

    try:
        raw = local_inference.generate_sync(
            prompt=prompt,
            model=_DEFAULT_LOCAL_MODEL_TAG,
            system=_GRADER_SYSTEM_PROMPT,
            temperature=0.0,
            max_tokens=1500,
            timeout=60.0,
            response_format="json",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "grader: local_inference raised — tenant=%s session=%s: %s",
            tenant_id, session_id, exc,
        )
        raw = None

    parsed = _extract_json(raw) if raw else None
    verdicts: dict[str, dict] = {}
    if isinstance(parsed, dict):
        for item in parsed.get("expectations", []) or []:
            if isinstance(item, dict) and item.get("id"):
                verdicts[str(item["id"])] = item

    if not verdicts:
        # The grader produced no parseable verdicts AT ALL — outage,
        # empty response, or fully unparseable JSON. Raise so the endpoint
        # returns 503 instead of a misleading "all failed" 200. Per-
        # expectation parsing fallback (one bad verdict among many) is a
        # future enhancement; see the module docstring.
        logger.warning(
            "grader: no verdicts parsed for tenant=%s session=%s",
            tenant_id, session_id,
        )
        raise GraderError("grader returned no parseable verdicts")

    graded: List[GradedExpectation] = []
    for exp in cleaned:
        v = verdicts.get(exp.id)
        if v is None:
            graded.append(GradedExpectation(
                id=exp.id,
                description=exp.description,
                passed=False,
                reasoning="Grader did not return a verdict for this expectation.",
            ))
            continue
        graded.append(GradedExpectation(
            id=exp.id,
            description=exp.description,
            passed=bool(v.get("passed", False)),
            reasoning=str(v.get("reasoning") or "(no reasoning provided)").strip(),
        ))

    score = sum(1 for g in graded if g.passed) / len(graded)
    return GradingResult(
        version=1,
        eval_id=eval_id,
        run_id=run_id,
        graded_at=datetime.now(tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        grader_model=grader_model_label,
        score=round(score, 4),
        # ``passed`` derives from the per-expectation verdicts, not the
        # aggregate score — same outcome semantically (1.0 iff all pass)
        # but immune to float-rounding edge cases and clearer to readers.
        passed=all(g.passed for g in graded),
        expectations=graded,
        dropped_expectations=dropped,
    )


def _empty_result(
    *,
    eval_id: str,
    run_id: str,
    grader_model: str,
    dropped_expectations: int = 0,
) -> GradingResult:
    """Zero-expectation grading payload (score 0.0, passed False).

    Score is 0.0 by convention — there's nothing to pass, so the run can't
    have passed. The endpoint surfaces this with a 200 so the caller can
    still persist the row and fix their expectations.
    """
    return GradingResult(
        version=1,
        eval_id=eval_id,
        run_id=run_id,
        graded_at=datetime.now(tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        grader_model=grader_model,
        score=0.0,
        passed=False,
        expectations=[],
        dropped_expectations=dropped_expectations,
    )
