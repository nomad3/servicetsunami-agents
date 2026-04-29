"""Local inference service — calls Ollama for zero-cost ML tasks.

Uses Gemma 4 models running on the local
Ollama instance for:
- Auto-quality scoring of agent responses
- Entity extraction from messages
- Task type classification
- Response summarization

All inference is async and non-blocking — never delays user responses.
"""

import asyncio
import logging
import os
import re
import threading
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


def _strip_json_fences(text: str) -> str:
    """Strip optional ```json ... ``` markdown fences and surrounding whitespace.

    Some Gemma outputs wrap JSON in code fences even when asked not to.
    Returns the inner JSON text. Safe to call on already-clean JSON.
    """
    if not text:
        return text
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
DEFAULT_MODEL = os.environ.get("LOCAL_MODEL", "gemma4")
QUALITY_MODEL = os.environ.get("QUALITY_MODEL", "gemma4")

# ---------------------------------------------------------------------------
# GPU inference bulkhead — foreground (user-blocking) has priority over
# background (scoring/consensus). Background skips when foreground is active.
#
# Shared _foreground_active flag coordinates across async and sync callers:
# - Sync foreground (local_tool_agent, generate_sync) sets the flag via _ollama_sync_lock
# - Async background (scoring, consensus) checks the flag before entering
# ---------------------------------------------------------------------------
_foreground_active = threading.Event()     # Set when any foreground caller holds GPU
_ollama_sync_lock = threading.Lock()       # Sync foreground callers
_background_lock = threading.Lock()        # Serializes background calls (thread-safe, no event loop issues)


async def generate(
    prompt: str,
    model: str = None,
    system: str = "",
    temperature: float = 0.1,
    max_tokens: int = 500,
    timeout: float = 30.0,
    priority: str = "background",
    response_format: str = None,
) -> Optional[str]:
    """Call Ollama generate endpoint. Returns None on failure.

    priority="foreground" — user-blocking, gets exclusive GPU access
    priority="background" — scoring/consensus, skips if foreground is active
    """
    model = model or DEFAULT_MODEL

    if priority == "foreground":
        return await _generate_foreground(prompt, model, system, temperature, max_tokens, timeout, response_format)
    else:
        return await _generate_background(prompt, model, system, temperature, max_tokens, timeout, response_format)


async def _generate_foreground(prompt, model, system, temperature, max_tokens, timeout, response_format=None):
    """Foreground async inference — sets shared flag so background skips."""
    try:
        _foreground_active.set()
        return await _do_generate(prompt, model, system, temperature, max_tokens, timeout, response_format)
    except Exception as e:
        logger.warning("Foreground inference failed: %s", e)
        return None
    finally:
        _foreground_active.clear()


async def _generate_background(prompt, model, system, temperature, max_tokens, timeout, response_format=None):
    """Background inference — skips if any foreground caller (async or sync) is active."""
    if _foreground_active.is_set():
        logger.debug("GPU busy with foreground — skipping background inference")
        return None
    acquired = _background_lock.acquire(blocking=False)
    if not acquired:
        logger.debug("Background queue full — skipping")
        return None
    try:
        if _foreground_active.is_set():
            logger.debug("GPU became busy — skipping background inference")
            return None
        return await _do_generate(prompt, model, system, temperature, max_tokens, timeout, response_format)
    except Exception as e:
        logger.warning("Background inference failed: %s", e)
        return None
    finally:
        _background_lock.release()


async def _do_generate(prompt, model, system, temperature, max_tokens, timeout, response_format=None):
    """Raw Ollama generate call — no locking, called by foreground/background wrappers."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            payload = {
                "model": model,
                "prompt": prompt,
                "system": system,
                "stream": False,
                "options": {
                    "temperature": temperature,
                    "num_predict": max_tokens,
                },
                # Tier-1 #3: pin the model in Ollama memory between calls
                # so quiet 10-15 min periods don't trigger a cold-load.
                "keep_alive": "30m",
            }
            if response_format:
                payload["format"] = response_format

            resp = await client.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json=payload,
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


# ---------------------------------------------------------------------------
# Synchronous helpers (for use in sync code paths like services/context_manager)
# ---------------------------------------------------------------------------

def generate_sync(
    prompt: str,
    model: str = None,
    system: str = "",
    temperature: float = 0.1,
    max_tokens: int = 500,
    timeout: float = 45.0,
    response_format: str = None,
) -> Optional[str]:
    """Synchronous Ollama call. Returns None on failure. Sets foreground flag so background skips."""
    model = model or DEFAULT_MODEL
    with _ollama_sync_lock:
        _foreground_active.set()
        try:
            with httpx.Client(timeout=timeout) as client:
                payload = {
                    "model": model,
                    "prompt": prompt,
                    "system": system,
                    "stream": False,
                    "options": {
                        "temperature": temperature,
                        "num_predict": max_tokens,
                    },
                    "keep_alive": "30m",  # Tier-1 #3 — pin model between calls
                }
                if response_format:
                    payload["format"] = response_format

                resp = client.post(
                    f"{OLLAMA_BASE_URL}/api/generate",
                    json=payload,
                )
                if resp.status_code == 200:
                    return resp.json().get("response", "").strip()
                logger.warning("Ollama (sync) returned %s: %s", resp.status_code, resp.text[:200])
        except httpx.ConnectError:
            logger.debug("Ollama not available (sync) at %s", OLLAMA_BASE_URL)
        except Exception as e:
            logger.warning("Local inference (sync) failed: %s", e)
        finally:
            _foreground_active.clear()
    return None


# ---------------------------------------------------------------------------
# Domain-specific offload functions
# ---------------------------------------------------------------------------

async def triage_inbox_items(items_text: str) -> Optional[list]:
    """Triage emails and calendar events using local Gemma 4 model.

    Returns a JSON list of high/medium priority items, or None on failure.
    Replaces the Anthropic LLM call in inbox_monitor.triage_items().
    """
    import json

    prompt = f"""{items_text}

Classify each item as "high" or "medium" priority. Skip all "low" priority items.
Return ONLY a JSON array (no markdown fences, no explanation):
[
  {{
    "source": "gmail" or "calendar",
    "priority": "high" or "medium",
    "title": "Brief summary (max 100 chars)",
    "body": "Why this matters and suggested action (1-2 sentences)",
    "reference_id": "the email id or event id",
    "reference_type": "email" or "event"
  }}
]
If nothing is high or medium priority, respond with: []"""

    result = await generate(
        prompt=prompt,
        model=QUALITY_MODEL,
        system=(
            "You are an inbox triage assistant. Classify items as high/medium priority. "
            "Output only valid JSON arrays. Skip low-priority items."
        ),
        temperature=0.1,
        max_tokens=1500,
        timeout=60.0,
    )

    if not result:
        return None

    try:
        start = result.index("[")
        end = result.rindex("]") + 1
        parsed = json.loads(result[start:end])
        if isinstance(parsed, list):
            return parsed
    except (json.JSONDecodeError, ValueError, IndexError):
        logger.debug("Failed to parse Gemma 4 triage result: %s", result[:200])
    return None


def extract_knowledge_sync(content: str, content_type: str = "plain_text") -> Optional[dict]:
    """Extract entities/relations/memories from content using local Gemma 4 model (sync).

    Returns a dict with keys: entities, relations, memories, action_triggers.
    Returns None on failure so callers can fall back to Anthropic.
    """
    import json

    prompt = f"""Extract structured knowledge from this {content_type}.

CONTENT:
{content[:3000]}

Return ONLY a JSON object (no markdown fences):
{{
  "entities": [
    {{"name": "...", "entity_type": "person|company|project|concept|technology|location", "description": "...", "confidence": 0.9}}
  ],
  "relations": [
    {{"from_entity": "...", "relation_type": "works_at|knows|manages|part_of|uses", "to_entity": "..."}}
  ],
  "memories": [
    {{"content": "...", "memory_type": "fact|preference|context"}}
  ],
  "action_triggers": []
}}
If no entities found, return: {{"entities": [], "relations": [], "memories": [], "action_triggers": []}}"""

    # 4096 tokens (~16KB JSON) — chat transcripts with many entities were
    # truncating mid-string at the previous 1200-token cap. Timeout bumped
    # to 90s to match: at ~57 tok/s on M4 GPU, 4096 tokens is ~72s worst case.
    result = generate_sync(
        prompt=prompt,
        model=QUALITY_MODEL,
        system="You are a knowledge extraction agent. Output valid JSON only.",
        temperature=0.0,
        max_tokens=4096,
        timeout=90.0,
        response_format="json",
    )

    if not result:
        return None

    try:
        data = json.loads(_strip_json_fences(result))
        if "entities" in data:
            return data
    except (json.JSONDecodeError, ValueError):
        logger.debug("Failed to parse Gemma 4 knowledge extraction: %s", result[:200])
    return None


def extract_knowledge_with_prompt_sync(prompt: str) -> Optional[dict]:
    """Extract knowledge using a pre-built prompt (preserves tenant entity schemas).

    Accepts the full prompt from KnowledgeExtractionService._build_prompt()
    so tenant-specific extraction rules and schemas are honored.
    Returns a dict with keys: entities, relations, memories, action_triggers.
    Returns None on failure so callers can fall back to Anthropic.
    """
    import json

    # 4096 tokens (~16KB JSON) — chat transcripts with many entities were
    # truncating mid-string at the previous 1200-token cap. Timeout bumped
    # to 90s to match: at ~57 tok/s on M4 GPU, 4096 tokens is ~72s worst case.
    result = generate_sync(
        prompt=prompt,
        model=QUALITY_MODEL,
        system="You are a knowledge extraction agent. Output valid JSON only.",
        temperature=0.0,
        max_tokens=4096,
        timeout=90.0,
        response_format="json",
    )

    if not result:
        logger.warning("extract_knowledge_with_prompt_sync: generate_sync returned empty result")
        return None

    try:
        data = json.loads(_strip_json_fences(result))
        if "entities" in data:
            return data
        logger.warning("extract_knowledge_with_prompt_sync: 'entities' key missing in parsed JSON")
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("Failed to parse Gemma 4 knowledge extraction: %s. Raw result: %s", e, result[:500])
    return None


def classify_task_type_sync(message: str) -> Optional[str]:
    """Classify a user message into a task type using local Gemma 4 model (sync).

    Returns one of: code, data, sales, marketing, knowledge, support, scheduling, general.
    Returns None on failure so callers can fall back to keyword matching.
    """
    prompt = f"""Classify this user message into exactly one task type.

MESSAGE: {message[:300]}

TASK TYPES: code, data, sales, marketing, knowledge, support, scheduling, general

Respond with ONLY the task type word, nothing else."""

    result = generate_sync(
        prompt=prompt,
        model=DEFAULT_MODEL,
        system="You are a task classifier. Output only a single word.",
        temperature=0.0,
        max_tokens=10,
        timeout=10.0,
    )

    if result:
        task_type = result.strip().lower().split()[0] if result.strip() else None
        valid_types = {"code", "data", "sales", "marketing", "knowledge", "support", "scheduling", "general"}
        if task_type in valid_types:
            return task_type
    return None


async def analyze_competitors_local(
    competitors_context: str,
    previous_summary: str = "",
) -> Optional[dict]:
    """Analyze competitor data using local Gemma 4 model.

    Returns dict with observations, notable_changes, summary — or None on failure.
    Replaces the Anthropic call in competitor_monitor.analyze_competitor_changes().
    """
    import json

    previous = f"\n\nPrevious summary:\n{previous_summary}" if previous_summary else ""

    prompt = f"""Analyze these competitors and identify notable changes.{previous}

COMPETITOR DATA:
{competitors_context[:4000]}

Return ONLY a JSON object (no markdown fences):
{{
  "observations": {{
    "competitor_id": "Observation text (1-3 sentences) about current activity"
  }},
  "notable_changes": [
    {{
      "competitor_id": "...",
      "competitor_name": "...",
      "change_type": "new_product|pricing|campaign|partnership|expansion|other",
      "title": "Brief title (max 100 chars)",
      "description": "What changed and why it matters (1-2 sentences)"
    }}
  ],
  "summary": "Overall competitive landscape summary (2-3 sentences)"
}}
If no notable changes, return notable_changes as empty array [].
Keys in observations must match competitor IDs from the data."""

    result = await generate(
        prompt=prompt,
        model=QUALITY_MODEL,
        system=(
            "You are a competitive intelligence analyst. "
            "Analyze competitor data and output only valid JSON."
        ),
        temperature=0.2,
        max_tokens=2000,
        timeout=90.0,
    )

    if not result:
        return None

    try:
        start = result.index("{")
        end = result.rindex("}") + 1
        data = json.loads(result[start:end])
        if "observations" in data:
            data.setdefault("notable_changes", [])
            data.setdefault("summary", "")
            return data
    except (json.JSONDecodeError, ValueError, IndexError):
        logger.debug("Failed to parse Gemma 4 competitor analysis: %s", result[:200])
    return None


def generate_agent_response_sync(
    message: str,
    conversation_summary: str = "",
    memory_context: str = "",
    skill_body: str = "",
    agent_slug: str = "luna",
) -> Optional[str]:
    """Generate an agent response using local Gemma 4 model (sync).

    Used as a fallback when no CLI subscription (Claude Code / Codex) is connected.
    Uses the agent's skill_body as the persona — not hardcoded to Luna.
    Returns response text or None on failure.
    """
    context_parts = []
    if memory_context:
        context_parts.append(f"Long-term memory context:\n{memory_context.strip()}")
    
    if conversation_summary:
        # Truncate history but keep it reasonable
        context_parts.append(f"Recent conversation context:\n{conversation_summary.strip()[-2000:]}")

    context_block = "\n\n".join(context_parts)
    if context_block:
        context_block = "\n\n" + context_block

    prompt = f"""A user sent this message to you:{context_block}

USER: {message[:600]}

Respond in character. Be warm, brief, and conversational — like a smart colleague texting back, not a formal report.
Keep your reply to 1-3 short sentences unless the question truly needs more detail.
Do NOT use markdown headers or bullet-point walls.
Do NOT mention that you are a local model or that any subscription is missing."""

    system = (skill_body.strip()[:1200] + "\n\n") if skill_body else ""
    if not system.strip():
        # Default persona only if no skill body provided
        system = (
            "You are an AI assistant. "
            "You respond in short, direct, conversational messages — never stiff or formal. "
            "Use contractions. React first, then inform. Never start with 'Certainly!' or 'Of course!'. "
            "Always respond in the same language the user writes in."
        )
    # Universal anti-hallucination preamble — same rules as the CLI hot path.
    # Imported lazily to avoid circular imports.
    from app.services.cli_session_manager import ANTI_HALLUCINATION_PREAMBLE
    system = ANTI_HALLUCINATION_PREAMBLE + "\n\n" + system

    return generate_sync(
        prompt=prompt,
        model=QUALITY_MODEL,
        system=system,
        temperature=0.7,
        max_tokens=400,
        timeout=60.0,
    )


# Backward-compatible alias
generate_luna_response_sync = generate_agent_response_sync


def summarize_conversation_sync(conversation_text: str) -> Optional[str]:
    """Summarize a conversation using local Gemma 4 model (sync).

    Returns summary text or None on failure.
    Replaces the Anthropic call in context_manager._generate_summary().
    """
    prompt = f"""Summarize this conversation concisely. Focus on:
- Key questions asked by the user
- Important data points and insights discovered
- SQL queries executed and their results
- Calculations performed
- Patterns or trends identified

Use bullet points. Be factual and structured.

CONVERSATION:
{conversation_text[:4000]}"""

    return generate_sync(
        prompt=prompt,
        model=QUALITY_MODEL,
        system=(
            "You are a conversation summarizer. Create concise, factual summaries "
            "using bullet points. Output plain text only."
        ),
        temperature=0.2,
        max_tokens=800,
        timeout=45.0,
    )


import json

def summarize_chat_window(messages: list[dict]) -> dict:
    """Summarize a chat window with structured output via Gemma4 JSON mode.

    Args:
        messages: list of {"role", "content", "created_at"} dicts.

    Returns:
        {"summary": str, "key_topics": list[str], "key_entities": list[str],
         "mood": str, "messages": list (echoed for downstream count)}
    """
    if not messages:
        return {"summary": "", "key_topics": [], "key_entities": [], "mood": "neutral", "messages": []}

    convo_text = "\n".join(
        f"[{m.get('role', 'user')}] {str(m.get('content', ''))[:500]}" for m in messages[:60]
    )
    prompt = f"""Summarize this conversation window. Return JSON ONLY in this exact shape:
{{"summary": "<2-3 sentence narrative summary>",
  "key_topics": ["topic1", "topic2"],
  "key_entities": ["Person A", "Project B"],
  "mood": "positive|neutral|concerned|escalated"}}

CONVERSATION:
{convo_text[:6000]}"""

    raw = generate_sync(
        prompt=prompt,
        model=QUALITY_MODEL,
        system="You are a conversation summarizer. Output valid JSON only, no prose, no markdown.",
        temperature=0.2,
        max_tokens=500,
        timeout=60.0,
        response_format="json",
    )
    try:
        parsed = json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError):
        parsed = {"summary": (raw or "")[:500], "key_topics": [], "key_entities": [], "mood": "neutral"}

    return {
        "summary": parsed.get("summary", "")[:2000],
        "key_topics": parsed.get("key_topics", [])[:10],
        "key_entities": parsed.get("key_entities", [])[:10],
        "mood": parsed.get("mood", "neutral"),
        "messages": messages,  # passed through for message_count downstream
    }
