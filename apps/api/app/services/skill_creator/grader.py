"""Service-layer grader for the skill-creator framework.

Given a transcript + outputs directory + a list of expectations, produces a
``GradingResult`` matching ``grading.json`` from ``docs/skill-creator/schemas.md``.

The grader dispatches to the LLM configured for the tenant (their
``default_cli_platform``) by mapping the platform name to a concrete model id
and calling through the existing local-inference fast path. The lookup mirrors
the resolution pattern in ``apps/api/app/services/agent_router.py`` so the
grader honors the same per-tenant model selection that chat turns use.

For Phase 1 the grader does NOT actually shell out to the tenant's CLI binary
— it calls ``local_inference.generate_sync`` with the resolved model id. That
is the same path the auto-quality scorer + classify_task_type use, and it
already handles availability, timeouts, and retries. Phase 4+ may add a CLI
dispatch path when the description optimizer needs the live triggering surface
(see open question #5 in the design doc).

Failure model
-------------

The grader is allowed to fail on individual expectations without failing the
whole call. When the LLM returns garbled output for one expectation, that
expectation is marked ``passed=false`` with a ``reasoning`` field that names
the parsing failure. The aggregate ``score`` then reflects the partial pass
rate honestly. A wholesale failure (LLM unavailable, all expectations
unparseable) raises ``GraderError`` so the caller can return a 503 instead of
a misleading 0% score.
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


# ──────────────────────────────────────────────────────────────────────────
# Errors
# ──────────────────────────────────────────────────────────────────────────


class GraderError(RuntimeError):
    """Raised when the grader cannot produce any usable verdict.

    The caller turns this into a 503/500 — never a 200 with a bogus score.
    """


# ──────────────────────────────────────────────────────────────────────────
# Platform → model id resolution
#
# Maps the tenant's ``default_cli_platform`` (the same enum honored by
# agent_router and cli_platform_resolver) onto the model id the grader should
# label its output with AND ask local_inference to use. Phase 1 collapses
# every CLI platform onto the local Ollama floor because:
#
#   * Phase 1 doesn't ship the CLI dispatch path (see module docstring).
#   * Every CLI platform's "default" hosted model is acceptable as a grader;
#     we just need to RECORD which one the tenant asked for so a re-grade
#     against the same tenant with a different default is reproducible.
#
# When Phase 4+ wires in actual CLI dispatch, this table flips to "spawn the
# CLI subprocess and parse its stdout"; the recorded ``grader_model`` is
# stable across that change because we already record the CLI's identity.
# ──────────────────────────────────────────────────────────────────────────


_PLATFORM_TO_MODEL_LABEL = {
    "claude_code": "claude-3-5-sonnet-latest",
    "codex": "gpt-4o",
    "gemini_cli": "gemini-1.5-pro-latest",
    "copilot_cli": "gpt-4o",
    "qwen_code": "qwen2.5-coder:32b",
    "kimi_k2": "moonshot-v1-32k",
    "deepseek": "deepseek-chat",
    "glm": "glm-4-flash",
    "aider": "claude-3-5-sonnet-latest",
    "goose": "claude-3-5-sonnet-latest",
    "opencode": "gemma3:4b",
}

# Local Ollama tag the grader actually calls in Phase 1. Cheap to swap to a
# stronger local model later; we keep it small here so grader latency is
# bounded on the dev machine.
_DEFAULT_LOCAL_MODEL_TAG = "gemma3:4b"


def _resolve_grader_model(db, tenant_id: uuid.UUID) -> str:
    """Return the model label to record on the grading payload.

    Reads ``tenant_features.default_cli_platform`` exactly like
    ``agent_router._init_platform``. Falls back to opencode (local Gemma)
    when the tenant has no preference set — same floor as the chat path.
    """
    try:
        # Late import — keeps the module import light and avoids a cycle
        # with TenantFeatures during test collection.
        from app.models.tenant_features import TenantFeatures

        features = (
            db.query(TenantFeatures)
            .filter(TenantFeatures.tenant_id == tenant_id)
            .first()
        )
        platform = getattr(features, "default_cli_platform", None) if features else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("grader: tenant_features lookup failed: %s", exc)
        platform = None

    if platform and platform in _PLATFORM_TO_MODEL_LABEL:
        return _PLATFORM_TO_MODEL_LABEL[platform]
    return _PLATFORM_TO_MODEL_LABEL["opencode"]


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


def _validate_expectations(raw: List) -> List[Expectation]:
    """Coerce caller-supplied list-of-dicts into Expectation objects.

    Drops entries that don't match the schema instead of raising — a malformed
    single expectation shouldn't 500 the grader. The endpoint surfaces a count
    of dropped expectations in the response metadata (Phase 1 keeps this
    server-side only; Phase 2 wires it into the UI).
    """
    cleaned: List[Expectation] = []
    for item in raw:
        if isinstance(item, Expectation):
            cleaned.append(item)
            continue
        if not isinstance(item, dict):
            logger.warning("grader: dropping non-dict expectation: %r", item)
            continue
        try:
            cleaned.append(Expectation(**item))
        except ValidationError as exc:
            logger.warning(
                "grader: dropping malformed expectation %r: %s", item, exc
            )
    return cleaned


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
        db: SQLAlchemy session for the tenant_features lookup. Optional —
            when None, the grader uses the local-Gemma floor as the model
            label (same fallback the chat path takes).

    Returns:
        GradingResult mirroring ``docs/skill-creator/schemas.md``::
        ``grading.json``.

    Raises:
        GraderError: when the LLM is unavailable AND no expectations could
            be graded. A partial grade (some expectations parsed, others
            fell back to ``passed=false``) is returned without raising.
    """
    cleaned = _validate_expectations(expectations)
    if not cleaned:
        # Caller asked us to grade against zero usable expectations. Return
        # a zero-length grading rather than raising so the endpoint can
        # surface "all your expectations were malformed" via the run row.
        return _empty_result(
            eval_id=eval_id,
            run_id=run_id,
            grader_model=_resolve_grader_model(db, tenant_id) if db is not None else _PLATFORM_TO_MODEL_LABEL["opencode"],
        )

    grader_model_label = (
        _resolve_grader_model(db, tenant_id)
        if db is not None
        else _PLATFORM_TO_MODEL_LABEL["opencode"]
    )

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
        # The grader produced nothing usable — but we still got at least one
        # well-formed expectation in. Mark every expectation failed with a
        # reasoning that names the grader-outage, AND raise so the endpoint
        # returns 503 instead of a misleading "all failed" 200.
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
        passed=score == 1.0,
        expectations=graded,
    )


def _empty_result(*, eval_id: str, run_id: str, grader_model: str) -> GradingResult:
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
    )
