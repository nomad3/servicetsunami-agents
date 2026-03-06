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


class SQLQueryTool(Tool):
    """Tool for executing SQL queries on datasets."""

    def __init__(self, dataset_service, dataset, alias: Optional[str] = None):
        super().__init__(
            name="sql_query",
            description=f"Execute SQL queries on the dataset '{dataset.name}' to retrieve and analyze data" if alias else "Execute SQL queries on the current dataset to retrieve and analyze data",
            alias=alias
        )
        self.dataset_service = dataset_service
        self.dataset = dataset

    def get_schema(self) -> Dict[str, Any]:
        """Get schema for SQL query tool."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "SQL query to execute. Table name is 'dataset'. Only SELECT queries allowed."
                    },
                    "explanation": {
                        "type": "string",
                        "description": "Brief explanation of what this query will find"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of rows to return (default: 100, max: 1000)",
                        "default": 100
                    }
                },
                "required": ["sql", "explanation"]
            }
        }

    def execute(self, **kwargs) -> ToolResult:
        """Execute SQL query on dataset."""
        try:
            sql = kwargs.get("sql")
            limit = kwargs.get("limit", 100)
            explanation = kwargs.get("explanation", "")

            if not sql:
                return ToolResult(
                    success=False,
                    error="SQL query is required"
                )

            result = self.dataset_service.execute_query(
                self.dataset,
                sql,
                limit=limit
            )

            return ToolResult(
                success=True,
                data=result,
                metadata={
                    "explanation": explanation,
                    "query": sql,
                    "row_count": result.get("row_count", 0)
                }
            )

        except Exception as e:
            return ToolResult(
                success=False,
                error=f"Query execution failed: {str(e)}"
            )


class CalculatorTool(Tool):
    """Tool for performing calculations."""

    def __init__(self):
        super().__init__(
            name="calculator",
            description="Perform mathematical calculations and return results"
        )

    def get_schema(self) -> Dict[str, Any]:
        """Get schema for calculator tool."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "Mathematical expression to evaluate (e.g., '(100 + 50) * 2')"
                    },
                    "explanation": {
                        "type": "string",
                        "description": "Explanation of what is being calculated"
                    }
                },
                "required": ["expression", "explanation"]
            }
        }

    def execute(self, **kwargs) -> ToolResult:
        """Execute calculation."""
        try:
            expression = kwargs.get("expression", "")
            explanation = kwargs.get("explanation", "")

            if not expression:
                return ToolResult(
                    success=False,
                    error="Expression is required"
                )

            # Safe evaluation - only allow basic math operations
            allowed_chars = set("0123456789+-*/() .")
            if not all(c in allowed_chars for c in expression):
                return ToolResult(
                    success=False,
                    error="Expression contains invalid characters. Only numbers and +, -, *, /, (, ) are allowed."
                )

            result = eval(expression, {"__builtins__": {}}, {})

            return ToolResult(
                success=True,
                data={"result": result, "expression": expression},
                metadata={"explanation": explanation}
            )

        except Exception as e:
            return ToolResult(
                success=False,
                error=f"Calculation failed: {str(e)}"
            )


class DataSummaryTool(Tool):
    """Tool for getting statistical summaries of datasets."""

    def __init__(self, dataset_service, dataset, alias: Optional[str] = None):
        super().__init__(
            name="data_summary",
            description=f"Get statistical summary of the dataset '{dataset.name}' including averages, min, max for numeric columns" if alias else "Get statistical summary of the dataset including averages, min, max for numeric columns",
            alias=alias
        )
        self.dataset_service = dataset_service
        self.dataset = dataset

    def get_schema(self) -> Dict[str, Any]:
        """Get schema for data summary tool."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": {
                    "column": {
                        "type": "string",
                        "description": "Optional: Specific column to summarize. If not provided, summarizes all numeric columns."
                    }
                },
                "required": []
            }
        }

    def execute(self, **kwargs) -> ToolResult:
        """Get data summary."""
        try:
            summary = self.dataset_service.run_summary_query(self.dataset)
            column = kwargs.get("column")

            if column:
                # Filter to specific column
                for col_stats in summary.get("numeric_columns", []):
                    if col_stats["column"] == column:
                        return ToolResult(
                            success=True,
                            data=col_stats,
                            metadata={"column": column}
                        )

                return ToolResult(
                    success=False,
                    error=f"Column '{column}' not found or is not numeric"
                )

            return ToolResult(
                success=True,
                data=summary,
                metadata={"summary_type": "all_numeric_columns"}
            )

        except Exception as e:
            return ToolResult(
                success=False,
                error=f"Failed to get summary: {str(e)}"
            )


class ReportGenerationTool(Tool):
    """Tool for generating structured reports with visualizations."""

    def __init__(self, dataset_service, dataset, alias: Optional[str] = None):
        super().__init__(
            name="generate_report",
            description=f"Generate a structured report with visualizations (bar, line, pie charts) from the dataset '{dataset.name}'." if alias else "Generate a structured report with visualizations (bar, line, pie charts) from the dataset.",
            alias=alias
        )
        self.dataset_service = dataset_service
        self.dataset = dataset

    def get_schema(self) -> Dict[str, Any]:
        """Get schema for report generation tool."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Title of the report or visualization"
                    },
                    "sql": {
                        "type": "string",
                        "description": "SQL query to fetch data. Table name is 'dataset'. Only SELECT queries allowed."
                    },
                    "chart_type": {
                        "type": "string",
                        "enum": ["bar", "line", "pie", "table", "metric"],
                        "description": "Type of visualization to generate"
                    },
                    "x_axis": {
                        "type": "string",
                        "description": "Column name for X-axis (required for bar/line charts)"
                    },
                    "y_axis": {
                        "type": "string",
                        "description": "Column name for Y-axis (required for bar/line charts)"
                    },
                    "description": {
                        "type": "string",
                        "description": "Brief description of the insight or finding"
                    }
                },
                "required": ["title", "sql", "chart_type"]
            }
        }

    def execute(self, **kwargs) -> ToolResult:
        """Execute report generation."""
        try:
            sql = kwargs.get("sql")
            chart_type = kwargs.get("chart_type")
            title = kwargs.get("title")
            x_axis = kwargs.get("x_axis")
            y_axis = kwargs.get("y_axis")
            description = kwargs.get("description", "")

            if not sql:
                return ToolResult(success=False, error="SQL query is required")

            # Execute the query
            result = self.dataset_service.execute_query(
                self.dataset,
                sql,
                limit=100  # Reasonable limit for charts
            )

            rows = result.get("rows", [])

            # Validate data for charts
            if chart_type in ["bar", "line"] and rows:
                if x_axis and x_axis not in rows[0]:
                    return ToolResult(success=False, error=f"X-axis column '{x_axis}' not found in query results")
                if y_axis and y_axis not in rows[0]:
                    return ToolResult(success=False, error=f"Y-axis column '{y_axis}' not found in query results")

            return ToolResult(
                success=True,
                data={
                    "type": "report_visualization",
                    "chart_type": chart_type,
                    "title": title,
                    "description": description,
                    "data": rows,
                    "config": {
                        "x_axis": x_axis,
                        "y_axis": y_axis
                    }
                },
                metadata={
                    "row_count": len(rows),
                    "query": sql
                }
            )

        except Exception as e:
            return ToolResult(
                success=False,
                error=f"Report generation failed: {str(e)}"
            )


class EntityExtractionTool(Tool):
    """Tool for extracting entities from text content."""

    def __init__(self, db, tenant_id):
        super().__init__(
            name="entity_extraction",
            description="Extract people, companies, and concepts from text content and store them in the knowledge graph"
        )
        self.db = db
        self.tenant_id = tenant_id

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The text content to extract entities from"
                    },
                    "content_type": {
                        "type": "string",
                        "description": "Type of content: plain_text, html, json, transcript",
                        "default": "plain_text"
                    },
                    "entity_schema": {
                        "type": "object",
                        "description": "Optional schema to guide extraction (e.g. {\"fields\": [\"name\", \"email\"], \"entity_type\": \"prospect\"})"
                    }
                },
                "required": ["content"]
            }
        }

    def execute(self, **kwargs) -> ToolResult:
        try:
            content = kwargs.get("content")
            if not content:
                return ToolResult(success=False, error="content is required")

            content_type = kwargs.get("content_type", "plain_text")
            entity_schema = kwargs.get("entity_schema")

            from app.services.knowledge_extraction import KnowledgeExtractionService
            service = KnowledgeExtractionService()
            result = service.extract_from_content(
                self.db,
                self.tenant_id,
                content,
                content_type,
                entity_schema=entity_schema,
            )
            entities = result.get("entities", [])

            return ToolResult(
                success=True,
                data=[{
                    "id": str(e.id),
                    "name": e.name,
                    "entity_type": e.entity_type,
                    "properties": e.properties,
                } for e in entities],
                metadata={"entity_count": len(entities)}
            )
        except Exception as e:
            return ToolResult(success=False, error=f"Entity extraction failed: {str(e)}")


class KnowledgeSearchTool(Tool):
    """Tool for searching the knowledge graph."""

    def __init__(self, db, tenant_id):
        super().__init__(
            name="knowledge_search",
            description="Search and browse entities in the knowledge graph"
        )
        self.db = db
        self.tenant_id = tenant_id

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query to match entity names"
                    },
                    "entity_type": {
                        "type": "string",
                        "description": "Optional filter by entity type (e.g. person, company, concept)"
                    }
                },
                "required": ["query"]
            }
        }

    def execute(self, **kwargs) -> ToolResult:
        try:
            query = kwargs.get("query")
            if not query:
                return ToolResult(success=False, error="query is required")

            entity_type = kwargs.get("entity_type")

            from app.services.knowledge import search_entities
            entities = search_entities(
                self.db,
                self.tenant_id,
                query,
                entity_type=entity_type,
            )

            return ToolResult(
                success=True,
                data=[{
                    "id": str(e.id),
                    "name": e.name,
                    "entity_type": e.entity_type,
                    "properties": e.properties,
                } for e in entities],
                metadata={"result_count": len(entities)}
            )
        except Exception as e:
            return ToolResult(success=False, error=f"Knowledge search failed: {str(e)}")


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
