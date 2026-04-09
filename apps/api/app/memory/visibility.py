"""SQL visibility filter for multi-agent scoping (design doc §7).

Applied at the query layer in `memory/_query.py`. Business logic does
not need to think about visibility — the memory API enforces it.

A record is visible to `agent_slug` iff one of:
  1. visibility = 'tenant_wide' (default — visible to all agents)
  2. visibility = 'agent_scoped' AND owner_agent_slug = agent_slug
  3. visibility = 'agent_group'  AND agent_slug IN visible_to[]

The signature `apply_visibility(query, model, agent_slug)` is locked
and matches the Task 10 stub call sites.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.orm.query import Query


def apply_visibility(query: Query, model: Any, agent_slug: str) -> Query:
    """Filter `query` to records visible to `agent_slug`.

    Requires `model` to expose three columns added by migration 087:
      - visibility       (VARCHAR(20), NOT NULL, default 'tenant_wide')
      - owner_agent_slug (VARCHAR(100), NULL)
      - visible_to       (TEXT[], NULL)

    Returns the same query with an additional WHERE clause.
    """
    return query.filter(
        or_(
            model.visibility == "tenant_wide",
            and_(
                model.visibility == "agent_scoped",
                model.owner_agent_slug == agent_slug,
            ),
            and_(
                model.visibility == "agent_group",
                # PostgreSQL ANY operator on TEXT[] column
                model.visible_to.any(agent_slug),
            ),
        )
    )
