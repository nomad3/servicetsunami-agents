"""Skill-creator eval endpoints.

Phase 1 surface: a single endpoint that runs the grader against a saved
``skill_eval_runs`` row and persists the result into ``skill_eval_grading``.
The eval runner itself (Phase 2) writes the run row; until that lands, this
endpoint is exercised by tests + the bundled ``skill-creator`` skill body
that inserts a run row directly via the DB-backed flow.

Security model: the endpoint runs under ``get_current_user`` so it inherits
the standard JWT + session check. Tenant ownership is enforced by joining
``skill_eval_runs → skill_evals → skills(tenant_id)`` — a request that names
a foreign tenant's run will 404 instead of leaking data.

The grader is a synchronous LLM call (~10–30s in practice). Phase 4+ moves
this onto the async chat-result pattern from
``docs/plans/2026-05-17-async-chat-result-pattern-design.md`` so a long grade
doesn't trip Cloudflare's 524. For Phase 1 the sync path keeps the contract
simple and is fine for the dev-loop usage.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.services.skill_creator import GradingResult, grade
from app.services.skill_creator.grader import GraderError

logger = logging.getLogger(__name__)
router = APIRouter()


class GradeRunRequest(BaseModel):
    """Request body for ``POST /skills/{skill_id}/evals/grade``.

    ``run_id`` is the only required field — the endpoint joins back through
    ``skill_eval_runs → skill_evals`` to pull the transcript, outputs path,
    and expectations. The caller doesn't need to re-supply any of that.
    """

    run_id: uuid.UUID


def _verify_tenant_owns_skill(
    db: Session, skill_id: uuid.UUID, tenant_id: uuid.UUID
) -> None:
    """Raise 404 if the skill doesn't exist or belongs to a different tenant.

    Using 404 (not 403) is deliberate — a 403 would confirm the skill_id is
    a real id owned by someone else, which leaks existence. Same pattern as
    the rest of the v1 skill endpoints.
    """
    row = db.execute(
        text("SELECT tenant_id FROM skills WHERE id = :id"),
        {"id": str(skill_id)},
    ).fetchone()
    if row is None or str(row[0]) != str(tenant_id):
        raise HTTPException(status_code=404, detail="Skill not found")


def _load_run_context(
    db: Session, skill_id: uuid.UUID, run_id: uuid.UUID
) -> dict:
    """Return ``{eval_id, transcript, outputs, expectations}`` for the run.

    Joins ``skill_eval_runs → skill_evals`` and verifies the eval belongs to
    the given skill. 404 if the run isn't found OR if it's tied to a
    different skill (cross-skill replay attack).
    """
    row = db.execute(
        text(
            """
            SELECT r.id, r.eval_id, r.transcript, r.outputs,
                   e.expectations, e.skill_id
              FROM skill_eval_runs r
              JOIN skill_evals e ON e.id = r.eval_id
             WHERE r.id = :run_id
            """
        ),
        {"run_id": str(run_id)},
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if str(row[5]) != str(skill_id):
        # The run exists but belongs to a different skill — same 404 to
        # avoid confirming the run id is real.
        raise HTTPException(status_code=404, detail="Run not found")
    return {
        "eval_id": str(row[1]),
        "transcript": row[2] or "",
        "outputs": row[3],
        "expectations": row[4] or [],
    }


def _persist_grading(
    db: Session,
    run_id: uuid.UUID,
    result: GradingResult,
) -> None:
    """Upsert the grading row.

    ``skill_eval_grading`` is keyed by ``run_id`` (1:1 with the run), so a
    re-grade overwrites the prior row in place. Phase 3 will archive the
    prior payload into ``library_revisions`` before the overwrite; for now
    the simple upsert is correct because Phase 1 doesn't expose a re-grade
    button — the only way to re-grade is to delete and POST again.
    """
    payload = result.model_dump()
    db.execute(
        text(
            """
            INSERT INTO skill_eval_grading (run_id, grading, score, grader_model, graded_at)
            VALUES (:run_id, CAST(:grading AS JSONB), :score, :grader_model, now())
            ON CONFLICT (run_id) DO UPDATE
               SET grading = EXCLUDED.grading,
                   score = EXCLUDED.score,
                   grader_model = EXCLUDED.grader_model,
                   graded_at = now()
            """
        ),
        {
            "run_id": str(run_id),
            "grading": _json_dumps(payload),
            "score": result.score,
            "grader_model": result.grader_model,
        },
    )
    db.commit()


def _json_dumps(obj) -> str:
    """Stable JSON serialization for psycopg2's JSONB cast.

    We pass the JSON as a TEXT bind and let Postgres parse it (CAST AS JSONB)
    so this works under both psycopg2 and psycopg3 without driver-specific
    JSONB adapters.
    """
    import json
    return json.dumps(obj, sort_keys=False, default=str)


# NOTE: ``skill_eval_runs.outputs`` is a manifest dict of the shape
# ``{path: {size_bytes, mime}}`` per schemas.md / migration 136. Phase 1
# doesn't write any outputs, so the grader is always called with
# ``outputs_dir=None``. Phase 2 will add a resolver that maps the manifest
# back to the on-disk iteration directory (workspaces volume) — the shape
# isn't a stable string-or-dict, so we don't pre-write one here.


# ──────────────────────────────────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────────────────────────────────


@router.post(
    "/{skill_id}/evals/grade",
    response_model=GradingResult,
)
def grade_run(
    skill_id: uuid.UUID,
    payload: GradeRunRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> GradingResult:
    """Grade a saved eval run against its expectations.

    Returns the ``grading.json`` payload and persists it into
    ``skill_eval_grading``. Side-effect-on-success only — a grader outage
    (LLM unavailable / unparseable response) returns 503 and the grading
    row is left untouched.
    """
    _verify_tenant_owns_skill(db, skill_id, current_user.tenant_id)
    ctx = _load_run_context(db, skill_id, payload.run_id)

    try:
        result = grade(
            transcript=ctx["transcript"],
            # Phase 1 doesn't write file outputs; the grader handles None
            # cleanly. Phase 2 will resolve the manifest into a real path.
            outputs_dir=None,
            expectations=ctx["expectations"],
            tenant_id=current_user.tenant_id,
            session_id=current_user.id,
            eval_id=ctx["eval_id"],
            run_id=str(payload.run_id),
            db=db,
        )
    except GraderError as exc:
        logger.warning(
            "grade_run: grader outage — run_id=%s tenant=%s: %s",
            payload.run_id, current_user.tenant_id, exc,
        )
        raise HTTPException(
            status_code=503,
            detail="Grader unavailable. Try again in a moment.",
        )

    try:
        _persist_grading(db, payload.run_id, result)
    except Exception as exc:  # noqa: BLE001
        # The endpoint contract is side-effect-on-success only (see docstring
        # above): a 200 must mean a grading row exists. If the commit fails
        # we MUST rollback and surface a 500 — returning the payload with a
        # 200 would lie about persistence and break re-grade idempotency.
        logger.warning(
            "grade_run: persist failed — run_id=%s: %s",
            payload.run_id, exc,
        )
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="failed to persist grading row",
        )

    return result
