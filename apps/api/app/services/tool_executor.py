"""Tool execution framework for agents."""
from __future__ import annotations

from typing import Dict, Any, List, Optional
from abc import ABC, abstractmethod
import json


class ToolResult:
    """Standardized result from tool execution."""

    def __init__(
        self,
        success: bool,
        data: Any = None,
        error: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ):
        self.success = success
        self.data = data
        self.error = error
        self.metadata = metadata or {}

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "metadata": self.metadata
        }

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), default=str)


class Tool(ABC):
    """Base class for all executable tools."""

    def __init__(self, name: str, description: str, alias: Optional[str] = None):
        self.name = alias or name
        self.original_name = name
        self.description = description

    @abstractmethod
    def get_schema(self) -> Dict[str, Any]:
        """
        Return the tool's parameter schema for LLM tool use.

        Returns:
            Dictionary with tool definition including input_schema
        """
        pass

    @abstractmethod
    def execute(self, **kwargs) -> ToolResult:
        """
        Execute the tool with given parameters.

        Args:
            **kwargs: Tool-specific parameters

        Returns:
            ToolResult with success status, data, and optional error
        """
        pass

    def validate_params(self, params: Dict[str, Any]) -> bool:
        """
        Validate parameters against schema.

        Args:
            params: Parameters to validate

        Returns:
            True if valid, False otherwise
        """
        schema = self.get_schema()
        required = schema.get("input_schema", {}).get("required", [])

        for field in required:
            if field not in params:
                return False
        return True


class LeadScoringTool(Tool):
    """Tool for computing a configurable composite score for knowledge entities.

    Supports multiple scoring rubrics:
    - ai_lead: AI orchestration platform lead scoring (default)
    - hca_deal: M&A sell-likelihood for investment banking
    - marketing_signal: Marketing engagement and intent scoring
    - Custom rubrics via agent kit configuration
    """

    def __init__(self, db, tenant_id, rubric_id=None, custom_rubric=None):
        super().__init__(
            name="lead_scoring",
            description="Compute a composite score (0-100) for a knowledge entity using a configurable scoring rubric"
        )
        self.db = db
        self.tenant_id = tenant_id
        self.rubric_id = rubric_id or "ai_lead"
        self.custom_rubric = custom_rubric

    def _get_rubric(self):
        """Get the scoring rubric to use."""
        if self.custom_rubric:
            return self.custom_rubric
        from app.services.scoring_rubrics import get_rubric
        rubric = get_rubric(self.rubric_id)
        if not rubric:
            from app.services.scoring_rubrics import get_rubric as get_default
            rubric = get_default("ai_lead")
        return rubric

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": {
                    "entity_id": {
                        "type": "string",
                        "description": "UUID of the entity to score"
                    },
                    "entity_name": {
                        "type": "string",
                        "description": "Name of the entity to score (used if entity_id not provided)"
                    },
                    "rubric_id": {
                        "type": "string",
                        "description": "Scoring rubric to use: ai_lead, hca_deal, marketing_signal"
                    },
                },
                "required": []
            }
        }

    def execute(self, **kwargs) -> ToolResult:
        try:
            import uuid as uuid_mod
            import re
            from datetime import datetime
            from app.models.knowledge_entity import KnowledgeEntity
            from app.models.knowledge_relation import KnowledgeRelation

            entity_id = kwargs.get("entity_id")
            entity_name = kwargs.get("entity_name")
            # Allow overriding rubric_id per-call
            rubric_id_override = kwargs.get("rubric_id")
            if rubric_id_override:
                self.rubric_id = rubric_id_override

            if not entity_id and not entity_name:
                return ToolResult(success=False, error="Either entity_id or entity_name is required")

            # Find the entity
            if entity_id:
                entity = self.db.query(KnowledgeEntity).filter(
                    KnowledgeEntity.id == uuid_mod.UUID(entity_id),
                    KnowledgeEntity.tenant_id == self.tenant_id,
                ).first()
            else:
                entity = self.db.query(KnowledgeEntity).filter(
                    KnowledgeEntity.tenant_id == self.tenant_id,
                    KnowledgeEntity.name.ilike(f"%{entity_name}%"),
                ).first()

            if not entity:
                return ToolResult(success=False, error=f"Entity not found: {entity_id or entity_name}")

            # Load relations and related entities
            relations = self.db.query(KnowledgeRelation).filter(
                (KnowledgeRelation.from_entity_id == entity.id) |
                (KnowledgeRelation.to_entity_id == entity.id)
            ).all()

            relations_text = ""
            for rel in relations:
                other_id = rel.to_entity_id if rel.from_entity_id == entity.id else rel.from_entity_id
                other = self.db.query(KnowledgeEntity).filter(KnowledgeEntity.id == other_id).first()
                if other:
                    direction = "→" if rel.from_entity_id == entity.id else "←"
                    relations_text += f"- {direction} {rel.relation_type}: {other.name} ({other.entity_type}, {other.category})\n"
                    if other.properties:
                        relations_text += f"  Properties: {json.dumps(other.properties)[:200]}\n"

            if not relations_text:
                relations_text = "No related entities found."

            # Get the rubric
            rubric = self._get_rubric()
            prompt_template = rubric["prompt_template"]
            system_prompt = rubric.get("system_prompt", "You are a scoring engine. Return only valid JSON.")

            # Build the prompt
            prompt = prompt_template.format(
                name=entity.name,
                entity_type=entity.entity_type or "",
                category=entity.category or "",
                description=entity.description or "No description",
                properties=json.dumps(entity.properties) if entity.properties else "None",
                enrichment_data=json.dumps(entity.enrichment_data)[:500] if entity.enrichment_data else "None",
                source_url=entity.source_url or "None",
                relations_text=relations_text,
            )

            # Call LLM via legacy service (direct Anthropic)
            from app.services.llm.legacy_service import LLMService as LegacyLLMService
            llm = LegacyLLMService()
            response = llm.generate_chat_response(
                user_message=prompt,
                conversation_history=[],
                system_prompt=system_prompt,
                max_tokens=1024,
                temperature=0.3,
            )
            response_text = response.get("text", "")

            # Parse response
            json_match = re.search(r'\{[\s\S]*\}', response_text)
            if not json_match:
                return ToolResult(success=False, error="LLM did not return valid JSON")

            result = json.loads(json_match.group())
            score = max(0, min(100, int(result.get("score", 0))))
            breakdown = result.get("breakdown", {})
            reasoning = result.get("reasoning", "")

            # Write score to entity
            entity.score = score
            entity.scored_at = datetime.utcnow()
            entity.scoring_rubric_id = self.rubric_id
            props = entity.properties or {}
            props["score_breakdown"] = breakdown
            props["score_reasoning"] = reasoning
            props["scoring_rubric_id"] = self.rubric_id
            entity.properties = props
            self.db.commit()
            self.db.refresh(entity)

            return ToolResult(
                success=True,
                data={
                    "entity_id": str(entity.id),
                    "entity_name": entity.name,
                    "score": score,
                    "breakdown": breakdown,
                    "reasoning": reasoning,
                    "scored_at": entity.scored_at.isoformat(),
                    "rubric_id": self.rubric_id,
                    "rubric_name": rubric.get("name", self.rubric_id),
                },
                metadata={"entity_type": entity.entity_type, "category": entity.category}
            )
        except json.JSONDecodeError as e:
            return ToolResult(success=False, error=f"Failed to parse LLM scoring response: {str(e)}")
        except Exception as e:
            return ToolResult(success=False, error=f"Lead scoring failed: {str(e)}")


class ToolRegistry:
    """Registry for managing available tools."""

    def __init__(self):
        self._tools: Dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool

    def unregister(self, tool_name: str) -> None:
        """Unregister a tool."""
        if tool_name in self._tools:
            del self._tools[tool_name]

    def get_tool(self, tool_name: str) -> Optional[Tool]:
        """Get a tool by name."""
        return self._tools.get(tool_name)

    def list_tools(self) -> List[str]:
        """Get list of registered tool names."""
        return list(self._tools.keys())

    def get_all_schemas(self) -> List[Dict[str, Any]]:
        """Get schemas for all registered tools (for LLM tool use)."""
        return [tool.get_schema() for tool in self._tools.values()]

    def execute_tool(self, tool_name: str, **kwargs) -> ToolResult:
        """Execute a tool by name."""
        tool = self.get_tool(tool_name)

        if not tool:
            return ToolResult(
                success=False,
                error=f"Tool '{tool_name}' not found"
            )

        if not tool.validate_params(kwargs):
            return ToolResult(
                success=False,
                error=f"Invalid parameters for tool '{tool_name}'"
            )

        return tool.execute(**kwargs)


# Singleton registry
_tool_registry: Optional[ToolRegistry] = None


def get_tool_registry() -> ToolRegistry:
    """Get or create the global tool registry."""
    global _tool_registry
    if _tool_registry is None:
        _tool_registry = ToolRegistry()
    return _tool_registry


# Only LeadScoringTool remains — actively used by
# `app.services.knowledge.score_entity` (live endpoint
# `POST /api/v1/knowledge/{id}/score`). The other Tool classes
# (SQLQueryTool, CalculatorTool, DataSummaryTool, EntityExtractionTool,
# KnowledgeSearchTool, ReportGenerationTool) were removed 2026-04-26
# after a 30-day audit showed zero `skill_executions` and no live
# importers outside the archive. CLIs (Claude Code / Gemini / Codex)
# handle math, shell, file ops, and SQL natively; the corresponding
# MCP tools (`find_entities`, `search_knowledge`, `query_sql`) cover
# the knowledge-side equivalents.
TOOL_CLASS_REGISTRY = {
    "LeadScoringTool": LeadScoringTool,
}
