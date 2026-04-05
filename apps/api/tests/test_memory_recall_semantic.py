"""Tests for Memory Recall Engine — hybrid semantic + keyword recall."""
import pytest
from uuid import uuid4
from datetime import datetime
from unittest.mock import patch, MagicMock, call
import os

os.environ["TESTING"] = "True"

from app.services.memory_recall import (
    _build_anticipatory_context,
    build_memory_context,
    build_memory_context_with_git,
    extract_keywords,
    get_recent_git_context,
    _is_code_related,
    _build_memory_context_keyword_fallback,
)
from app.services.cli_session_manager import generate_cli_instructions


TENANT_ID = uuid4()


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.execute.return_value.fetchall.return_value = []
    query = db.query.return_value
    filtered = query.filter.return_value
    filtered.limit.return_value.all.return_value = []
    filtered.order_by.return_value.first.return_value = None
    return db


class TestExtractKeywords:
    def test_extracts_meaningful_words(self):
        kws = extract_keywords("Tell me about Acme Corp deal")
        assert "acme" in kws
        assert "corp" in kws
        assert "deal" in kws

    def test_removes_stop_words(self):
        kws = extract_keywords("what is the status of the project")
        assert "the" not in kws
        assert "what" not in kws
        assert "status" in kws
        assert "project" in kws

    def test_short_words_excluded(self):
        kws = extract_keywords("a an it do be if")
        assert kws == []

    def test_deduplicates_preserving_order(self):
        kws = extract_keywords("acme acme acme")
        assert kws == ["acme"]

    def test_caps_at_10(self):
        kws = extract_keywords(
            "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima"
        )
        assert len(kws) <= 10

    def test_empty_message(self):
        assert extract_keywords("") == []

    def test_digits_excluded(self):
        kws = extract_keywords("order 12345 items")
        assert "12345" not in kws


class TestIsCodeRelated:
    def test_code_related(self):
        assert _is_code_related("fix the login bug")
        assert _is_code_related("deploy to production")
        assert _is_code_related("what changed in the last commit")

    def test_not_code_related(self):
        assert not _is_code_related("schedule a meeting tomorrow")
        assert not _is_code_related("who is the CEO of Acme?")


class TestBuildMemoryContextSemantic:
    """Test the hybrid semantic recall path in build_memory_context."""

    @patch("app.services.memory_recall.log_experience")
    @patch("app.services.memory_recall._fetch_top_observations_semantic")
    @patch("app.services.memory_recall.embedding_service")
    def test_semantic_path_returns_entities_and_memories(
        self, mock_embed_svc, mock_fetch_obs, mock_log_exp, mock_db
    ):
        """Semantic search path returns entities, memories, and relations."""
        mock_embed_svc.embed_text.return_value = [0.1] * 768
        mock_embed_svc.search_entities_semantic.return_value = [
            {
                "id": str(uuid4()),
                "name": "Acme Corp",
                "entity_type": "company",
                "category": "customer",
                "description": "Big customer",
                "similarity": 0.85,
            }
        ]
        mock_embed_svc.search_memories_semantic.return_value = [
            {
                "id": str(uuid4()),
                "memory_type": "fact",
                "content": "Acme prefers email",
                "similarity": 0.80,
            }
        ]
        mock_fetch_obs.return_value = []
        # No relations
        mock_db.query.return_value.filter.return_value.limit.return_value.all.return_value = []

        context = build_memory_context(mock_db, TENANT_ID, "Tell me about Acme Corp")

        assert "relevant_entities" in context
        assert len(context["relevant_entities"]) == 1
        assert context["relevant_entities"][0]["name"] == "Acme Corp"
        assert "relevant_memories" in context
        assert len(context["relevant_memories"]) == 1

    @patch("app.services.memory_recall.log_experience")
    @patch("app.services.memory_recall._fetch_top_observations_semantic")
    @patch("app.services.memory_recall.embedding_service")
    def test_keyword_boost_adds_03(
        self, mock_embed_svc, mock_fetch_obs, mock_log_exp, mock_db
    ):
        """Entities whose name matches a query word get +0.3 similarity boost."""
        entity_id = str(uuid4())
        mock_embed_svc.embed_text.return_value = [0.1] * 768
        mock_embed_svc.search_entities_semantic.return_value = [
            {
                "id": entity_id,
                "name": "Luna",
                "entity_type": "agent",
                "category": "assistant",
                "description": "AI assistant",
                "similarity": 0.60,
            },
            {
                "id": str(uuid4()),
                "name": "SomeOtherEntity",
                "entity_type": "concept",
                "category": "",
                "description": "",
                "similarity": 0.62,
            },
        ]
        mock_embed_svc.search_memories_semantic.return_value = []
        mock_fetch_obs.return_value = []
        mock_db.query.return_value.filter.return_value.limit.return_value.all.return_value = []

        context = build_memory_context(mock_db, TENANT_ID, "Tell me about Luna")

        # Luna should be boosted to 0.90 and come first
        entities = context["relevant_entities"]
        assert entities[0]["name"] == "Luna"
        assert entities[0]["similarity"] == 0.9  # 0.60 + 0.30

    @patch("app.services.memory_recall.log_experience")
    @patch("app.services.memory_recall._fetch_top_observations_semantic")
    @patch("app.services.memory_recall.embedding_service")
    def test_keyword_boost_capped_at_1(
        self, mock_embed_svc, mock_fetch_obs, mock_log_exp, mock_db
    ):
        """Keyword boost should not exceed 1.0."""
        mock_embed_svc.embed_text.return_value = [0.1] * 768
        mock_embed_svc.search_entities_semantic.return_value = [
            {
                "id": str(uuid4()),
                "name": "Acme",
                "entity_type": "company",
                "category": "",
                "description": "",
                "similarity": 0.85,
            },
        ]
        mock_embed_svc.search_memories_semantic.return_value = []
        mock_fetch_obs.return_value = []
        mock_db.query.return_value.filter.return_value.limit.return_value.all.return_value = []

        context = build_memory_context(mock_db, TENANT_ID, "Acme status")

        assert context["relevant_entities"][0]["similarity"] <= 1.0

    @patch("app.services.memory_recall.log_experience")
    @patch("app.services.memory_recall._fetch_top_observations_semantic")
    @patch("app.services.memory_recall.embedding_service")
    def test_recall_count_incremented(
        self, mock_embed_svc, mock_fetch_obs, mock_log_exp, mock_db
    ):
        """Entity recall_count is incremented via UPDATE SQL."""
        entity_id = str(uuid4())
        mock_embed_svc.embed_text.return_value = [0.1] * 768
        mock_embed_svc.search_entities_semantic.return_value = [
            {
                "id": entity_id,
                "name": "Acme",
                "entity_type": "company",
                "category": "",
                "description": "",
                "similarity": 0.80,
            },
        ]
        mock_embed_svc.search_memories_semantic.return_value = []
        mock_fetch_obs.return_value = []
        mock_db.query.return_value.filter.return_value.limit.return_value.all.return_value = []

        build_memory_context(mock_db, TENANT_ID, "Acme updates")

        # Verify that db.execute was called (for the UPDATE recall_count statement)
        assert mock_db.execute.called
        # Verify commit was called
        assert mock_db.commit.called

    @patch("app.services.memory_recall.log_experience")
    @patch("app.services.memory_recall._fetch_top_observations_semantic")
    @patch("app.services.memory_recall.embedding_service")
    def test_rl_experience_logged(
        self, mock_embed_svc, mock_fetch_obs, mock_log_exp, mock_db
    ):
        """An RL experience is logged for the memory_recall decision point."""
        mock_embed_svc.embed_text.return_value = [0.1] * 768
        mock_embed_svc.search_entities_semantic.return_value = [
            {
                "id": str(uuid4()),
                "name": "TestEntity",
                "entity_type": "person",
                "category": "",
                "description": "",
                "similarity": 0.75,
            },
        ]
        mock_embed_svc.search_memories_semantic.return_value = []
        mock_fetch_obs.return_value = []
        mock_db.query.return_value.filter.return_value.limit.return_value.all.return_value = []

        build_memory_context(mock_db, TENANT_ID, "TestEntity progress")

        mock_log_exp.assert_called_once()
        call_kwargs = mock_log_exp.call_args
        assert call_kwargs.kwargs["decision_point"] == "memory_recall"
        assert call_kwargs.kwargs["tenant_id"] == TENANT_ID
        assert "query" in call_kwargs.kwargs["state"]
        assert "recalled_entities" in call_kwargs.kwargs["action"]

    @patch("app.services.memory_recall.embedding_service")
    def test_fallback_to_keyword_when_embedding_none(self, mock_embed_svc, mock_db):
        """Falls back to keyword-based recall when embed_text returns None."""
        mock_embed_svc.embed_text.return_value = None

        # Set up keyword fallback query results
        mock_entity = MagicMock()
        mock_entity.id = uuid4()
        mock_entity.name = "Acme"
        mock_entity.entity_type = "company"
        mock_entity.category = "customer"
        mock_entity.description = "A company"
        mock_entity.confidence = 0.9

        mock_memory = MagicMock()
        mock_memory.content = "Acme prefers email"
        mock_memory.memory_type = "fact"
        mock_memory.importance = 0.8
        mock_memory.access_count = 0
        mock_memory.last_accessed_at = None

        # Configure query chain for entities
        entity_q = MagicMock()
        entity_q.filter.return_value = entity_q
        entity_q.order_by.return_value = entity_q
        entity_q.limit.return_value = entity_q
        entity_q.all.return_value = [mock_entity]

        # Configure query chain for memories
        mem_q = MagicMock()
        mem_q.filter.return_value = mem_q
        mem_q.order_by.return_value = mem_q
        mem_q.limit.return_value = mem_q
        mem_q.all.return_value = [mock_memory]

        # Relations query
        rel_q = MagicMock()
        rel_q.filter.return_value = rel_q
        rel_q.limit.return_value = rel_q
        rel_q.all.return_value = []

        from app.models.knowledge_entity import KnowledgeEntity
        from app.models.agent_memory import AgentMemory
        from app.models.knowledge_relation import KnowledgeRelation

        def mock_query_side_effect(model):
            if model == KnowledgeEntity:
                return entity_q
            if model == AgentMemory:
                return mem_q
            if model == KnowledgeRelation:
                return rel_q
            q = MagicMock()
            q.filter.return_value = q
            q.all.return_value = []
            return q

        mock_db.query.side_effect = mock_query_side_effect

        context = build_memory_context(mock_db, TENANT_ID, "Tell me about Acme")

        assert "relevant_entities" in context
        assert context["relevant_entities"][0]["name"] == "Acme"

    def test_anticipatory_context_built_without_keywords(self, mock_db):
        mock_db.execute.return_value.mappings.return_value.all.return_value = []

        context = build_memory_context(mock_db, TENANT_ID, "hi")

        assert context["time_context"]["time_of_day"] in {"morning", "midday", "afternoon", "evening"}

    def test_anticipatory_context_includes_upcoming_events(self, mock_db):
        start_time = datetime(2026, 4, 4, 9, 30)
        mock_db.execute.return_value.mappings.return_value.all.return_value = [
            {"title": "Standup", "start_time": start_time, "description": "Daily sync"},
        ]

        context = _build_anticipatory_context(mock_db, TENANT_ID, now=datetime(2026, 4, 4, 8, 0))

        assert context["time_context"]["time_of_day"] == "morning"
        assert context["time_context"]["local_date"] == "2026-04-04"
        assert context["upcoming_events"][0]["title"] == "Standup"
        assert context["upcoming_events"][0]["time"] == "09:30 AM"

        execute_args = mock_db.execute.call_args
        assert execute_args.args[1]["window_start"] == datetime(2026, 4, 4, 8, 0)
        assert execute_args.args[1]["window_end"] == datetime(2026, 4, 4, 12, 0)


class TestGenerateCliInstructions:
    def test_today_briefing_includes_operational_summary(self):
        instruction_text = generate_cli_instructions(
            skill_body="You are Luna.",
            tenant_name=str(TENANT_ID),
            user_name="user-1",
            channel="whatsapp",
            conversation_summary="",
            memory_context={
                "time_context": {"time_of_day": "morning", "greeting_hint": "Good morning! It's Friday."},
                "upcoming_events": [{"time": "09:30 AM", "title": "Standup"}],
                "recent_episodes": [{"summary": "Yesterday we planned the continuity work."}],
                "self_model": {
                    "active_goals": [
                        {"title": "Ship continuity layer", "state": "active", "priority": "high"},
                        {"title": "Unblock morning brief", "state": "blocked", "priority": "high"},
                    ],
                    "open_commitments": [
                        {"title": "Open PR", "due_at": datetime.utcnow().date().isoformat() + "T12:00:00"},
                    ],
                },
            },
        )

        assert "## Today's Context" in instruction_text
        assert "You have 1 upcoming event in the next 4 hours" in instruction_text
        assert "There are 2 active goals, including 1 blocked." in instruction_text
        assert "There is 1 open commitment." in instruction_text
        assert "Recent thread to keep in mind: Yesterday we planned the continuity work." in instruction_text

    @patch("app.services.memory_recall.log_experience")
    @patch("app.services.memory_recall._fetch_top_observations_semantic")
    @patch("app.services.memory_recall.embedding_service")
    def test_observations_fetched_for_entities(
        self, mock_embed_svc, mock_fetch_obs, mock_log_exp, mock_db
    ):
        """Top observations are fetched for each recalled entity."""
        entity_id = str(uuid4())
        mock_embed_svc.embed_text.return_value = [0.1] * 768
        mock_embed_svc.search_entities_semantic.return_value = [
            {
                "id": entity_id,
                "name": "Acme Corp",
                "entity_type": "company",
                "category": "",
                "description": "",
                "similarity": 0.80,
            },
        ]
        mock_embed_svc.search_memories_semantic.return_value = []
        mock_fetch_obs.return_value = [
            {"text": "Acme signed contract", "type": "fact", "source": "email", "date": "2026-01-01T00:00:00"},
        ]
        mock_db.query.return_value.filter.return_value.limit.return_value.all.return_value = []

        context = build_memory_context(mock_db, TENANT_ID, "Acme Corp details")

        assert "entity_observations" in context
        assert "Acme Corp" in context["entity_observations"]
        assert len(context["entity_observations"]["Acme Corp"]) == 1

    @patch("app.services.memory_recall.log_experience")
    @patch("app.services.memory_recall._fetch_top_observations_semantic")
    @patch("app.services.memory_recall.embedding_service")
    def test_empty_keywords_returns_empty(
        self, mock_embed_svc, mock_fetch_obs, mock_log_exp, mock_db
    ):
        """When no keywords extracted, skip embedding but still return anticipatory context."""
        mock_db.execute.return_value.mappings.return_value.all.return_value = []

        context = build_memory_context(mock_db, TENANT_ID, "hi")

        assert "time_context" in context
        mock_embed_svc.embed_text.assert_not_called()


class TestBuildMemoryContextWithGit:
    """Test git context appending for code-related queries."""

    @patch("app.services.memory_recall.get_recent_git_context")
    @patch("app.services.memory_recall.build_memory_context")
    def test_appends_git_context_for_code_queries(
        self, mock_build, mock_git_ctx
    ):
        db = MagicMock()
        mock_build.return_value = {"relevant_entities": []}
        mock_git_ctx.return_value = [
            {"text": "fix: login bug", "type": "git_commit", "date": "2026-01-01"},
        ]

        context = build_memory_context_with_git(db, TENANT_ID, "fix the login bug")

        assert "git_context" in context
        assert len(context["git_context"]) == 1
        mock_git_ctx.assert_called_once()

    @patch("app.services.memory_recall.get_recent_git_context")
    @patch("app.services.memory_recall.build_memory_context")
    def test_no_git_context_for_non_code_queries(
        self, mock_build, mock_git_ctx
    ):
        db = MagicMock()
        mock_build.return_value = {"relevant_entities": []}

        context = build_memory_context_with_git(db, TENANT_ID, "schedule a meeting")

        assert "git_context" not in context
        mock_git_ctx.assert_not_called()

    @patch("app.services.memory_recall.get_recent_git_context")
    @patch("app.services.memory_recall.build_memory_context")
    def test_no_git_key_when_git_returns_empty(
        self, mock_build, mock_git_ctx
    ):
        db = MagicMock()
        mock_build.return_value = {"relevant_entities": []}
        mock_git_ctx.return_value = []

        context = build_memory_context_with_git(db, TENANT_ID, "deploy the changes")

        assert "git_context" not in context


class TestGetRecentGitContext:
    def test_returns_empty_for_no_keywords(self):
        db = MagicMock()
        result = get_recent_git_context(db, TENANT_ID, "hi", limit=5)
        assert result == []

    def test_queries_observations(self):
        db = MagicMock()
        mock_row = MagicMock()
        mock_row.observation_text = "feat: add login"
        mock_row.observation_type = "git_commit"
        mock_row.created_at = datetime(2026, 1, 1)
        db.execute.return_value.fetchall.return_value = [mock_row]

        result = get_recent_git_context(db, TENANT_ID, "login feature", limit=5)

        assert len(result) == 1
        assert result[0]["text"] == "feat: add login"
        assert result[0]["type"] == "git_commit"
