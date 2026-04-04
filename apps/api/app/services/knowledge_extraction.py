"""Universal knowledge extraction service.

Supports extracting entities from multiple content types:
- chat_transcript: Chat session messages
- html: Raw HTML content (scraped pages, emails)
- structured_json: Pre-structured JSON data (API responses, CSV-as-JSON)
- plain_text: Free-form text (documents, notes, articles)

Optionally accepts an entity_schema to guide extraction toward specific
fields and entity types (e.g. prospects with name/email/company).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.chat import ChatSession
from app.models.knowledge_entity import KnowledgeEntity
from app.models.knowledge_relation import KnowledgeRelation
from app.models.agent_memory import AgentMemory
from app.services.llm.legacy_service import get_llm_service
from app.services.memory_activity import log_activity
from app.services.orchestration.entity_validator import EntityValidator, ValidationPolicy

logger = logging.getLogger(__name__)

# Supported content types for extract_from_content()
SUPPORTED_CONTENT_TYPES = {"chat_transcript", "html", "structured_json", "plain_text"}

# Maximum characters sent to the LLM to stay within context limits
_MAX_CONTENT_CHARS = 12_000

# Platform-internal terms that should never become entities.
# Lowercase for case-insensitive matching.
ENTITY_BLOCKLIST: set[str] = {
    # Platform internals
    "luna", "servicetsunami", "service tsunami", "agentprovision", "agentprovision.com",
    "adk", "adk service", "adk server", "google adk",
    "mcp", "mcp server",
    # Communication channels (the channels themselves, not contacts)
    "whatsapp", "gmail", "email", "inbox", "calendar",
    "slack", "telegram", "sms",
    # UI / platform concepts
    "dashboard", "workflow", "workflows", "pipeline",
    "knowledge_manager", "knowledge manager", "knowledge base",
    "sales_agent", "sales agent", "data_analyst", "data analyst",
    "report_generator", "report generator",
    "personal_assistant", "personal assistant",
    "agent", "agents", "supervisor", "tool", "tools",
    # Generic noise
    "user", "usuario", "assistant", "bot", "system",
    "api", "database", "server", "client",
}

# Map content_type to source_channel for observation attribution
_SOURCE_CHANNEL_MAP = {
    "chat_transcript": "chat",
    "plain_text": "chat",
    "email": "gmail",
    "calendar": "calendar",
    "html": "web",
}


class KnowledgeExtractionService:
    """Universal entity extraction from arbitrary content sources."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_from_session(
        self,
        db: Session,
        session_id: uuid.UUID,
        tenant_id: uuid.UUID,
        *,
        source_agent_id: Optional[uuid.UUID] = None,
        collection_task_id: Optional[uuid.UUID] = None,
    ) -> Dict[str, Any]:
        """Extract knowledge from a chat session (backward-compat wrapper).

        Loads the ChatSession, converts its messages to a transcript, then
        delegates to :meth:`extract_from_content` with content_type="chat_transcript".

        Returns:
            Dict with keys: entities (List[KnowledgeEntity]), relations (List[dict]),
            memories (List[dict]), action_triggers (List[dict]).
        """
        empty_result = {"entities": [], "relations": [], "memories": [], "action_triggers": []}

        session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
        if not session:
            logger.warning("ChatSession %s not found — skipping extraction", session_id)
            return empty_result

        transcript = ""
        for msg in session.messages:
            transcript += f"{msg.role}: {msg.content}\n"

        if not transcript.strip():
            return empty_result

        return self.extract_from_content(
            db=db,
            tenant_id=tenant_id,
            content=transcript,
            content_type="chat_transcript",
            source_agent_id=source_agent_id,
            collection_task_id=collection_task_id,
        )

    def extract_from_content(
        self,
        db: Session,
        tenant_id: uuid.UUID,
        content: str,
        content_type: str = "plain_text",
        *,
        entity_schema: Optional[Dict[str, Any]] = None,
        source_url: Optional[str] = None,
        source_agent_id: Optional[uuid.UUID] = None,
        collection_task_id: Optional[uuid.UUID] = None,
        activity_source: str = "chat",  # "chat", "gmail", "calendar", "whatsapp"
    ) -> Dict[str, Any]:
        """Extract entities, relations, memories, and action triggers from content.

        Args:
            db: SQLAlchemy session.
            tenant_id: Tenant scope.
            content: Raw content string (transcript, HTML, JSON, text).
            content_type: One of ``SUPPORTED_CONTENT_TYPES``.
            entity_schema: Optional guide for extraction. Example::

                {
                    "fields": ["name", "email", "company"],
                    "entity_type": "prospect"
                }

                When provided the LLM is asked to extract entities matching
                the given fields and assign the specified entity_type.
            source_url: URL the content was collected from (stored on entity).
            source_agent_id: Agent that originated the extraction.
            collection_task_id: AgentTask that triggered the extraction.

        Returns:
            Dict with keys: entities (List[KnowledgeEntity]), relations (List[dict]),
            memories (List[dict]), action_triggers (List[dict]).
        """
        empty_result = self._empty_result()

        if content_type not in SUPPORTED_CONTENT_TYPES:
            logger.error(
                "Unsupported content_type '%s'. Must be one of %s",
                content_type,
                SUPPORTED_CONTENT_TYPES,
            )
            return empty_result

        if not content or not content.strip():
            logger.info("Empty content provided — nothing to extract")
            return empty_result

        # Build the LLM prompt
        prompt = self._build_prompt(content, content_type, entity_schema)

        try:
            # ── Try local Gemma 4 model first (zero token cost) ──
            parsed = None
            try:
                from app.services.local_inference import extract_knowledge_with_prompt_sync as _gemma_extract
                gemma_result = _gemma_extract(prompt)
                if gemma_result is not None:
                    parsed = gemma_result
                    logger.info(
                        "extract_from_content: used local Gemma 4 for content_type=%s (saved Anthropic tokens)",
                        content_type,
                    )
            except Exception as e:
                logger.debug("Gemma 4 knowledge extraction failed (%s) — falling back to Anthropic", e)

            # ── Fall back to Anthropic if Gemma 4 failed ──
            if parsed is None:
                try:
                    llm_service = get_llm_service()
                except ValueError:
                    logger.warning(
                        "LLM service not configured (missing API key). Skipping knowledge extraction."
                    )
                    return empty_result

                response = llm_service.generate_chat_response(
                    user_message=prompt,
                    conversation_history=[],
                    system_prompt="You are a knowledge extraction agent. Output valid JSON only.",
                    temperature=0.0,
                )

                parsed = self._parse_json_response(response.get("text", ""))

            entities_data = parsed.get("entities", [])
            relations_data = parsed.get("relations", [])
            memories_data = parsed.get("memories", [])
            triggers_data = parsed.get("action_triggers", [])

            if not entities_data:
                logger.info("LLM returned no entities for content_type=%s", content_type)
                return {
                    "entities": [],
                    "relations": relations_data,
                    "memories": memories_data,
                    "action_triggers": triggers_data,
                }

            created = self._persist_entities(
                db=db,
                tenant_id=tenant_id,
                entities_data=entities_data,
                entity_schema=entity_schema,
                source_url=source_url,
                source_agent_id=source_agent_id,
                collection_task_id=collection_task_id,
                content_type=content_type,
            )

            relations_created = self._persist_relations(db, tenant_id, relations_data)
            memories_created = self._persist_memories(db, tenant_id, memories_data)

            # --- Activity logging ---
            for entity in created:
                try:
                    log_activity(
                        db, tenant_id, "entity_created",
                        f'Extracted "{entity.name}" ({entity.entity_type})',
                        source=activity_source, entity_id=entity.id,
                    )
                except Exception:
                    logger.debug("Failed to log entity activity for %s", entity.name)

            if relations_created:
                try:
                    log_activity(
                        db, tenant_id, "relation_created",
                        f"Discovered {relations_created} relations",
                        source=activity_source,
                    )
                except Exception:
                    logger.debug("Failed to log relation activity")

            if memories_created:
                try:
                    log_activity(
                        db, tenant_id, "memory_created",
                        f"Learned {memories_created} new memories",
                        source=activity_source,
                    )
                except Exception:
                    logger.debug("Failed to log memory activity")

            logger.info(
                "Extracted %d entities (%d new), %d relations (%d persisted), "
                "%d memories (%d persisted), %d triggers from content_type=%s",
                len(entities_data),
                len(created),
                len(relations_data),
                relations_created,
                len(memories_data),
                memories_created,
                len(triggers_data),
                content_type,
            )
            return {
                "entities": created,
                "relations": relations_data,
                "memories": memories_data,
                "action_triggers": triggers_data,
            }

        except Exception as e:
            logger.error("Knowledge extraction failed: %s", e)
            return empty_result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_prompt(
        content: str,
        content_type: str,
        entity_schema: Optional[Dict[str, Any]],
    ) -> str:
        """Build an LLM prompt tailored to the content type and optional schema."""

        type_instructions = {
            "chat_transcript": (
                "Analyze the following chat transcript and extract key entities "
                "(people, companies, products, concepts) and facts."
            ),
            "html": (
                "Analyze the following HTML content. Ignore boilerplate navigation and ads. "
                "Extract key entities (people, companies, products, locations, concepts) from "
                "the meaningful body content."
            ),
            "structured_json": (
                "Analyze the following structured JSON data. Each object or record may "
                "represent an entity. Extract all distinct entities with their attributes."
            ),
            "plain_text": (
                "Analyze the following text and extract key entities "
                "(people, companies, products, concepts, locations) and facts."
            ),
        }

        parts: List[str] = []
        parts.append(type_instructions[content_type])

        # Schema-guided extraction
        if entity_schema:
            fields = entity_schema.get("fields", [])
            e_type = entity_schema.get("entity_type", "entity")
            parts.append(
                f"\nFocus on extracting entities of type '{e_type}'. "
                f"For each entity, try to capture these fields: {', '.join(fields)}. "
                "Include any of these fields you can identify as keys inside the 'attributes' object."
            )

        parts.append(
            "\nExtract entities as a JSON array of objects. Each object must have:\n"
            '- "name": string (the entity\'s canonical name — use proper capitalization, e.g. "John Smith" not "john smith")\n'
            '- "type": string (one of: person, organization, product, location, event, opportunity, task, concept)\n'
            '- "category": string (one of: lead, contact, customer, investor, partner, competitor, '
            'vendor, prospect, person, organization, location, product, event, opportunity, task, concept)\n'
            '- "description": string (1-2 sentence description of who/what this entity is)\n'
            '- "confidence": number between 0.0 and 1.0\n'
            '- "attributes": object (optional extra key-value pairs like email, phone, company, role, url, address)\n'
            '- "sentiment": string (the user\'s emotional attitude toward this entity: positive, negative, neutral, excited, frustrated, or curious)\n'
            "\nIMPORTANT RULES:\n"
            "- DO NOT extract platform/tool names (WhatsApp, Gmail, Slack, Luna, etc.) as entities\n"
            "- DO NOT extract generic terms (user, assistant, bot, agent, system, workflow, etc.)\n"
            "- Normalize entity names: use the most complete, proper form (e.g. 'Dr. Maria Garcia' not 'maria')\n"
            "- If the same entity appears multiple times with slight variations, use ONE canonical name\n"
            "- Assign the most specific category that fits (e.g. 'lead' for a sales prospect, 'contact' for a known person)\n"
        )

        parts.append(
            "\nAlso extract:\n"
            "\nRELATIONS between entities (if any are apparent):\n"
            'Return as "relations" array. Each object:\n'
            '- "from": string (source entity name, must match an entity name above)\n'
            '- "to": string (target entity name, must match an entity name above)\n'
            '- "type": string (one of: works_at, knows, manages, reports_to, purchased, prefers, related_to, part_of, located_in, competes_with, owns)\n'
            '- "confidence": number 0.0-1.0\n'
            '- "evidence": string (brief text explaining why this relation exists)\n'
            "\nMEMORIES — things learned about the user from this conversation:\n"
            'Return as "memories" array. Each object:\n'
            '- "type": string (one of: preference, fact, experience, decision, goal, working_style)\n'
            '- "content": string (the memory in natural language, e.g. "User prefers email over phone for follow-ups")\n'
            '- "importance": number 0.0-1.0\n'
            '- "source": string (how this was learned, e.g. "stated in conversation", "inferred from behavior")\n'
            "\nBe GENEROUS with memory extraction. Capture:\n"
            "- Communication preferences (channels, tone, language, timing)\n"
            "- Business priorities and goals mentioned\n"
            "- Working style cues (prefers quick updates vs detailed reports, morning vs evening)\n"
            "- Industry focus, target markets, or verticals they care about\n"
            "- Tools, technologies, or workflows they mention using\n"
            "- Decisions made during the conversation\n"
            "- Personal context shared (timezone, role, team structure)\n"
            "- Feedback on agent behavior (liked/disliked something the agent did)\n"
            "Do NOT include memories about external entities (those are captured as entity attributes).\n"
            "\nACTION TRIGGERS — if the user explicitly requests a reminder, follow-up, or scheduled action:\n"
            'Return as "action_triggers" array. Each object:\n'
            '- "type": string (one of: reminder, follow_up, research, auto_reply)\n'
            '- "description": string (what should happen)\n'
            '- "delay_hours": number (how many hours to wait, 0 for immediate)\n'
            '- "entity_name": string (related entity name if any)\n'
            "\nOnly include action triggers for EXPLICIT user requests (e.g. \"remind me in 3 days\", \"follow up next week\"). "
            "Do NOT infer actions the user didn't ask for.\n"
            '\nReturn the COMPLETE result as a JSON object:\n'
            '{"entities": [...], "relations": [...], "memories": [...], "action_triggers": [...]}\n'
            "\nIf any section has no items, return an empty array for it."
        )

        # Truncate content to avoid blowing up context window
        truncated = content[:_MAX_CONTENT_CHARS]
        parts.append(f"\nContent:\n{truncated}")

        return "\n".join(parts)

    @staticmethod
    def _empty_result() -> Dict[str, Any]:
        """Return an empty extraction result dict."""
        return {"entities": [], "relations": [], "memories": [], "action_triggers": []}

    @staticmethod
    def _parse_json_response(text: str) -> Dict[str, Any]:
        """Extract a JSON object from an LLM response, handling markdown fences.

        Returns a dict with keys: entities, relations, memories, action_triggers.
        Backward-compatible: raw arrays are treated as entity-only results.
        """
        empty: Dict[str, Any] = {"entities": [], "relations": [], "memories": [], "action_triggers": []}

        if not text:
            return empty

        cleaned = text.strip()

        # Strip markdown code fences
        if "```json" in cleaned:
            cleaned = cleaned.split("```json", 1)[1].split("```", 1)[0]
        elif "```" in cleaned:
            cleaned = cleaned.split("```", 1)[1].split("```", 1)[0]

        cleaned = cleaned.strip()

        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning("Failed to parse LLM JSON response: %.200s", cleaned)
            return empty

        # Full dict with expected keys -> return it (filling missing keys)
        if isinstance(parsed, dict) and "entities" in parsed:
            return {
                "entities": parsed.get("entities", []) if isinstance(parsed.get("entities"), list) else [],
                "relations": parsed.get("relations", []) if isinstance(parsed.get("relations"), list) else [],
                "memories": parsed.get("memories", []) if isinstance(parsed.get("memories"), list) else [],
                "action_triggers": parsed.get("action_triggers", []) if isinstance(parsed.get("action_triggers"), list) else [],
            }

        # Legacy dict formats (results, data, items) -> entity-only
        if isinstance(parsed, dict):
            for key in ("results", "data", "items"):
                if key in parsed and isinstance(parsed[key], list):
                    return {**empty, "entities": parsed[key]}
            return empty

        # Raw list -> treat as entities (backward compat)
        if isinstance(parsed, list):
            return {**empty, "entities": parsed}

        return empty

    @staticmethod
    def _persist_entities(
        db: Session,
        tenant_id: uuid.UUID,
        entities_data: List[Dict[str, Any]],
        entity_schema: Optional[Dict[str, Any]],
        source_url: Optional[str],
        source_agent_id: Optional[uuid.UUID],
        collection_task_id: Optional[uuid.UUID],
        content_type: str = "plain_text",
        source_ref: Optional[str] = None,
    ) -> List[KnowledgeEntity]:
        """Validate, deduplicate, and persist extracted entities.

        Uses EntityValidator for enterprise guardrails (rate limits, dedup,
        content validation) before persisting to the knowledge graph.
        Checks for contradictions (entity_type mismatch) and logs disputed
        world state assertions when conflicts are found.
        """
        default_type = entity_schema.get("entity_type", "concept") if entity_schema else "concept"

        # Build validation policy from schema
        dedup_fields = ["name", "entity_type"]
        if entity_schema and "dedup_on" in entity_schema:
            dedup_fields = entity_schema["dedup_on"]

        policy = ValidationPolicy(
            required_fields=["name"],
            dedup_fields=dedup_fields,
        )

        # Validate batch through EntityValidator
        validator = EntityValidator(db, tenant_id)
        result = validator.validate_batch(entities_data, policy, collection_task_id)

        if result.errors:
            for err in result.errors:
                logger.warning("Validation: %s", err)

        if result.rejected_entities:
            logger.warning("Rejected %d entities", len(result.rejected_entities))

        # Batch-load existing entities by name to avoid N+1 queries during contradiction check
        all_names = [
            item.get("name", "").strip().lower()
            for item in result.valid_entities
            if item.get("name", "").strip()
        ]
        existing_by_name: Dict[str, KnowledgeEntity] = {}
        if all_names:
            rows = db.query(KnowledgeEntity).filter(
                KnowledgeEntity.tenant_id == tenant_id,
                func.lower(KnowledgeEntity.name).in_(all_names),
            ).all()
            for row in rows:
                existing_by_name[row.name.lower()] = row

        # Import once outside the loop
        from app.models.world_state import WorldStateAssertion

        # Persist valid entities
        created: List[KnowledgeEntity] = []
        blocked_count = 0
        for item in result.valid_entities:
            name = item.get("name", "").strip()
            if not name:
                continue

            # Skip blocklisted entities
            if name.lower() in ENTITY_BLOCKLIST:
                blocked_count += 1
                continue

            # Determine entity_type: schema override > LLM output > default
            if entity_schema and entity_schema.get("entity_type"):
                entity_type = entity_schema["entity_type"].lower()
            else:
                entity_type = (item.get("type") or default_type).lower()

            # Category from LLM output (falls back to entity_type)
            category = (item.get("category") or entity_type).lower()

            # --- Contradiction detection: check for entity_type mismatch ---
            # Uses pre-loaded batch — no extra DB query per entity
            existing = existing_by_name.get(name.lower())
            if existing and existing.entity_type != entity_type:
                try:
                    dispute = WorldStateAssertion(
                        tenant_id=tenant_id,
                        subject_entity_id=existing.id,
                        subject_slug=name,
                        attribute_path="entity_type",
                        value_json={"type": entity_type, "source": content_type},
                        previous_value_json={"type": existing.entity_type},
                        confidence=0.5,
                        source_type=_SOURCE_CHANNEL_MAP.get(content_type, "chat"),
                        status="disputed",
                        dispute_reason=f"Existing: {existing.entity_type}, New extraction: {entity_type}",
                    )
                    db.add(dispute)
                    logger.info(
                        "Contradiction detected for '%s': existing=%s vs new=%s — skipping entity creation",
                        name, existing.entity_type, entity_type,
                    )
                except Exception:
                    logger.debug("Contradiction check failed for '%s'", name, exc_info=True)
                # Skip creating a conflicting entity — flag it for user to resolve
                continue

            # Description as a top-level field
            description = item.get("description", "")

            # Structured attributes (email, phone, company, role, etc.)
            attributes: Dict[str, Any] = {}
            if isinstance(item.get("attributes"), dict):
                attributes.update(item["attributes"])

            confidence = float(item.get("confidence", 0.8))

            entity = KnowledgeEntity(
                tenant_id=tenant_id,
                name=name,
                entity_type=entity_type,
                category=category,
                description=description or None,
                attributes=attributes or None,
                confidence=confidence,
                source_agent_id=source_agent_id,
                status="draft",
                collection_task_id=collection_task_id,
                source_url=source_url,
            )
            db.add(entity)
            created.append(entity)

        # Build a lookup of entity name → sentiment from extracted data
        _sentiment_map: Dict[str, str] = {}
        for item in result.valid_entities:
            _name = (item.get("name") or "").strip().lower()
            _sent = item.get("sentiment")
            if _name and _sent:
                _sentiment_map[_name] = _sent

        if created:
            db.commit()
            # Create observations from entity descriptions with source attribution
            source_channel = _SOURCE_CHANNEL_MAP.get(content_type, "chat")
            source_ref = f"{source_channel} {datetime.utcnow().strftime('%b %d')}"
            for entity in created:
                if entity.description:
                    try:
                        from app.services.knowledge import create_observation
                        sentiment = _sentiment_map.get(entity.name.lower(), "neutral")
                        create_observation(
                            db, tenant_id,
                            observation_text=entity.description,
                            observation_type="extracted",
                            source_type=content_type or "conversation",
                            entity_id=entity.id,
                            confidence=entity.confidence or 0.8,
                            source_channel=source_channel,
                            source_ref=source_ref,
                            sentiment=sentiment,
                        )
                    except Exception:
                        logger.debug("Failed to create observation for entity %s", entity.name)
            db.commit()

        if blocked_count:
            logger.info("Blocked %d noise entities via blocklist", blocked_count)

        logger.info(
            "Persisted %d entities, skipped %d dupes, rejected %d",
            len(created),
            result.duplicates_skipped,
            len(result.rejected_entities),
        )
        return created

    @staticmethod
    def _persist_relations(
        db: Session,
        tenant_id: uuid.UUID,
        relations_data: List[Dict[str, Any]],
    ) -> int:
        """Persist extracted relations by resolving entity names to IDs."""
        if not relations_data:
            return 0

        created_count = 0
        for rel in relations_data:
            from_name = rel.get("from", "").strip()
            to_name = rel.get("to", "").strip()
            rel_type = rel.get("type", "related_to")

            if not from_name or not to_name:
                continue

            # Resolve entity names to IDs via ILIKE
            from_entity = db.query(KnowledgeEntity).filter(
                KnowledgeEntity.tenant_id == tenant_id,
                KnowledgeEntity.name.ilike(from_name),
            ).first()
            to_entity = db.query(KnowledgeEntity).filter(
                KnowledgeEntity.tenant_id == tenant_id,
                KnowledgeEntity.name.ilike(to_name),
            ).first()

            if not from_entity or not to_entity:
                logger.debug("Skipping relation %s->%s: entity not found", from_name, to_name)
                continue

            # Check for duplicate
            existing = db.query(KnowledgeRelation).filter(
                KnowledgeRelation.tenant_id == tenant_id,
                KnowledgeRelation.from_entity_id == from_entity.id,
                KnowledgeRelation.to_entity_id == to_entity.id,
                KnowledgeRelation.relation_type == rel_type,
            ).first()
            if existing:
                continue

            relation = KnowledgeRelation(
                tenant_id=tenant_id,
                from_entity_id=from_entity.id,
                to_entity_id=to_entity.id,
                relation_type=rel_type,
                strength=float(rel.get("confidence", 0.8)),
                evidence={"text": rel.get("evidence", "")},
            )
            db.add(relation)
            created_count += 1

        if created_count:
            db.commit()
        logger.info("Persisted %d relations", created_count)
        return created_count

    @staticmethod
    def _persist_memories(
        db: Session,
        tenant_id: uuid.UUID,
        memories_data: List[Dict[str, Any]],
    ) -> int:
        """Persist extracted agent memories (user preferences, facts, decisions)."""
        if not memories_data:
            return 0

        # Resolve a default agent_id for this tenant (Luna's agent)
        from app.models.agent import Agent
        agent = db.query(Agent).filter(Agent.tenant_id == tenant_id).first()
        if not agent:
            logger.warning("No agent found for tenant %s — cannot store memories", tenant_id)
            return 0

        created_count = 0
        for mem in memories_data:
            content = mem.get("content", "").strip()
            memory_type = mem.get("type", "fact")
            if not content:
                continue

            # Valid types
            if memory_type not in ("preference", "fact", "experience", "decision", "skill", "relationship", "procedure", "goal", "working_style"):
                memory_type = "fact"

            # Dedup: skip if similar content exists
            existing = db.query(AgentMemory).filter(
                AgentMemory.agent_id == agent.id,
                AgentMemory.tenant_id == tenant_id,
                AgentMemory.memory_type == memory_type,
                AgentMemory.content.ilike(f"%{content[:50]}%"),
            ).first()
            if existing:
                continue

            memory = AgentMemory(
                agent_id=agent.id,
                tenant_id=tenant_id,
                memory_type=memory_type,
                content=content,
                importance=float(mem.get("importance", 0.5)),
                source=mem.get("source", "conversation"),
            )
            db.add(memory)
            created_count += 1

        if created_count:
            db.commit()
        logger.info("Persisted %d agent memories", created_count)
        return created_count


# Module-level singleton
knowledge_extraction_service = KnowledgeExtractionService()
