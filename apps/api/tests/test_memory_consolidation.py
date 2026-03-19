"""Tests for Memory Consolidation Activities."""
import pytest
import json
from uuid import uuid4
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock, PropertyMock
import os

os.environ["TESTING"] = "True"


TENANT_ID = uuid4()
TENANT_ID_STR = str(TENANT_ID)


class TestFindDuplicateEntities:
    """Test the find_duplicate_entities activity."""

    @patch("app.workflows.activities.memory_consolidation.SessionLocal")
    @pytest.mark.asyncio
    async def test_returns_clusters_for_name_matches(self, mock_session_cls):
        from app.workflows.activities.memory_consolidation import find_duplicate_entities

        db = MagicMock()
        mock_session_cls.return_value = db

        id_a = uuid4()
        id_b = uuid4()
        row = MagicMock()
        row.a_id = id_a
        row.b_id = id_b

        # First execute: name-based duplicates
        # Second execute: embedding similarity
        db.execute.side_effect = [
            MagicMock(fetchall=MagicMock(return_value=[row])),
            MagicMock(fetchall=MagicMock(return_value=[])),
        ]

        result = await find_duplicate_entities(TENANT_ID_STR)

        assert result["count"] == 1
        assert len(result["clusters"]) == 1
        cluster = result["clusters"][0]
        assert str(id_a) in cluster
        assert str(id_b) in cluster

    @patch("app.workflows.activities.memory_consolidation.SessionLocal")
    @pytest.mark.asyncio
    async def test_returns_empty_when_no_duplicates(self, mock_session_cls):
        from app.workflows.activities.memory_consolidation import find_duplicate_entities

        db = MagicMock()
        mock_session_cls.return_value = db
        db.execute.return_value.fetchall.return_value = []

        result = await find_duplicate_entities(TENANT_ID_STR)

        assert result["count"] == 0
        assert result["clusters"] == []

    @patch("app.workflows.activities.memory_consolidation.SessionLocal")
    @pytest.mark.asyncio
    async def test_embedding_similarity_adds_to_clusters(self, mock_session_cls):
        from app.workflows.activities.memory_consolidation import find_duplicate_entities

        db = MagicMock()
        mock_session_cls.return_value = db

        id_c = uuid4()
        id_d = uuid4()
        emb_row = MagicMock()
        emb_row.a_id = id_c
        emb_row.b_id = id_d
        emb_row.similarity = 0.95

        # No name-based duplicates, but embedding-based ones
        db.execute.side_effect = [
            MagicMock(fetchall=MagicMock(return_value=[])),  # name match
            MagicMock(fetchall=MagicMock(return_value=[emb_row])),  # embedding match
        ]

        result = await find_duplicate_entities(TENANT_ID_STR)

        assert result["count"] == 1
        cluster = result["clusters"][0]
        assert str(id_c) in cluster
        assert str(id_d) in cluster

    @patch("app.workflows.activities.memory_consolidation.SessionLocal")
    @pytest.mark.asyncio
    async def test_handles_exception_gracefully(self, mock_session_cls):
        from app.workflows.activities.memory_consolidation import find_duplicate_entities

        db = MagicMock()
        mock_session_cls.return_value = db
        db.execute.side_effect = Exception("DB error")

        result = await find_duplicate_entities(TENANT_ID_STR)

        assert result["count"] == 0
        assert "error" in result


class TestAutoMergeDuplicates:
    """Test auto_merge_duplicates activity."""

    @patch("app.workflows.activities.memory_consolidation.SessionLocal")
    @pytest.mark.asyncio
    async def test_keeps_highest_recall_count_entity(self, mock_session_cls):
        from app.workflows.activities.memory_consolidation import auto_merge_duplicates

        db = MagicMock()
        mock_session_cls.return_value = db

        id_primary = uuid4()
        id_dup = uuid4()

        primary_entity = MagicMock()
        primary_entity.id = id_primary
        primary_entity.recall_count = 10
        primary_entity.reference_count = 5

        dup_entity = MagicMock()
        dup_entity.id = id_dup
        dup_entity.recall_count = 3
        dup_entity.reference_count = 2
        dup_entity.deleted_at = None

        # query chain for finding entities by ID
        q = MagicMock()
        q.filter.return_value = q
        q.order_by.return_value = q
        q.all.return_value = [primary_entity, dup_entity]  # primary first (highest recall)

        # query chain for relation/observation transfers
        transfer_q = MagicMock()
        transfer_q.filter.return_value = transfer_q
        transfer_q.update.return_value = 0

        db.query.side_effect = lambda model: q if model.__name__ == "KnowledgeEntity" else transfer_q

        clusters = [[str(id_primary), str(id_dup)]]
        result = await auto_merge_duplicates(TENANT_ID_STR, json.dumps(clusters))

        assert result["merged"] == 1
        # Primary accumulates dup's counts
        assert primary_entity.recall_count == 13
        assert primary_entity.reference_count == 7
        # Duplicate is soft-deleted
        assert dup_entity.deleted_at is not None
        db.commit.assert_called()

    @patch("app.workflows.activities.memory_consolidation.SessionLocal")
    @pytest.mark.asyncio
    async def test_skips_single_entity_clusters(self, mock_session_cls):
        from app.workflows.activities.memory_consolidation import auto_merge_duplicates

        db = MagicMock()
        mock_session_cls.return_value = db

        clusters = [[str(uuid4())]]
        result = await auto_merge_duplicates(TENANT_ID_STR, json.dumps(clusters))

        assert result["merged"] == 0


class TestApplyMemoryDecay:
    """Test apply_memory_decay activity."""

    @patch("app.workflows.activities.memory_consolidation.SessionLocal")
    @pytest.mark.asyncio
    async def test_archives_low_effective_importance(self, mock_session_cls):
        from app.workflows.activities.memory_consolidation import apply_memory_decay

        db = MagicMock()
        mock_session_cls.return_value = db

        # Memory with low importance, old access, low access_count
        old_memory = MagicMock()
        old_memory.importance = 0.1
        old_memory.last_accessed_at = datetime.utcnow() - timedelta(days=200)
        old_memory.created_at = datetime.utcnow() - timedelta(days=200)
        old_memory.access_count = 0
        old_memory.expires_at = None

        q = MagicMock()
        q.filter.return_value = q
        q.limit.return_value = q
        q.all.return_value = [old_memory]
        db.query.return_value = q

        result = await apply_memory_decay(TENANT_ID_STR)

        assert result["decayed"] == 1
        assert result["archived"] == 1
        assert old_memory.expires_at is not None

    @patch("app.workflows.activities.memory_consolidation.SessionLocal")
    @pytest.mark.asyncio
    async def test_preserves_high_importance_memories(self, mock_session_cls):
        """Memories with high importance (>=0.9) are never selected for decay."""
        from app.workflows.activities.memory_consolidation import apply_memory_decay

        db = MagicMock()
        mock_session_cls.return_value = db

        # The query already filters importance < 0.9, so high importance memories
        # won't appear in results. An empty result means 0 decayed.
        q = MagicMock()
        q.filter.return_value = q
        q.limit.return_value = q
        q.all.return_value = []
        db.query.return_value = q

        result = await apply_memory_decay(TENANT_ID_STR)

        assert result["decayed"] == 0
        assert result["archived"] == 0

    @patch("app.workflows.activities.memory_consolidation.SessionLocal")
    @pytest.mark.asyncio
    async def test_does_not_archive_frequently_accessed(self, mock_session_cls):
        from app.workflows.activities.memory_consolidation import apply_memory_decay

        db = MagicMock()
        mock_session_cls.return_value = db

        # Memory with moderate importance but high access_count
        mem = MagicMock()
        mem.importance = 0.3
        mem.last_accessed_at = datetime.utcnow() - timedelta(days=60)
        mem.created_at = datetime.utcnow() - timedelta(days=100)
        mem.access_count = 10  # >= 2, won't be archived
        mem.expires_at = None

        q = MagicMock()
        q.filter.return_value = q
        q.limit.return_value = q
        q.all.return_value = [mem]
        db.query.return_value = q

        result = await apply_memory_decay(TENANT_ID_STR)

        assert result["decayed"] == 1
        assert result["archived"] == 0  # access_count >= 2 prevents archival
        assert mem.expires_at is None


class TestPromoteEntities:
    """Test entity lifecycle promotions."""

    @patch("app.workflows.activities.memory_consolidation.SessionLocal")
    @pytest.mark.asyncio
    async def test_draft_to_verified(self, mock_session_cls):
        from app.workflows.activities.memory_consolidation import promote_entities

        db = MagicMock()
        mock_session_cls.return_value = db

        draft_entity = MagicMock()
        draft_entity.status = "draft"
        draft_entity.confidence = 0.8
        draft_entity.created_at = datetime.utcnow() - timedelta(days=10)

        # Setup query chains for each promotion stage
        draft_q = MagicMock()
        draft_q.filter.return_value = draft_q
        draft_q.limit.return_value = draft_q
        draft_q.all.return_value = [draft_entity]

        verified_q = MagicMock()
        verified_q.filter.return_value = verified_q
        verified_q.limit.return_value = verified_q
        verified_q.all.return_value = []

        stale_q = MagicMock()
        stale_q.filter.return_value = stale_q
        stale_q.limit.return_value = stale_q
        stale_q.all.return_value = []

        call_count = [0]
        def query_side_effect(model):
            q = MagicMock()
            q.filter.return_value = q
            q.limit.return_value = q
            result = call_count[0]
            call_count[0] += 1
            if result == 0:
                q.all.return_value = [draft_entity]
            else:
                q.all.return_value = []
            return q

        db.query.side_effect = query_side_effect

        result = await promote_entities(TENANT_ID_STR)

        assert result["draft_to_verified"] == 1
        assert draft_entity.status == "verified"

    @patch("app.workflows.activities.memory_consolidation.SessionLocal")
    @pytest.mark.asyncio
    async def test_verified_to_enriched_by_recall_count(self, mock_session_cls):
        from app.workflows.activities.memory_consolidation import promote_entities

        db = MagicMock()
        mock_session_cls.return_value = db

        verified_entity = MagicMock()
        verified_entity.status = "verified"
        verified_entity.recall_count = 10  # > 5

        call_count = [0]
        def query_side_effect(model):
            q = MagicMock()
            q.filter.return_value = q
            q.limit.return_value = q
            result = call_count[0]
            call_count[0] += 1
            if result == 0:
                q.all.return_value = []  # draft
            elif result == 1:
                q.all.return_value = [verified_entity]  # verified
            else:
                q.all.return_value = []  # stale
            return q

        db.query.side_effect = query_side_effect

        result = await promote_entities(TENANT_ID_STR)

        assert result["verified_to_enriched"] == 1
        assert verified_entity.status == "enriched"

    @patch("app.workflows.activities.memory_consolidation.SessionLocal")
    @pytest.mark.asyncio
    async def test_any_to_archived(self, mock_session_cls):
        from app.workflows.activities.memory_consolidation import promote_entities

        db = MagicMock()
        mock_session_cls.return_value = db

        stale_entity = MagicMock()
        stale_entity.status = "verified"
        stale_entity.updated_at = datetime.utcnow() - timedelta(days=100)
        stale_entity.recall_count = 0
        stale_entity.reference_count = 0

        call_count = [0]
        def query_side_effect(model):
            q = MagicMock()
            q.filter.return_value = q
            q.limit.return_value = q
            result = call_count[0]
            call_count[0] += 1
            if result == 2:
                q.all.return_value = [stale_entity]  # stale
            else:
                q.all.return_value = []
            return q

        db.query.side_effect = query_side_effect

        result = await promote_entities(TENANT_ID_STR)

        assert result["archived"] == 1
        assert stale_entity.status == "archived"


class TestSyncMemoriesAndEntities:
    """Test sync_memories_and_entities activity."""

    @patch("app.workflows.activities.memory_consolidation.SessionLocal")
    @pytest.mark.asyncio
    async def test_high_importance_fact_creates_entity(self, mock_session_cls):
        from app.workflows.activities.memory_consolidation import sync_memories_and_entities

        db = MagicMock()
        mock_session_cls.return_value = db

        fact_memory = MagicMock()
        fact_memory.id = uuid4()
        fact_memory.content = "Acme Corp signed the contract\nDetails follow"
        fact_memory.importance = 0.9
        fact_memory.memory_type = "fact"

        # First query: find fact memories
        mem_q = MagicMock()
        mem_q.filter.return_value = mem_q
        mem_q.limit.return_value = mem_q
        mem_q.all.return_value = [fact_memory]

        # Second query: check if entity exists (None = doesn't exist)
        exist_q = MagicMock()
        exist_q.filter.return_value = exist_q
        exist_q.first.return_value = None

        call_count = [0]
        def query_side_effect(*args):
            q = MagicMock()
            q.filter.return_value = q
            q.limit.return_value = q
            result = call_count[0]
            call_count[0] += 1
            if result == 0:
                q.all.return_value = [fact_memory]
                return q
            else:
                q.first.return_value = None
                return q

        db.query.side_effect = query_side_effect

        result = await sync_memories_and_entities(TENANT_ID_STR)

        assert result["synced"] == 1
        db.add.assert_called_once()
        db.commit.assert_called()

    @patch("app.workflows.activities.memory_consolidation.SessionLocal")
    @pytest.mark.asyncio
    async def test_skips_existing_entity(self, mock_session_cls):
        from app.workflows.activities.memory_consolidation import sync_memories_and_entities

        db = MagicMock()
        mock_session_cls.return_value = db

        fact_memory = MagicMock()
        fact_memory.id = uuid4()
        fact_memory.content = "Existing fact"
        fact_memory.importance = 0.9

        call_count = [0]
        def query_side_effect(*args):
            q = MagicMock()
            q.filter.return_value = q
            q.limit.return_value = q
            result = call_count[0]
            call_count[0] += 1
            if result == 0:
                q.all.return_value = [fact_memory]
                return q
            else:
                # Entity already exists
                q.first.return_value = MagicMock()
                return q

        db.query.side_effect = query_side_effect

        result = await sync_memories_and_entities(TENANT_ID_STR)

        assert result["synced"] == 0
        db.add.assert_not_called()
