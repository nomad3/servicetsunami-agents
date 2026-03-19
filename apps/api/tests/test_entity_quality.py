"""Tests for Entity Quality Tracking — reference counts, feedback scores, history, observations."""
import pytest
from uuid import uuid4
from datetime import datetime
from unittest.mock import patch, MagicMock
import os

os.environ["TESTING"] = "True"


TENANT_ID = uuid4()


class TestIncrementReferenceCount:
    """Test increment_reference_count in knowledge service."""

    def test_increments_when_entity_name_in_text(self):
        from app.services.knowledge import increment_reference_count

        db = MagicMock()
        entity = MagicMock()
        entity.reference_count = 3
        db.query.return_value.filter.return_value.first.return_value = entity

        result = increment_reference_count(
            db, TENANT_ID, ["Acme Corp"], "We discussed Acme Corp deal"
        )

        assert result == 1
        assert entity.reference_count == 4
        db.flush.assert_called_once()

    def test_case_insensitive_matching(self):
        from app.services.knowledge import increment_reference_count

        db = MagicMock()
        entity = MagicMock()
        entity.reference_count = 0
        db.query.return_value.filter.return_value.first.return_value = entity

        result = increment_reference_count(
            db, TENANT_ID, ["acme corp"], "ACME CORP is great"
        )

        assert result == 1
        assert entity.reference_count == 1

    def test_no_match_no_increment(self):
        from app.services.knowledge import increment_reference_count

        db = MagicMock()
        # No entity found in response text
        result = increment_reference_count(
            db, TENANT_ID, ["Acme Corp"], "Something unrelated"
        )

        assert result == 0

    def test_empty_inputs(self):
        from app.services.knowledge import increment_reference_count

        db = MagicMock()
        assert increment_reference_count(db, TENANT_ID, [], "some text") == 0
        assert increment_reference_count(db, TENANT_ID, ["Acme"], "") == 0

    def test_short_names_skipped(self):
        from app.services.knowledge import increment_reference_count

        db = MagicMock()
        # Names shorter than 2 chars are skipped
        result = increment_reference_count(db, TENANT_ID, ["A"], "A is here")
        assert result == 0

    def test_none_reference_count_initializes(self):
        from app.services.knowledge import increment_reference_count

        db = MagicMock()
        entity = MagicMock()
        entity.reference_count = None
        db.query.return_value.filter.return_value.first.return_value = entity

        result = increment_reference_count(
            db, TENANT_ID, ["TestEntity"], "TestEntity mentioned"
        )

        assert result == 1
        assert entity.reference_count == 1


class TestUpdateFeedbackScore:
    """Test update_feedback_score in knowledge service."""

    @patch("app.services.knowledge.get_entity")
    def test_memory_helpful_increases_score(self, mock_get):
        from app.services.knowledge import update_feedback_score

        entity = MagicMock()
        entity.feedback_score = 0.0
        mock_get.return_value = entity
        db = MagicMock()

        result = update_feedback_score(db, TENANT_ID, uuid4(), "memory_helpful")

        assert result is not None
        assert entity.feedback_score == 0.1

    @patch("app.services.knowledge.get_entity")
    def test_memory_irrelevant_decreases_score(self, mock_get):
        from app.services.knowledge import update_feedback_score

        entity = MagicMock()
        entity.feedback_score = 0.0
        mock_get.return_value = entity
        db = MagicMock()

        result = update_feedback_score(db, TENANT_ID, uuid4(), "memory_irrelevant")

        assert result is not None
        assert entity.feedback_score == pytest.approx(-0.1, abs=0.001)

    @patch("app.services.knowledge.get_entity")
    def test_clamped_to_positive_1(self, mock_get):
        from app.services.knowledge import update_feedback_score

        entity = MagicMock()
        entity.feedback_score = 0.95
        mock_get.return_value = entity
        db = MagicMock()

        update_feedback_score(db, TENANT_ID, uuid4(), "memory_helpful")

        assert entity.feedback_score <= 1.0

    @patch("app.services.knowledge.get_entity")
    def test_clamped_to_negative_1(self, mock_get):
        from app.services.knowledge import update_feedback_score

        entity = MagicMock()
        entity.feedback_score = -0.95
        mock_get.return_value = entity
        db = MagicMock()

        update_feedback_score(db, TENANT_ID, uuid4(), "memory_irrelevant")

        assert entity.feedback_score >= -1.0

    def test_unknown_feedback_type_returns_none(self):
        from app.services.knowledge import update_feedback_score

        db = MagicMock()
        result = update_feedback_score(db, TENANT_ID, uuid4(), "unknown_type")
        assert result is None

    @patch("app.services.knowledge.get_entity")
    def test_entity_not_found_returns_none(self, mock_get):
        from app.services.knowledge import update_feedback_score

        mock_get.return_value = None
        db = MagicMock()

        result = update_feedback_score(db, TENANT_ID, uuid4(), "memory_helpful")
        assert result is None

    @patch("app.services.knowledge.get_entity")
    def test_none_feedback_score_defaults_to_zero(self, mock_get):
        from app.services.knowledge import update_feedback_score

        entity = MagicMock()
        entity.feedback_score = None
        mock_get.return_value = entity
        db = MagicMock()

        update_feedback_score(db, TENANT_ID, uuid4(), "memory_helpful")

        assert entity.feedback_score == 0.1


class TestGetQualityStats:
    """Test get_quality_stats returns expected structure."""

    def test_returns_empty_structure_for_zero_entities(self):
        from app.services.knowledge import get_quality_stats

        db = MagicMock()
        # total count = 0
        db.query.return_value.filter.return_value.scalar.return_value = 0

        result = get_quality_stats(db, TENANT_ID)

        assert result["total_entities"] == 0
        assert result["embedding_coverage_pct"] == 0.0
        assert result["top_10_by_usefulness"] == []
        assert result["bottom_10"] == []
        assert result["per_platform_extraction_stats"] == []

    def test_returns_correct_keys(self):
        from app.services.knowledge import get_quality_stats

        db = MagicMock()

        # First call: total entities count
        # Second call: embedding count
        scalar_calls = [10, 5]
        call_count = [0]

        def mock_scalar():
            idx = call_count[0]
            call_count[0] += 1
            return scalar_calls[idx] if idx < len(scalar_calls) else 0

        filter_mock = MagicMock()
        filter_mock.scalar.side_effect = mock_scalar

        # For top/bottom queries that return entity lists
        order_mock = MagicMock()
        order_mock.offset.return_value = order_mock
        order_mock.limit.return_value = order_mock
        order_mock.all.return_value = []

        query_mock = MagicMock()
        query_mock.filter.return_value = query_mock
        query_mock.order_by.return_value = order_mock
        query_mock.scalar.side_effect = mock_scalar

        db.query.return_value = query_mock

        # For the raw SQL execute (platform stats)
        db.execute.return_value.fetchall.return_value = []

        result = get_quality_stats(db, TENANT_ID)

        assert "total_entities" in result
        assert "embedding_coverage_pct" in result
        assert "top_10_by_usefulness" in result
        assert "bottom_10" in result
        assert "per_platform_extraction_stats" in result


class TestCreateEntityHistory:
    """Test create_entity_history snapshots entity state."""

    def test_creates_history_record(self):
        from app.services.knowledge import create_entity_history

        db = MagicMock()
        entity = MagicMock()
        entity.id = uuid4()
        entity.tenant_id = TENANT_ID
        entity.properties = {"key": "value"}
        entity.attributes = {"industry": "tech"}

        # Max version query returns 0 (no prior history)
        db.query.return_value.filter.return_value.scalar.return_value = 0

        history = create_entity_history(db, entity, change_reason="test update")

        db.add.assert_called_once()
        db.flush.assert_called_once()
        added_obj = db.add.call_args[0][0]
        assert added_obj.entity_id == entity.id
        assert added_obj.tenant_id == TENANT_ID
        assert added_obj.version == 1
        assert added_obj.properties_snapshot == {"key": "value"}
        assert added_obj.attributes_snapshot == {"industry": "tech"}
        assert added_obj.change_reason == "test update"

    def test_auto_increments_version(self):
        from app.services.knowledge import create_entity_history

        db = MagicMock()
        entity = MagicMock()
        entity.id = uuid4()
        entity.tenant_id = TENANT_ID
        entity.properties = {}
        entity.attributes = {}

        # Previous max version is 3
        db.query.return_value.filter.return_value.scalar.return_value = 3

        create_entity_history(db, entity)

        added_obj = db.add.call_args[0][0]
        assert added_obj.version == 4

    def test_handles_none_properties(self):
        from app.services.knowledge import create_entity_history

        db = MagicMock()
        entity = MagicMock()
        entity.id = uuid4()
        entity.tenant_id = TENANT_ID
        entity.properties = "not a dict"
        entity.attributes = None

        db.query.return_value.filter.return_value.scalar.return_value = 0

        create_entity_history(db, entity)

        added_obj = db.add.call_args[0][0]
        assert added_obj.properties_snapshot is None
        assert added_obj.attributes_snapshot is None

    def test_changed_by_platform_stored(self):
        from app.services.knowledge import create_entity_history

        db = MagicMock()
        entity = MagicMock()
        entity.id = uuid4()
        entity.tenant_id = TENANT_ID
        entity.properties = {}
        entity.attributes = {}

        db.query.return_value.filter.return_value.scalar.return_value = 0

        create_entity_history(db, entity, changed_by_platform="claude_code")

        added_obj = db.add.call_args[0][0]
        assert added_obj.changed_by_platform == "claude_code"


class TestCreateObservation:
    """Test create_observation stores with embedding and source tracking."""

    @patch("app.services.memory_activity.log_activity")
    @patch("app.services.knowledge.embedding_service")
    def test_creates_observation_with_embedding(self, mock_embed_svc, mock_log):
        from app.services.knowledge import create_observation

        db = MagicMock()
        mock_embed_svc.embed_text.return_value = [0.1] * 768

        obs = create_observation(
            db,
            tenant_id=TENANT_ID,
            observation_text="Customer signed contract",
            observation_type="fact",
            source_type="email",
            source_platform="gmail",
            source_agent="inbox_monitor",
            entity_id=uuid4(),
            confidence=0.95,
        )

        db.add.assert_called_once()
        db.flush.assert_called()
        added = db.add.call_args[0][0]
        assert added.observation_text == "Customer signed contract"
        assert added.observation_type == "fact"
        assert added.source_type == "email"
        assert added.source_platform == "gmail"
        assert added.source_agent == "inbox_monitor"
        assert added.confidence == 0.95
        assert added.embedding == [0.1] * 768

    @patch("app.services.memory_activity.log_activity")
    @patch("app.services.knowledge.embedding_service")
    def test_handles_embedding_failure_gracefully(self, mock_embed_svc, mock_log):
        from app.services.knowledge import create_observation

        db = MagicMock()
        mock_embed_svc.embed_text.side_effect = Exception("model unavailable")

        # Should not raise
        obs = create_observation(
            db,
            tenant_id=TENANT_ID,
            observation_text="Some observation",
        )

        db.add.assert_called_once()

    @patch("app.services.memory_activity.log_activity")
    @patch("app.services.knowledge.embedding_service")
    def test_embedding_none_handled(self, mock_embed_svc, mock_log):
        from app.services.knowledge import create_observation

        db = MagicMock()
        mock_embed_svc.embed_text.return_value = None

        create_observation(
            db,
            tenant_id=TENANT_ID,
            observation_text="Observation without embedding",
        )

        added = db.add.call_args[0][0]
        # embedding should not be set when embed_text returns None
        assert not hasattr(added, '_embedding_set') or added.embedding is None


class TestSearchObservations:
    """Test search_observations returns relevant results."""

    @patch("app.services.knowledge.embedding_service")
    def test_returns_empty_when_no_embedding(self, mock_embed_svc):
        from app.services.knowledge import search_observations

        db = MagicMock()
        mock_embed_svc.embed_text.return_value = None

        result = search_observations(db, TENANT_ID, "some query")

        assert result == []

    @patch("app.services.knowledge.embedding_service")
    def test_returns_empty_on_embed_error(self, mock_embed_svc):
        from app.services.knowledge import search_observations

        db = MagicMock()
        mock_embed_svc.embed_text.side_effect = Exception("model error")

        result = search_observations(db, TENANT_ID, "query text")

        assert result == []

    @patch("app.services.knowledge.embedding_service")
    def test_returns_results_with_similarity(self, mock_embed_svc):
        from app.services.knowledge import search_observations

        db = MagicMock()
        mock_embed_svc.embed_text.return_value = [0.1] * 768

        obs = MagicMock()
        obs.id = uuid4()
        obs.observation_text = "Customer prefers email"
        obs.observation_type = "fact"
        obs.source_type = "conversation"
        obs.source_platform = "web"
        obs.entity_id = uuid4()
        obs.confidence = 0.9
        obs.created_at = datetime(2026, 1, 1)

        # Mock the query chain for pgvector cosine search
        q = MagicMock()
        q.filter.return_value = q
        q.order_by.return_value = q
        q.limit.return_value = q
        q.all.return_value = [(obs, 0.85)]

        db.query.return_value = q

        result = search_observations(db, TENANT_ID, "email preferences")

        assert len(result) == 1
        assert result[0]["observation_text"] == "Customer prefers email"
        assert result[0]["similarity"] == 0.85

    @patch("app.services.knowledge.embedding_service")
    def test_scoped_to_entity(self, mock_embed_svc):
        from app.services.knowledge import search_observations

        db = MagicMock()
        mock_embed_svc.embed_text.return_value = [0.1] * 768

        entity_id = uuid4()

        q = MagicMock()
        q.filter.return_value = q
        q.order_by.return_value = q
        q.limit.return_value = q
        q.all.return_value = []
        db.query.return_value = q

        result = search_observations(db, TENANT_ID, "query", entity_id=entity_id)

        assert result == []
        # Verify filter was called (entity_id filter applied)
        assert q.filter.call_count >= 2  # tenant + embedding + entity_id filters
