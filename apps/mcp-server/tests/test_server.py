"""Tests for MCP server"""
import pytest
import src.mcp_tools  # noqa: F401 — registers @mcp.tool() decorators
from src.mcp_app import mcp


def test_server_has_tools():
    """Test that server exposes expected tools"""
    # FastMCP stores tools in _tool_manager._tools dict
    tool_names = list(mcp._tool_manager._tools.keys())

    expected_tools = [
        "connect_postgres",
        "verify_connection",  # Note: renamed from test_connection
        "list_source_tables",
        "sync_table_to_bronze",
        "upload_file",
        "query_sql",
        "list_tables",
        "describe_table",
        "transform_to_silver",
    ]

    for tool in expected_tools:
        assert tool in tool_names, f"Missing tool: {tool}"


def test_server_name():
    """Test server has correct name"""
    assert mcp.name == "ServiceTsunami"
