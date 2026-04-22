import logging
import time
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.models.agent_test_suite import AgentTestCase, AgentTestRun

logger = logging.getLogger(__name__)


def _score_case(actual_text: str, quality_score: Optional[float], latency_ms: int, case: AgentTestCase) -> dict:
    """Evaluate a single case; returns {pass, reason, ...}."""
    reasons = []
    text_lower = (actual_text or "").lower()

    for needle in case.expected_output_contains or []:
        if str(needle).lower() not in text_lower:
            reasons.append(f"missing expected phrase: {needle}")

    for banned in case.expected_output_excludes or []:
        if str(banned).lower() in text_lower:
            reasons.append(f"contains banned phrase: {banned}")

    min_quality = float(case.min_quality_score or 0)
    if quality_score is not None and quality_score < min_quality:
        reasons.append(f"quality {quality_score:.2f} below minimum {min_quality:.2f}")

    max_latency = int(case.max_latency_ms or 0)
    if max_latency > 0 and latency_ms > max_latency:
        reasons.append(f"latency {latency_ms}ms exceeds max {max_latency}ms")

    return {
        "case_id": str(case.id),
        "case_name": case.name,
        "pass": len(reasons) == 0,
        "reason": "; ".join(reasons) if reasons else None,
        "actual_preview": (actual_text or "")[:500],
        "quality_score": quality_score,
        "latency_ms": latency_ms,
    }


def _invoke_agent_local(db: Session, agent: Agent, prompt: str) -> tuple[str, Optional[float], int]:
    """Invoke the agent locally via Gemma 4 (zero cloud cost) for deterministic test runs.

    Returns (response_text, quality_score, latency_ms). Quality scoring runs best-effort
    via a lightweight synchronous call to the local rubric; returns None if unavailable.
    """
    from app.services.local_inference import generate_agent_response_sync

    start = time.time()
    system_prompt = (agent.persona_prompt or agent.description or "You are a helpful agent.").strip()
    try:
        response_text = generate_agent_response_sync(
            message=prompt,
            skill_body=system_prompt,
            agent_slug=agent.name or "agent",
        ) or ""
    except Exception as exc:
        logger.warning("Local inference failed for agent %s test: %s", agent.id, exc)
        response_text = ""
    latency_ms = int((time.time() - start) * 1000)
    logger.info(
        "agent_test invoke agent=%s prompt_len=%d response_len=%d latency_ms=%d",
        agent.id, len(prompt), len(response_text), latency_ms,
    )

    quality_score = _score_response_locally(prompt, response_text)
    return response_text, quality_score, latency_ms


def _score_response_locally(user_message: str, agent_response: str) -> Optional[float]:
    """Lightweight sync quality scorer for test runs.

    Uses the same `agent_response_quality` rubric as auto_quality_scorer but runs it
    synchronously via local_inference.generate_sync so the test runner doesn't need
    an event loop. Returns a 0.0-1.0 score or None on any failure.
    """
    if not agent_response or not agent_response.strip():
        return 0.0
    try:
        from app.services.local_inference import generate_sync, QUALITY_MODEL
        from app.services.scoring_rubrics import get_rubric
        import json as _json
        import re as _re

        rubric = get_rubric("agent_response_quality")
        if not rubric:
            return None
        prompt = rubric["prompt_template"].format(
            platform="local_test",
            agent_slug="test",
            task_type="test",
            channel="test",
            tokens_used=0,
            response_time_ms=0,
            cost_usd="0.0000",
            user_message=user_message[:500],
            agent_response=agent_response[:1000],
            tools_called="none",
            entities_recalled="none",
        )
        raw = generate_sync(
            prompt=prompt,
            model=QUALITY_MODEL,
            system=rubric.get("system_prompt", ""),
            temperature=0.1,
            max_tokens=300,
        ) or ""
        # Rubric prompts ask for JSON with a `total_score` field (0-100).
        match = _re.search(r"\{[\s\S]*\}", raw)
        if not match:
            return None
        data = _json.loads(match.group(0))
        total = data.get("total_score") or data.get("score")
        if isinstance(total, (int, float)):
            return max(0.0, min(1.0, float(total) / 100.0))
    except Exception as exc:
        logger.debug("Local test-run scorer failed: %s", exc)
    return None


def run_test_suite(
    db: Session,
    *,
    agent_id: uuid.UUID,
    tenant_id: uuid.UUID,
    triggered_by_user_id: Optional[uuid.UUID] = None,
    run_type: str = "manual",
) -> AgentTestRun:
    agent = db.query(Agent).filter(Agent.id == agent_id, Agent.tenant_id == tenant_id).first()
    if not agent:
        raise ValueError("Agent not found")

    cases = (
        db.query(AgentTestCase)
        .filter(
            AgentTestCase.agent_id == agent_id,
            AgentTestCase.tenant_id == tenant_id,
            AgentTestCase.enabled.is_(True),
        )
        .all()
    )

    run = AgentTestRun(
        agent_id=agent_id,
        tenant_id=tenant_id,
        agent_version=agent.version,
        triggered_by_user_id=triggered_by_user_id,
        run_type=run_type,
        status="running",
        total_cases=len(cases),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    results = []
    passed = 0
    failed = 0
    for case in cases:
        response_text, quality, latency_ms = _invoke_agent_local(db, agent, case.input)
        entry = _score_case(response_text, quality, latency_ms, case)
        results.append(entry)
        if entry["pass"]:
            passed += 1
        else:
            failed += 1

    run.results = results
    run.passed_count = passed
    run.failed_count = failed
    # No cases → the run is a no-op, not an error. Manual callers still see this as
    # "passed" so the promotion gate short-circuits cleanly; the count of 0 makes
    # the emptiness obvious in the UI.
    run.status = "failed" if failed > 0 else "passed"
    run.completed_at = datetime.utcnow()
    db.commit()
    db.refresh(run)
    return run
