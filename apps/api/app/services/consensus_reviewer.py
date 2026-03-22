"""Consensus reviewer — 3-agent review council for ALL agent responses.

Extends the consensus review pattern (originally for code tasks in code-worker)
to every agent response in the system: Luna, Sales, Marketing, Data, etc.

Three specialized reviewers evaluate every response in parallel via local Ollama.
Requires 2/3 approval to pass. Results logged as RL experience for continuous
improvement. Runs async — never blocks user response delivery.

Reviewers:
  - Accuracy Reviewer: factual correctness, no hallucinations, tool output accuracy
  - Helpfulness Reviewer: addresses real user need, actionable, complete
  - Persona Reviewer: tone, brevity, Luna personality, memory/context usage
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

CONSENSUS_MODEL = "qwen3:1.7b"
FALLBACK_MODEL = "qwen2.5-coder:1.5b"

# ── Reviewer definitions ────────────────────────────────────────────────────

@dataclass
class ReviewAgent:
    role: str
    system_prompt: str


REVIEW_AGENTS = [
    ReviewAgent(
        role="Accuracy Reviewer",
        system_prompt=(
            "You are an Accuracy Reviewer. Your job is to verify that an AI assistant "
            "response is factually correct, does not hallucinate, and accurately reflects "
            "any tool outputs, data, or knowledge graph results mentioned.\n\n"
            "Focus on:\n"
            "- Are all stated facts accurate and grounded?\n"
            "- Are tool outputs correctly interpreted (no invented numbers/names)?\n"
            "- Are there any contradictions or unsupported claims?\n\n"
            "Respond ONLY with valid JSON. No markdown, no prose outside JSON."
        ),
    ),
    ReviewAgent(
        role="Helpfulness Reviewer",
        system_prompt=(
            "You are a Helpfulness Reviewer. Your job is to evaluate whether an AI assistant "
            "response truly addresses the user's actual need and provides actionable value.\n\n"
            "Focus on:\n"
            "- Does the response directly answer what was asked?\n"
            "- Is it actionable — does it give the user something concrete to do or know?\n"
            "- Does it complete the task or leave important parts unanswered?\n"
            "- Is the level of detail appropriate (not too sparse, not padded)?\n\n"
            "Respond ONLY with valid JSON. No markdown, no prose outside JSON."
        ),
    ),
    ReviewAgent(
        role="Persona Reviewer",
        system_prompt=(
            "You are a Persona Reviewer. Your job is to evaluate whether an AI assistant "
            "response matches good conversational style for a business AI assistant.\n\n"
            "General style guidelines (apply to all agents):\n"
            "- Concise and direct: avoid unnecessary padding or filler\n"
            "- No AI-isms: never start with 'Certainly!', 'Of course!', 'Absolutely!', 'Great question!'\n"
            "- Actionable: suggests next steps when appropriate\n"
            "- Uses context from memory/knowledge graph when relevant\n"
            "- Responds in the same language the user writes in\n"
            "- Tone should match the agent's role (warm for personal assistant, precise for data analyst)\n\n"
            "Respond ONLY with valid JSON. No markdown, no prose outside JSON."
        ),
    ),
]

# ── Review prompt template ──────────────────────────────────────────────────

REVIEW_PROMPT = """Review this agent response and return your verdict as JSON.

USER MESSAGE:
{user_message}

AGENT RESPONSE ({agent_slug} via {platform}):
{agent_response}

CONTEXT:
- Channel: {channel}
- Tools called: {tools_called}
- Entities recalled: {entities_recalled}

Return ONLY this JSON structure:
{{
  "approved": true or false,
  "verdict": "APPROVED" or "REJECTED" or "CONDITIONAL",
  "issues": ["specific issue 1", "specific issue 2"],
  "suggestions": ["actionable fix 1", "actionable fix 2"],
  "summary": "1-2 sentence review summary"
}}

Rules:
- approved=true if the response meets your criteria (APPROVED or CONDITIONAL with minor issues)
- approved=false if there are significant problems (REJECTED)
- Keep issues and suggestions to 3 items max each
- Summary must be concise (1-2 sentences)"""


# ── Single agent runner ─────────────────────────────────────────────────────

async def _run_reviewer(
    agent: ReviewAgent,
    prompt: str,
) -> dict:
    """Run one review agent. Returns parsed result or fallback dict on failure."""
    from app.services.local_inference import generate

    raw = await generate(
        prompt=prompt,
        model=CONSENSUS_MODEL,
        system=agent.system_prompt,
        temperature=0.1,
        max_tokens=250,
        timeout=20.0,
    )

    # Fallback to smaller model if primary not available
    if not raw:
        raw = await generate(
            prompt=prompt,
            model=FALLBACK_MODEL,
            system=agent.system_prompt,
            temperature=0.1,
            max_tokens=250,
            timeout=20.0,
        )

    if not raw:
        logger.debug("Consensus reviewer %s: no response from Ollama", agent.role)
        return {
            "role": agent.role,
            "approved": True,  # Fail open — don't penalize when model unavailable
            "verdict": "SKIPPED",
            "issues": [],
            "suggestions": [],
            "summary": "Reviewer unavailable — skipped",
        }

    # Extract JSON from response (handle <think> tags from qwen3)
    try:
        # Strip <think>...</think> blocks first
        clean = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
        start = clean.index("{")
        end = clean.rindex("}") + 1
        data = json.loads(clean[start:end])
    except (json.JSONDecodeError, ValueError):
        logger.debug("Consensus reviewer %s: failed to parse JSON from: %s", agent.role, raw[:120])
        return {
            "role": agent.role,
            "approved": True,  # Fail open on parse error
            "verdict": "PARSE_ERROR",
            "issues": [],
            "suggestions": [],
            "summary": "Could not parse reviewer response",
        }

    return {
        "role": agent.role,
        "approved": bool(data.get("approved", True)),
        "verdict": str(data.get("verdict", "UNKNOWN")),
        "issues": list(data.get("issues", []))[:3],
        "suggestions": list(data.get("suggestions", []))[:3],
        "summary": str(data.get("summary", ""))[:200],
    }


# ── Consensus logic ─────────────────────────────────────────────────────────

def _consensus_check(reviews: list, required: int = 2) -> tuple[bool, str]:
    """Return (passed, report). Consensus requires `required` agents to approve."""
    approved_count = sum(1 for r in reviews if r.get("approved", True))
    passed = approved_count >= required

    lines = [f"Consensus {'✓ PASSED' if passed else '✗ FAILED'} ({approved_count}/{len(reviews)} approved)"]
    for r in reviews:
        icon = "✓" if r.get("approved") else "✗"
        lines.append(f"  {icon} {r['role']}: {r['verdict']} — {r.get('summary', '')}")
        for issue in r.get("issues", []):
            lines.append(f"      Issue: {issue}")

    return passed, "\n".join(lines)


# ── Public API ───────────────────────────────────────────────────────────────

@dataclass
class ConsensusResult:
    passed: bool
    approved_count: int
    total_reviewers: int
    reviews: list
    report: str
    all_issues: list = field(default_factory=list)
    all_suggestions: list = field(default_factory=list)


async def run_consensus_review(
    user_message: str,
    agent_response: str,
    agent_slug: str = "luna",
    platform: str = "claude_code",
    channel: str = "web",
    tools_called: list = None,
    entities_recalled: list = None,
) -> ConsensusResult:
    """Run 3 review agents in parallel and return consensus result.

    Never raises — always returns a ConsensusResult (may have skipped reviews).
    """
    tools_called = tools_called or []
    entities_recalled = entities_recalled or []

    prompt = REVIEW_PROMPT.format(
        user_message=user_message[:400],
        agent_slug=agent_slug,
        platform=platform,
        agent_response=agent_response[:800],
        channel=channel,
        tools_called=", ".join(str(t) for t in tools_called[:8]) or "none",
        entities_recalled=", ".join(str(e) for e in entities_recalled[:5]) or "none",
    )

    # Run all 3 reviewers in parallel
    try:
        reviews = await asyncio.gather(
            *[_run_reviewer(agent, prompt) for agent in REVIEW_AGENTS],
            return_exceptions=False,
        )
    except Exception as e:
        logger.warning("Consensus review failed with exception: %s", e)
        reviews = [
            {"role": a.role, "approved": True, "verdict": "ERROR", "issues": [], "suggestions": [], "summary": str(e)[:100]}
            for a in REVIEW_AGENTS
        ]

    passed, report = _consensus_check(list(reviews))

    all_issues = [issue for r in reviews for issue in r.get("issues", [])]
    all_suggestions = [s for r in reviews for s in r.get("suggestions", [])]

    approved_count = sum(1 for r in reviews if r.get("approved", True))

    logger.info(
        "Consensus review: %s (%d/%d) agent=%s | %s",
        "PASSED" if passed else "FAILED",
        approved_count,
        len(reviews),
        agent_slug,
        "; ".join(all_issues[:3]) if not passed else "no issues",
    )

    return ConsensusResult(
        passed=passed,
        approved_count=approved_count,
        total_reviewers=len(reviews),
        reviews=list(reviews),
        report=report,
        all_issues=all_issues,
        all_suggestions=all_suggestions,
    )
