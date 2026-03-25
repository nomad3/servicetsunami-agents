from datetime import timedelta

from app.workflows.activities.dynamic_step import _http_timeout_for_step
from app.workflows.dynamic_executor import _heartbeat_for


def test_mcp_tool_http_timeout_uses_step_budget():
    timeout = _http_timeout_for_step({"type": "mcp_tool"}, default_seconds=60.0)

    assert timeout.connect == 10.0
    assert timeout.read == 60.0
    assert timeout.write == 60.0
    assert timeout.pool == 10.0


def test_mcp_tool_http_timeout_respects_custom_timeout():
    timeout = _http_timeout_for_step(
        {"type": "mcp_tool", "timeout_seconds": 180},
        default_seconds=60.0,
    )

    assert timeout.read == 180.0
    assert timeout.write == 180.0


def test_dynamic_workflow_steps_do_not_default_heartbeat_timeout():
    assert _heartbeat_for({"type": "agent"}) is None
    assert _heartbeat_for({"type": "workflow"}) is None


def test_dynamic_workflow_steps_allow_explicit_heartbeat_timeout():
    assert _heartbeat_for({"type": "agent", "heartbeat_seconds": 120}) == timedelta(seconds=120)
