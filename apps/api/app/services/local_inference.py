"""Local inference service — calls Ollama for zero-cost ML tasks.

Uses small models (Qwen2.5-Coder-0.5B to 3B) running on the local
Ollama instance for:
- Auto-quality scoring of agent responses
- Entity extraction from messages
- Task type classification
- Response summarization

All inference is async and non-blocking — never delays user responses.
"""

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434")
DEFAULT_MODEL = os.environ.get("LOCAL_MODEL", "qwen2.5-coder:0.5b")
QUALITY_MODEL = os.environ.get("QUALITY_MODEL", "qwen2.5-coder:1.5b")


async def generate(
    prompt: str,
    model: str = None,
    system: str = "",
    temperature: float = 0.1,
    max_tokens: int = 500,
    timeout: float = 30.0,
) -> Optional[str]:
    """Call Ollama generate endpoint. Returns None on failure."""
    model = model or DEFAULT_MODEL
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "system": system,
                    "stream": False,
                    "options": {
                        "temperature": temperature,
                        "num_predict": max_tokens,
                    },
                },
            )
            if resp.status_code == 200:
                return resp.json().get("response", "").strip()
            logger.warning("Ollama returned %s: %s", resp.status_code, resp.text[:200])
    except httpx.ConnectError:
        logger.debug("Ollama not available at %s", OLLAMA_BASE_URL)
    except Exception as e:
        logger.warning("Local inference failed: %s", e)
    return None


async def score_response_quality(
    user_message: str,
    agent_response: str,
) -> Optional[dict]:
    """Score an agent response on a 1-5 scale using local model.

    Returns dict with 'score' (1-5), 'reasoning' (brief explanation),
    or None if scoring fails.
    """
    prompt = f"""Rate this AI assistant response on a scale of 1-5.

USER MESSAGE: {user_message[:500]}

ASSISTANT RESPONSE: {agent_response[:1000]}

SCORING CRITERIA:
- 5: Excellent — directly answers the question, accurate, helpful, well-structured
- 4: Good — answers the question with minor issues
- 3: Acceptable — partially answers but misses key points
- 2: Poor — mostly irrelevant or incorrect
- 1: Bad — completely wrong, harmful, or nonsensical

Respond with ONLY a JSON object, no other text:
{{"score": <1-5>, "reasoning": "<one sentence explanation>"}}"""

    result = await generate(
        prompt=prompt,
        model=QUALITY_MODEL,
        system="You are a response quality evaluator. Output only valid JSON.",
        temperature=0.1,
        max_tokens=100,
    )

    if not result:
        return None

    try:
        import json
        # Try to parse JSON from response (may have extra text)
        json_match = result[result.index('{'):result.rindex('}') + 1]
        data = json.loads(json_match)
        score = int(data.get("score", 3))
        score = max(1, min(5, score))  # Clamp to 1-5
        return {
            "score": score,
            "reasoning": str(data.get("reasoning", ""))[:200],
            "model": QUALITY_MODEL,
        }
    except (json.JSONDecodeError, ValueError, IndexError):
        logger.debug("Failed to parse quality score from: %s", result[:100])
        return None


async def extract_entities(text: str) -> Optional[list]:
    """Extract entities from text using local model.

    Returns list of dicts with 'name', 'type', 'category'.
    """
    prompt = f"""Extract all named entities from this text. Return a JSON array.

TEXT: {text[:1000]}

Entity types: person, company, project, technology, location, date, amount
Return ONLY a JSON array like: [{{"name": "...", "type": "...", "category": "..."}}]
If no entities found, return: []"""

    result = await generate(
        prompt=prompt,
        model=DEFAULT_MODEL,
        system="You are an entity extraction system. Output only valid JSON arrays.",
        temperature=0.0,
        max_tokens=300,
    )

    if not result:
        return None

    try:
        import json
        json_match = result[result.index('['):result.rindex(']') + 1]
        return json.loads(json_match)
    except (json.JSONDecodeError, ValueError, IndexError):
        return None


async def classify_task_type(message: str) -> Optional[str]:
    """Classify a user message into a task type using local model."""
    prompt = f"""Classify this user message into exactly one task type.

MESSAGE: {message[:300]}

TASK TYPES: code, data, sales, marketing, knowledge, support, scheduling, general

Respond with ONLY the task type word, nothing else."""

    result = await generate(
        prompt=prompt,
        model=DEFAULT_MODEL,
        temperature=0.0,
        max_tokens=10,
    )

    if result:
        task_type = result.strip().lower().split()[0] if result.strip() else "general"
        valid_types = {"code", "data", "sales", "marketing", "knowledge", "support", "scheduling", "general"}
        return task_type if task_type in valid_types else "general"
    return None


async def is_available() -> bool:
    """Check if Ollama is running and has models loaded."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
            if resp.status_code == 200:
                models = resp.json().get("models", [])
                return len(models) > 0
    except Exception:
        pass
    return False


async def list_models() -> list:
    """List available models on Ollama."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
            if resp.status_code == 200:
                return [m.get("name") for m in resp.json().get("models", [])]
    except Exception:
        pass
    return []


async def pull_model(model_name: str) -> bool:
    """Pull a model to Ollama. This can take minutes for first download."""
    try:
        async with httpx.AsyncClient(timeout=600.0) as client:
            resp = await client.post(
                f"{OLLAMA_BASE_URL}/api/pull",
                json={"name": model_name, "stream": False},
            )
            return resp.status_code == 200
    except Exception as e:
        logger.error("Failed to pull model %s: %s", model_name, e)
        return False
