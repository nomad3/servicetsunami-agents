"""Tests for RL-Memory cross-learning: reward service, entity scores, agent router, experience-to-observation."""
import pytest
import json
from uuid import uuid4
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
import os

os.environ["TESTING"] = "True"


TENANT_ID = uuid4()
TENANT_ID_STR = str(TENANT_ID)


class TestComputeCostAdjustedReward:
    """Test cost-adjusted reward from rl_reward_service."""

    def test_positive_reward_cheap_execution(self):
        from app.services.rl_reward_service import compute_cost_adjusted_reward

        # $0 cost => cost_factor=1.0 => reward * (0.7 + 0.3*1.0) = reward * 1.0
        result = compute_cost_adjusted_reward(raw_reward=0.8, cost_usd=0.0, max_cost_budget=0.10)
        assert result == pytest.approx(0.8, abs=0.001)

    def test_positive_reward_expensive_execution(self):
        from app.services.rl_reward_service import compute_cost_adjusted_reward

        # $0.10 cost => cost_factor=0.0 => reward * (0.7 + 0.3*0) = reward * 0.7
        result = compute_cost_adjusted_reward(raw_reward=0.8, cost_usd=0.10, max_cost_budget=0.10)
        assert result == pytest.approx(0.56, abs=0.001)

    def test_negative_reward_cheap_execution(self):
        from app.services.rl_reward_service import compute_cost_adjusted_reward

        # negative + cheap => reward * (1.3 - 0.3*1.0) = reward * 1.0
        result = compute_cost_adjusted_reward(raw_reward=-0.6, cost_usd=0.0, max_cost_budget=0.10)
        assert result == pytest.approx(-0.6, abs=0.001)

    def test_negative_reward_expensive_execution(self):
        from app.services.rl_reward_service import compute_cost_adjusted_reward

        # negative + expensive => reward * (1.3 - 0.3*0) = reward * 1.3
        result = compute_cost_adjusted_reward(raw_reward=-0.6, cost_usd=0.10, max_cost_budget=0.10)
        assert result == pytest.approx(-0.78, abs=0.001)

    def test_clamped_to_range(self):
        from app.services.rl_reward_service import compute_cost_adjusted_reward

        result = compute_cost_adjusted_reward(raw_reward=1.0, cost_usd=0.0)
        assert -1.0 <= result <= 1.0

        result = compute_cost_adjusted_reward(raw_reward=-1.0, cost_usd=0.10)
        assert -1.0 <= result <= 1.0

    def test_zero_reward_stays_zero(self):
        from app.services.rl_reward_service import compute_cost_adjusted_reward

        result = compute_cost_adjusted_reward(raw_reward=0.0, cost_usd=0.05)
        assert result == 0.0

    def test_cost_exceeds_budget(self):
        from app.services.rl_reward_service import compute_cost_adjusted_reward

        # cost_factor clamps at 0 when cost > budget
        result = compute_cost_adjusted_reward(raw_reward=0.8, cost_usd=0.20, max_cost_budget=0.10)
        assert result == pytest.approx(0.56, abs=0.001)


class TestUpdateEntityScoresOnReward:
    """Test entity quality score adjustment from RL reward signals."""

    def test_positive_reward_bumps_score(self):
        from app.services.rl_reward_service import update_entity_scores_on_reward

        db = MagicMock()
        entity = MagicMock()
        entity.id = uuid4()
        entity.data_quality_score = 0.5
        entity.tags = []
        db.query.return_value.filter.return_value.all.return_value = [entity]

        updated = update_entity_scores_on_reward(db, TENANT_ID, [entity.id], reward=0.5)

        assert updated == 1
        assert entity.data_quality_score == 0.52

    def test_negative_reward_drops_score(self):
        from app.services.rl_reward_service import update_entity_scores_on_reward

        db = MagicMock()
        entity = MagicMock()
        entity.id = uuid4()
        entity.data_quality_score = 0.5
        entity.tags = []
        db.query.return_value.filter.return_value.all.return_value = [entity]

        updated = update_entity_scores_on_reward(db, TENANT_ID, [entity.id], reward=-0.5)

        assert updated == 1
        assert entity.data_quality_score == 0.48

    def test_flags_for_review_below_threshold(self):
        from app.services.rl_reward_service import update_entity_scores_on_reward

        db = MagicMock()
        entity = MagicMock()
        entity.id = uuid4()
        entity.data_quality_score = 0.29  # Will drop to 0.27, below 0.3
        entity.tags = []
        db.query.return_value.filter.return_value.all.return_value = [entity]

        update_entity_scores_on_reward(db, TENANT_ID, [entity.id], reward=-0.5)

        assert entity.data_quality_score == pytest.approx(0.27, abs=0.001)
        assert "needs_review" in entity.tags

    def test_zero_reward_skips(self):
        from app.services.rl_reward_service import update_entity_scores_on_reward

        db = MagicMock()
        entity = MagicMock()
        entity.id = uuid4()
        entity.data_quality_score = 0.5
        db.query.return_value.filter.return_value.all.return_value = [entity]

        updated = update_entity_scores_on_reward(db, TENANT_ID, [entity.id], reward=0.0)

        assert updated == 0

    def test_empty_entity_ids_returns_zero(self):
        from app.services.rl_reward_service import update_entity_scores_on_reward

        db = MagicMock()
        updated = update_entity_scores_on_reward(db, TENANT_ID, [], reward=0.5)
        assert updated == 0

    def test_default_score_when_none(self):
        from app.services.rl_reward_service import update_entity_scores_on_reward

        db = MagicMock()
        entity = MagicMock()
        entity.id = uuid4()
        entity.data_quality_score = None  # Defaults to 0.5
        entity.tags = []
        db.query.return_value.filter.return_value.all.return_value = [entity]

        update_entity_scores_on_reward(db, TENANT_ID, [entity.id], reward=0.5)

        assert entity.data_quality_score == 0.52  # 0.5 + 0.02


class TestGetPlatformPerformance:
    """Test get_platform_performance from rl_experience_service."""

    def test_groups_by_platform_agent_task(self):
        from app.services.rl_experience_service import get_platform_performance

        db = MagicMock()
        row = MagicMock()
        row.platform = "claude_code"
        row.agent_slug = "luna"
        row.task_type = "general"
        row.total = 10
        row.avg_reward = 0.6
        row.positive_count = 8
        db.execute.return_value.fetchall.return_value = [row]

        result = get_platform_performance(db, TENANT_ID)

        assert len(result) == 1
        assert result[0]["platform"] == "claude_code"
        assert result[0]["agent_slug"] == "luna"
        assert result[0]["total"] == 10
        assert result[0]["avg_reward"] == 0.6
        assert result[0]["positive_pct"] == 80.0

    def test_empty_when_no_data(self):
        from app.services.rl_experience_service import get_platform_performance

        db = MagicMock()
        db.execute.return_value.fetchall.return_value = []

        result = get_platform_performance(db, TENANT_ID)

        assert result == []


class TestAgentRouterGetPlatformPerformance:
    """Test agent_router.get_platform_performance."""

    def test_returns_platform_stats(self):
        from app.services.agent_router import get_platform_performance

        db = MagicMock()
        row = MagicMock()
        row.platform = "claude_code"
        row.total = 20
        row.avg_reward = 0.45
        row.positive_count = 15
        db.execute.return_value.fetchall.return_value = [row]

        result = get_platform_performance(db, TENANT_ID)

        assert len(result) == 1
        assert result[0]["platform"] == "claude_code"
        assert result[0]["positive_pct"] == 75.0


class TestAgentRouterEnrichedState:
    """Test that agent router builds enriched state_text with entity context."""

    @patch("app.services.agent_router.run_agent_session")
    @patch("app.services.agent_router.rl_experience_service")
    @patch("app.services.agent_router.get_platform_performance")
    def test_state_text_includes_entity_context(
        self, mock_perf, mock_rl, mock_run
    ):
        from app.services.agent_router import route_and_execute

        db = MagicMock()
        features = MagicMock()
        features.default_cli_platform = "claude_code"
        db.query.return_value.filter.return_value.first.return_value = features

        mock_perf.return_value = [{"platform": "claude_code", "positive_pct": 80.0}]
        mock_run.return_value = ("response", {"status": "ok"})

        recalled_entities = [
            {"name": "Acme Corp", "entity_type": "company", "category": "customer"},
            {"name": "John Doe", "entity_type": "person", "category": "lead"},
        ]

        route_and_execute(
            db,
            tenant_id=TENANT_ID,
            user_id=uuid4(),
            message="Tell me about Acme Corp",
            recalled_entities=recalled_entities,
        )

        # Verify RL experience was logged with entity context in state
        mock_rl.log_experience.assert_called_once()
        call_kwargs = mock_rl.log_experience.call_args
        state = call_kwargs.kwargs["state"]
        assert state["entity_count"] == 2

        # Verify state_text contains entity info
        state_text = call_kwargs.kwargs["state_text"]
        assert "Acme Corp" in state_text
        assert "known_entities" in state_text

    @patch("app.services.agent_router.run_agent_session")
    @patch("app.services.agent_router.rl_experience_service")
    @patch("app.services.agent_router.get_platform_performance")
    def test_state_text_without_entities(self, mock_perf, mock_rl, mock_run):
        from app.services.agent_router import route_and_execute

        db = MagicMock()
        features = MagicMock()
        features.default_cli_platform = "claude_code"
        db.query.return_value.filter.return_value.first.return_value = features

        mock_perf.return_value = []
        mock_run.return_value = ("response", {})

        route_and_execute(
            db,
            tenant_id=TENANT_ID,
            user_id=uuid4(),
            message="Hello",
            recalled_entities=None,
        )

        call_kwargs = mock_rl.log_experience.call_args
        state_text = call_kwargs.kwargs["state_text"]
        assert "known_entities" not in state_text


class TestExperienceToObservation:
    """Test experience_to_observation activity from rl_policy_update."""

    @patch("app.workflows.activities.rl_policy_update.embedding_service")
    @patch("app.workflows.activities.rl_policy_update.SessionLocal")
    @pytest.mark.asyncio
    async def test_creates_observations_from_strong_signals(
        self, mock_session_cls, mock_embed_svc
    ):
        from app.workflows.activities.rl_policy_update import experience_to_observation

        db = MagicMock()
        mock_session_cls.return_value = db

        exp = MagicMock()
        exp.id = uuid4()
        exp.decision_point = "agent_routing"
        exp.reward = 0.8  # > 0.5 threshold
        exp.action = {"platform": "claude_code", "agent_slug": "luna"}
        exp.state = {"task_type": "code", "channel": "web"}
        exp.reward_source = "explicit_rating"

        q = MagicMock()
        q.filter.return_value = q
        q.all.return_value = [exp]
        db.query.return_value = q

        result = await experience_to_observation(TENANT_ID_STR)

        assert result["observations_created"] == 1
        # Verify INSERT was executed
        assert db.execute.called
        db.commit.assert_called()

    @patch("app.workflows.activities.rl_policy_update.embedding_service")
    @patch("app.workflows.activities.rl_policy_update.SessionLocal")
    @pytest.mark.asyncio
    async def test_skips_weak_signals(self, mock_session_cls, mock_embed_svc):
        from app.workflows.activities.rl_policy_update import experience_to_observation

        db = MagicMock()
        mock_session_cls.return_value = db

        # Experience with low reward (< 0.5)
        exp = MagicMock()
        exp.id = uuid4()
        exp.decision_point = "agent_routing"
        exp.reward = 0.2  # Below threshold
        exp.action = {"platform": "claude_code"}
        exp.state = {}
        exp.reward_source = "implicit"

        q = MagicMock()
        q.filter.return_value = q
        q.all.return_value = [exp]
        db.query.return_value = q

        result = await experience_to_observation(TENANT_ID_STR)

        assert result["observations_created"] == 0

    @patch("app.workflows.activities.rl_policy_update.embedding_service")
    @patch("app.workflows.activities.rl_policy_update.SessionLocal")
    @pytest.mark.asyncio
    async def test_negative_strong_signal_creates_observation(
        self, mock_session_cls, mock_embed_svc
    ):
        from app.workflows.activities.rl_policy_update import experience_to_observation

        db = MagicMock()
        mock_session_cls.return_value = db

        exp = MagicMock()
        exp.id = uuid4()
        exp.decision_point = "memory_recall"
        exp.reward = -0.7  # abs > 0.5
        exp.action = {"platform": "claude_code"}
        exp.state = {"task_type": "general"}
        exp.reward_source = "explicit_rating"

        q = MagicMock()
        q.filter.return_value = q
        q.all.return_value = [exp]
        db.query.return_value = q

        result = await experience_to_observation(TENANT_ID_STR)

        assert result["observations_created"] == 1


class TestComputeImplicitReward:
    """Test implicit reward computation from system signals."""

    def test_task_completed_signal(self):
        from app.services.rl_reward_service import compute_implicit_reward

        reward = compute_implicit_reward({"task_completed": True})
        assert reward == 0.3

    def test_task_failed_signal(self):
        from app.services.rl_reward_service import compute_implicit_reward

        reward = compute_implicit_reward({"task_failed": True})
        assert reward == -0.5

    def test_combined_signals(self):
        from app.services.rl_reward_service import compute_implicit_reward

        reward = compute_implicit_reward({
            "task_completed": True,
            "user_continued": True,
            "entity_referenced": True,
        })
        assert reward == pytest.approx(0.6, abs=0.001)

    def test_clamped_range(self):
        from app.services.rl_reward_service import compute_implicit_reward

        # All positive signals
        reward = compute_implicit_reward({
            "task_completed": True,
            "latency_below_p50": True,
            "user_continued": True,
            "notification_read": True,
            "entity_referenced": True,
            "memory_recall_positive_response": True,
            "deal_advanced": True,
            "pipeline_succeeded": True,
        })
        assert reward <= 1.0
        assert reward >= -1.0

    def test_empty_signals(self):
        from app.services.rl_reward_service import compute_implicit_reward

        reward = compute_implicit_reward({})
        assert reward == 0.0
