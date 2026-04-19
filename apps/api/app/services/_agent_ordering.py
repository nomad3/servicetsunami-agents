"""Shared ordering helpers for the Agent model.

The auto-select logic that picks a default Agent for chat/WhatsApp/robot
sessions needs to rank lifecycle statuses semantically (production > staging
> draft > deprecated) — `Agent.status.desc()` sorts alphabetically (staging >
production) which is wrong. Keep the CASE expression in one place so all
call sites stay consistent.
"""
from sqlalchemy import case

from app.models.agent import Agent


# Lower rank wins. Use in ORDER BY as `agent_status_rank.asc()`.
agent_status_rank = case(
    (Agent.status == "production", 0),
    (Agent.status == "staging", 1),
    (Agent.status == "draft", 2),
    (Agent.status == "deprecated", 3),
    else_=4,
)
