import os
os.environ["TESTING"] = "True"

import pytest
from unittest.mock import MagicMock, patch, AsyncMock


def test_select_template_returns_underscored_pattern():
    """Pattern names must use underscores to match CollaborationPattern enum."""
    # The bug: old code returned "propose-critique-revise" (hyphens)
    # Fix: must return "propose_critique_revise" (underscores)
    from app.workflows.activities.coalition_activities import _infer_pattern
    assert "_" in _infer_pattern("research competitors")
    assert "-" not in _infer_pattern("research competitors")


def test_incident_keywords_route_to_incident_investigation():
    from app.workflows.activities.coalition_activities import _infer_pattern
    assert _infer_pattern("investigate the incident in prod") == "incident_investigation"
    assert _infer_pattern("service outage detected") == "incident_investigation"
    assert _infer_pattern("pods are crash-looping") == "incident_investigation"
    assert _infer_pattern("pricing alert on SKUs") == "incident_investigation"


def test_infer_pattern_research_returns_research_synthesize():
    from app.workflows.activities.coalition_activities import _infer_pattern
    assert _infer_pattern("research the market") == "research_synthesize"


def test_infer_pattern_deploy_returns_plan_verify():
    from app.workflows.activities.coalition_activities import _infer_pattern
    assert _infer_pattern("deploy this fix") == "plan_verify"


def test_infer_pattern_default_returns_propose_critique_revise():
    from app.workflows.activities.coalition_activities import _infer_pattern
    assert _infer_pattern("write a poem") == "propose_critique_revise"
