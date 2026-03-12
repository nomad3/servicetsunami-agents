from sqlalchemy.orm import Session
from typing import List, Optional
import uuid

from app.models.agent import Agent
from app.models.agent_relationship import AgentRelationship
from app.models.tenant_features import TenantFeatures
from app.services import rl_policy_engine


class TaskDispatcher:
    """Dispatches tasks to appropriate agents based on capabilities."""

    def __init__(self, db: Session):
        self.db = db

    def find_best_agent(
        self,
        group_id: uuid.UUID,
        required_capabilities: List[str],
        tenant_id: uuid.UUID,
        exclude_agent_ids: List[uuid.UUID] = None
    ) -> Optional[Agent]:
        """
        Find the best agent in a group for given capabilities.

        Args:
            group_id: The agent group to search in
            required_capabilities: List of capabilities needed
            tenant_id: Tenant ID for security
            exclude_agent_ids: Agents to exclude (e.g., already tried)

        Returns:
            Best matching Agent or None
        """
        # Get all agents in this group via relationships
        relationships = self.db.query(AgentRelationship).filter(
            AgentRelationship.group_id == group_id
        ).all()

        # Collect unique agent IDs
        agent_ids = set()
        for rel in relationships:
            agent_ids.add(rel.from_agent_id)
            agent_ids.add(rel.to_agent_id)

        if exclude_agent_ids:
            agent_ids -= set(exclude_agent_ids)

        if not agent_ids:
            return None

        # Get agents with their capabilities
        agents = self.db.query(Agent).filter(
            Agent.id.in_(agent_ids),
            Agent.tenant_id == tenant_id
        ).all()

        # Check if RL is enabled for this tenant
        features = self.db.query(TenantFeatures).filter(TenantFeatures.tenant_id == tenant_id).first()
        rl_enabled = features.rl_enabled if features else False

        if rl_enabled:
            candidates = [
                {"id": str(a.id), "name": a.name, "capabilities": a.capabilities or []}
                for a in agents
            ]
            state = {
                "task_type": "agent_task",
                "required_capabilities": required_capabilities or [],
                "agent_count": len(agents),
            }
            try:
                chosen, _explanation = rl_policy_engine.select_action(
                    self.db, tenant_id, "agent_selection", state, candidates
                )
                for agent in agents:
                    if str(agent.id) == chosen.get("id"):
                        return agent
            except Exception:
                pass  # Fall through to heuristic scoring

        # Heuristic fallback (Layer 0) — score agents based on capability match
        best_agent = None
        best_score = -1

        for agent in agents:
            score = self._calculate_capability_score(agent, required_capabilities)
            if score > best_score:
                best_score = score
                best_agent = agent

        return best_agent

    def _calculate_capability_score(self, agent: Agent, required_capabilities: List[str]) -> float:
        """Calculate how well an agent matches required capabilities."""
        if not agent.capabilities:
            return 0.0

        agent_caps = set(agent.capabilities)
        required_caps = set(required_capabilities)

        if not required_caps:
            return 1.0

        # Count matches
        matches = len(agent_caps & required_caps)
        return matches / len(required_caps)

    def get_supervisor(self, agent_id: uuid.UUID, group_id: uuid.UUID) -> Optional[Agent]:
        """Get the supervisor agent for a given agent in a group."""
        rel = self.db.query(AgentRelationship).filter(
            AgentRelationship.group_id == group_id,
            AgentRelationship.to_agent_id == agent_id,
            AgentRelationship.relationship_type == "supervises"
        ).first()

        if rel:
            return self.db.query(Agent).filter(Agent.id == rel.from_agent_id).first()
        return None

    def get_subordinates(self, agent_id: uuid.UUID, group_id: uuid.UUID) -> List[Agent]:
        """Get agents supervised by a given agent."""
        rels = self.db.query(AgentRelationship).filter(
            AgentRelationship.group_id == group_id,
            AgentRelationship.from_agent_id == agent_id,
            AgentRelationship.relationship_type == "supervises"
        ).all()

        subordinate_ids = [rel.to_agent_id for rel in rels]
        if not subordinate_ids:
            return []

        return self.db.query(Agent).filter(Agent.id.in_(subordinate_ids)).all()

    def can_delegate(self, from_agent: Agent, to_agent: Agent, group_id: uuid.UUID) -> bool:
        """Check if one agent can delegate to another."""
        rel = self.db.query(AgentRelationship).filter(
            AgentRelationship.group_id == group_id,
            AgentRelationship.from_agent_id == from_agent.id,
            AgentRelationship.to_agent_id == to_agent.id,
            AgentRelationship.relationship_type.in_(["supervises", "delegates_to"])
        ).first()

        return rel is not None
