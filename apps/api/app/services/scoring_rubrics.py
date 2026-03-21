"""Scoring rubric registry for configurable entity scoring."""
from __future__ import annotations

from typing import Dict, Any, Optional


# Default rubrics keyed by ID
RUBRICS: Dict[str, Dict[str, Any]] = {}


def _register(rubric_id: str, rubric: Dict[str, Any]) -> None:
    RUBRICS[rubric_id] = rubric


def get_rubric(rubric_id: str) -> Optional[Dict[str, Any]]:
    """Get a rubric by ID."""
    return RUBRICS.get(rubric_id)


def list_rubrics() -> Dict[str, Dict[str, Any]]:
    """List all available rubrics."""
    return {k: {"name": v["name"], "description": v["description"]} for k, v in RUBRICS.items()}


# ---------- AI Lead Scoring (current default) ----------
_register("ai_lead", {
    "name": "AI Lead Scoring",
    "description": "Score leads 0-100 based on likelihood of becoming a customer for an AI/agent orchestration platform",
    "system_prompt": "You are a lead scoring engine. Return only valid JSON.",
    "prompt_template": """You are a lead scoring specialist. Analyze the following entity and compute a composite score from 0 to 100 based on how likely this entity is to become a customer for an AI agent orchestration platform.

## Scoring Rubric (0-100 total)

| Category | Max Points | What to look for |
|---|---|---|
| hiring | 25 | Job posts mentioning AI, ML, agents, orchestration, automation, platform engineering |
| tech_stack | 20 | Uses or evaluates LangChain, OpenAI, Anthropic, CrewAI, AutoGen, or similar agent frameworks |
| funding | 20 | Recent funding round (Series A/B/C within 12 months scores highest) |
| company_size | 15 | Mid-market (50-500 employees) and growth-stage companies score highest |
| news | 10 | Recent product launches, partnerships, expansions, AI initiatives |
| direct_fit | 10 | Explicit mentions of orchestration needs, multi-agent workflows, workflow automation |

## Entity to Score

Name: {name}
Type: {entity_type}
Category: {category}
Description: {description}
Properties: {properties}
Enrichment Data: {enrichment_data}
Source URL: {source_url}

## Related Entities
{relations_text}

## Instructions

Return ONLY a JSON object with this exact structure:
{{"score": <integer 0-100>, "breakdown": {{"hiring": <integer 0-25>, "tech_stack": <integer 0-20>, "funding": <integer 0-20>, "company_size": <integer 0-15>, "news": <integer 0-10>, "direct_fit": <integer 0-10>}}, "reasoning": "<one paragraph explaining the score>"}}""",
    "categories": {
        "hiring": {"max": 25, "description": "AI/ML/agent hiring signals"},
        "tech_stack": {"max": 20, "description": "AI framework adoption"},
        "funding": {"max": 20, "description": "Recent funding activity"},
        "company_size": {"max": 15, "description": "Company size/stage fit"},
        "news": {"max": 10, "description": "Recent news and momentum"},
        "direct_fit": {"max": 10, "description": "Direct orchestration need indicators"},
    },
})


# ---------- HCA Deal Intelligence (M&A sell-likelihood) ----------
_register("hca_deal", {
    "name": "HCA Deal Intelligence",
    "description": "Score companies 0-100 on sell-likelihood for middle-market M&A advisory",
    "system_prompt": "You are an investment banking deal scoring engine. Return only valid JSON.",
    "prompt_template": """You are an M&A deal intelligence specialist for a middle-market investment bank. Analyze the following company entity and compute a sell-likelihood score from 0 to 100.

## Scoring Rubric (0-100 total, weighted)

| Category | Weight | Max Points | What to look for |
|---|---|---|---|
| ownership_succession | 0.30 | 30 | Owner age 55+, years in business 20+, no visible succession plan, owner reducing involvement, key person risk |
| market_timing | 0.25 | 25 | Industry M&A activity trending up, multiples at cycle highs, competitor exits, industry consolidation, regulatory sell pressure |
| company_performance | 0.20 | 20 | Revenue plateau after strong run, revenue $10M-$200M sweet spot, EBITDA margins expanding, customer concentration decreasing, recurring revenue growing |
| external_triggers | 0.15 | 15 | Recent leadership changes (new CFO/COO), hiring for corp dev/M&A roles, capex slowdown, debt maturity approaching, recent press/awards |
| negative_signals | 0.10 | -10 | Recent PE acquisition (-5), recent capital raise (-3), founder very young (-3), rapid hiring/growth mode (-2), new product launches (-2). These REDUCE the score. |

## Entity to Score

Name: {name}
Type: {entity_type}
Category: {category}
Description: {description}
Properties: {properties}
Enrichment Data: {enrichment_data}
Source URL: {source_url}

## Related Entities
{relations_text}

## Instructions

Return ONLY a JSON object with this exact structure:
{{"score": <integer 0-100>, "breakdown": {{"ownership_succession": <integer 0-30>, "market_timing": <integer 0-25>, "company_performance": <integer 0-20>, "external_triggers": <integer 0-15>, "negative_signals": <integer -10 to 0>}}, "reasoning": "<one paragraph explaining the sell-likelihood assessment>"}}""",
    "categories": {
        "ownership_succession": {"max": 30, "description": "Owner age, succession planning, involvement reduction"},
        "market_timing": {"max": 25, "description": "M&A cycle, multiples, industry consolidation"},
        "company_performance": {"max": 20, "description": "Revenue, margins, recurring revenue"},
        "external_triggers": {"max": 15, "description": "Leadership changes, corp dev hiring, debt maturity"},
        "negative_signals": {"max": 0, "min": -10, "description": "Factors reducing sell-likelihood"},
    },
})


# ---------- Marketing Signals (campaign/engagement scoring) ----------
_register("marketing_signal", {
    "name": "Marketing Signal Scoring",
    "description": "Score leads 0-100 based on marketing engagement, campaign response, and buying intent signals",
    "system_prompt": "You are a marketing intelligence scoring engine. Return only valid JSON.",
    "prompt_template": """You are a marketing signal analyst. Analyze the following entity and compute a marketing-qualified lead score from 0 to 100 based on engagement signals and buying intent.

## Scoring Rubric (0-100 total)

| Category | Max Points | What to look for |
|---|---|---|
| engagement | 25 | Website visits, content downloads, webinar attendance, demo requests, email open/click rates |
| intent_signals | 25 | Searched for competitor products, visited pricing page, compared solutions, asked for proposal |
| firmographic_fit | 20 | Industry match, company size in ICP range, geography alignment, technology stack compatibility |
| behavioral_recency | 15 | How recent the engagement (last 7 days = highest, last 30 = medium, 30+ = low), frequency of interactions |
| champion_signals | 15 | Multiple contacts engaged, senior decision-maker involved, internal champion identified, shared content internally |

## Entity to Score

Name: {name}
Type: {entity_type}
Category: {category}
Description: {description}
Properties: {properties}
Enrichment Data: {enrichment_data}
Source URL: {source_url}

## Related Entities
{relations_text}

## Instructions

Return ONLY a JSON object with this exact structure:
{{"score": <integer 0-100>, "breakdown": {{"engagement": <integer 0-25>, "intent_signals": <integer 0-25>, "firmographic_fit": <integer 0-20>, "behavioral_recency": <integer 0-15>, "champion_signals": <integer 0-15>}}, "reasoning": "<one paragraph explaining the marketing qualification score>"}}""",
    "categories": {
        "engagement": {"max": 25, "description": "Website, content, demo engagement"},
        "intent_signals": {"max": 25, "description": "Buying intent and comparison activity"},
        "firmographic_fit": {"max": 20, "description": "ICP and firmographic alignment"},
        "behavioral_recency": {"max": 15, "description": "Recency and frequency of engagement"},
        "champion_signals": {"max": 15, "description": "Internal champion and multi-contact engagement"},
    },
})


# ---------- Agent Response Quality (RL auto-scoring) ----------
_register("agent_response_quality", {
    "name": "Agent Response Quality",
    "description": "Score agent responses 0-100 across quality dimensions for RL training. Includes cost efficiency tracking by platform.",
    "system_prompt": "You are an AI response quality evaluator. Return only valid JSON.",
    "prompt_template": """You are an expert AI response quality evaluator. Analyze this agent interaction and compute a quality score from 0 to 100.

## Scoring Rubric (0-100 total)

| Category | Max Points | What to look for |
|---|---|---|
| accuracy | 25 | Factually correct, answers the actual question, no hallucinations, verifiable claims |
| helpfulness | 20 | Addresses what the user actually needs (not just what they asked), actionable, complete |
| tool_usage | 20 | Used appropriate MCP tools (email, calendar, knowledge, code), didn't skip tools that would help, didn't call unnecessary tools |
| memory_usage | 15 | Checked knowledge graph (find_entities, search_knowledge), used recalled context, built on previous conversations |
| efficiency | 10 | Concise without losing substance, fast response, didn't over-explain or pad |
| context_awareness | 10 | Used conversation history, referenced prior messages, maintained continuity |

## Interaction Details

Platform: {platform}
Agent: {agent_slug}
Task Type: {task_type}
Channel: {channel}
Tokens Used: {tokens_used}
Response Time: {response_time_ms}ms
Cost (USD): ${cost_usd}

User Message: {user_message}

Agent Response: {agent_response}

Tools Called: {tools_called}
Entities Recalled: {entities_recalled}

## Instructions

Return ONLY a JSON object with this exact structure:
{{"score": <integer 0-100>, "breakdown": {{"accuracy": <integer 0-25>, "helpfulness": <integer 0-20>, "tool_usage": <integer 0-20>, "memory_usage": <integer 0-15>, "efficiency": <integer 0-10>, "context_awareness": <integer 0-10>}}, "cost_efficiency": {{"tokens_per_quality_point": <float>, "platform_recommendation": "<claude_code|gemini_cli|codex|any>"}}, "reasoning": "<one paragraph explaining the score>"}}""",
    "categories": {
        "accuracy": {"max": 25, "description": "Factual correctness, no hallucinations"},
        "helpfulness": {"max": 20, "description": "Addresses actual user need, actionable"},
        "tool_usage": {"max": 20, "description": "Appropriate MCP tool selection and usage"},
        "memory_usage": {"max": 15, "description": "Knowledge graph recall, context building"},
        "efficiency": {"max": 10, "description": "Concise, fast, no padding"},
        "context_awareness": {"max": 10, "description": "Conversation continuity, history usage"},
    },
})
