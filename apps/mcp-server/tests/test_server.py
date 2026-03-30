"""Tests for MCP server"""
import pytest
import src.mcp_tools  # noqa: F401 — registers @mcp.tool() decorators
from src.mcp_app import mcp


def test_server_has_tools():
    """Test that server exposes expected tools"""
    # FastMCP stores tools in _tool_manager._tools dict
    tool_names = list(mcp._tool_manager._tools.keys())

    # Core tools that must always be present
    expected_tools = [
        "check_aremko_availability",
        "search_knowledge",
        "find_entities",
        "create_entity",
        "record_observation",
        "search_emails",
        "send_email",
        "list_calendar_events",
        "search_jira_issues",
    ]

    for tool in expected_tools:
        assert tool in tool_names, f"Missing tool: {tool}"

    # Should have many more tools (81+)
    assert len(tool_names) >= 50, f"Expected 50+ tools, got {len(tool_names)}"


def test_server_name():
    """Test server has correct name"""
    assert mcp.name == "agentprovision.com"
