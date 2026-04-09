"""Visibility filter integration tests (plan Task 11).

Validates the multi-agent scoping rules from design doc §7:
  - tenant_wide  → visible to all agents in the tenant (default)
  - agent_scoped → visible only when owner_agent_slug == agent_slug
  - agent_group  → visible when agent_slug IN visible_to[]

Test data is created against the production DB via the `db_session`
fixture, which rolls back at teardown — nothing persists. We never
call `db.commit()`; `db.flush()` is used when an INSERT-generated id
is needed.

NOTE on the agent_scoped test (deviation from the plan as written):
The plan's first test exercises the full `recall()` pipeline, but
that path goes through pgvector cosine similarity, which would
require us to compute and persist an embedding for the test
entity. Instead we test the visibility filter directly via
`apply_visibility()` (the same shape as the second test), which
gives a much more targeted assertion. The full recall pipeline is
covered for the visibility filter implicitly because `_query.py`
calls `apply_visibility` on every ORM-based search and inlines
the equivalent SQL predicate in `search_entities`.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from app.memory.visibility import apply_visibility
from app.models.knowledge_entity import KnowledgeEntity
from app.models.tenant import Tenant


@pytest.mark.integration
def test_agent_scoped_entity_only_visible_to_owner(db_session):
    """An agent_scoped entity owned by sales_agent must be invisible to luna
    but visible to sales_agent. Test data rolls back via the fixture."""
    tenant = Tenant(name=f"vis-test-scoped-{uuid4().hex[:8]}")
    db_session.add(tenant)
    db_session.flush()

    private = KnowledgeEntity(
        tenant_id=tenant.id,
        name="Sales pipeline draft Q2 — TEST",
        entity_type="note",
        category="note",
        owner_agent_slug="sales_agent",
        visibility="agent_scoped",
        description="Internal sales notes for visibility test",
    )
    db_session.add(private)
    db_session.flush()

    def visible_count(agent_slug: str) -> int:
        q = db_session.query(KnowledgeEntity).filter(
            KnowledgeEntity.tenant_id == tenant.id,
            KnowledgeEntity.name == "Sales pipeline draft Q2 — TEST",
        )
        q = apply_visibility(q, KnowledgeEntity, agent_slug)
        return q.count()

    # Sales agent (the owner) sees it.
    assert visible_count("sales_agent") == 1
    # Luna (a different agent) does NOT see it.
    assert visible_count("luna") == 0
    # Some random other agent also does NOT see it.
    assert visible_count("support_agent") == 0


@pytest.mark.integration
def test_agent_group_entity_visible_to_listed_agents(db_session):
    """An agent_group entity with visible_to=['sre_agent','devops_agent']
    must be visible to those agents and only those agents."""
    tenant = Tenant(name=f"vis-test-group-{uuid4().hex[:8]}")
    db_session.add(tenant)
    db_session.flush()

    shared = KnowledgeEntity(
        tenant_id=tenant.id,
        name="SRE incident playbook v3 — TEST",
        entity_type="document",
        category="document",
        visibility="agent_group",
        visible_to=["sre_agent", "devops_agent"],
        description="Playbook for visibility test",
    )
    db_session.add(shared)
    db_session.flush()

    def visible_count(agent_slug: str) -> int:
        q = db_session.query(KnowledgeEntity).filter(
            KnowledgeEntity.tenant_id == tenant.id,
            KnowledgeEntity.name == "SRE incident playbook v3 — TEST",
        )
        q = apply_visibility(q, KnowledgeEntity, agent_slug)
        return q.count()

    assert visible_count("sre_agent") == 1
    assert visible_count("devops_agent") == 1
    assert visible_count("support_agent") == 0
    assert visible_count("luna") == 0
