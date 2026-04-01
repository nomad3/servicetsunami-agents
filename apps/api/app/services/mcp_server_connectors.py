"""Service layer for MCP server connectors."""
import logging
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
from sqlalchemy.orm import Session

from app.models.mcp_server_connector import MCPServerConnector, MCPServerCallLog

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def create_mcp_server(
    db: Session,
    tenant_id: uuid.UUID,
    name: str,
    server_url: str,
    transport: str = "sse",
    auth_type: str = "none",
    auth_token: Optional[str] = None,
    auth_header: Optional[str] = None,
    custom_headers: Optional[Dict[str, str]] = None,
    description: Optional[str] = None,
    enabled: bool = True,
) -> MCPServerConnector:
    """Create a new MCP server connector."""
    connector = MCPServerConnector(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        name=name,
        description=description,
        server_url=server_url,
        transport=transport,
        auth_type=auth_type,
        auth_token=auth_token,
        auth_header=auth_header,
        custom_headers=custom_headers,
        enabled=enabled,
    )
    db.add(connector)
    db.commit()
    db.refresh(connector)
    logger.info("Created MCP server connector '%s' (id=%s) for tenant %s", name, connector.id, tenant_id)
    return connector


def list_mcp_servers(
    db: Session,
    tenant_id: uuid.UUID,
    status: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
) -> List[MCPServerConnector]:
    """List MCP server connectors for a tenant."""
    query = db.query(MCPServerConnector).filter(MCPServerConnector.tenant_id == tenant_id)
    if status:
        query = query.filter(MCPServerConnector.status == status)
    return query.order_by(MCPServerConnector.created_at.desc()).offset(skip).limit(limit).all()


def get_mcp_server(db: Session, tenant_id: uuid.UUID, connector_id: uuid.UUID) -> Optional[MCPServerConnector]:
    """Get a single MCP server connector by ID."""
    return db.query(MCPServerConnector).filter(
        MCPServerConnector.id == connector_id,
        MCPServerConnector.tenant_id == tenant_id,
    ).first()


def update_mcp_server(
    db: Session,
    tenant_id: uuid.UUID,
    connector_id: uuid.UUID,
    updates: Dict[str, Any],
) -> Optional[MCPServerConnector]:
    """Update an MCP server connector."""
    connector = get_mcp_server(db, tenant_id, connector_id)
    if not connector:
        return None
    for key, value in updates.items():
        if value is not None and hasattr(connector, key):
            setattr(connector, key, value)
    connector.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(connector)
    return connector


def delete_mcp_server(db: Session, tenant_id: uuid.UUID, connector_id: uuid.UUID) -> bool:
    """Delete an MCP server connector and its call logs."""
    connector = get_mcp_server(db, tenant_id, connector_id)
    if not connector:
        return False
    db.query(MCPServerCallLog).filter(MCPServerCallLog.mcp_server_connector_id == connector_id).delete()
    db.delete(connector)
    db.commit()
    logger.info("Deleted MCP server connector %s for tenant %s", connector_id, tenant_id)
    return True


# ---------------------------------------------------------------------------
# Call logs
# ---------------------------------------------------------------------------

def log_call(
    db: Session,
    tenant_id: uuid.UUID,
    connector_id: uuid.UUID,
    tool_name: str,
    arguments: Optional[Dict] = None,
    result: Optional[Dict] = None,
    success: bool = False,
    error_message: Optional[str] = None,
    duration_ms: Optional[int] = None,
) -> MCPServerCallLog:
    """Record an MCP tool call attempt."""
    log = MCPServerCallLog(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        mcp_server_connector_id=connector_id,
        tool_name=tool_name,
        arguments=arguments,
        result=result,
        success=success,
        error_message=error_message,
        duration_ms=duration_ms,
    )
    db.add(log)
    db.commit()
    return log


def get_call_logs(
    db: Session,
    tenant_id: uuid.UUID,
    connector_id: Optional[uuid.UUID] = None,
    limit: int = 50,
) -> List[MCPServerCallLog]:
    """Fetch recent call logs."""
    query = db.query(MCPServerCallLog).filter(MCPServerCallLog.tenant_id == tenant_id)
    if connector_id:
        query = query.filter(MCPServerCallLog.mcp_server_connector_id == connector_id)
    return query.order_by(MCPServerCallLog.created_at.desc()).limit(limit).all()


# ---------------------------------------------------------------------------
# SSE session helper
# ---------------------------------------------------------------------------

def _sse_jsonrpc_call(
    base_url: str, headers: Dict[str, str], rpc_body: dict, timeout: float = 15
) -> dict:
    """Execute a JSON-RPC call over MCP SSE transport.

    MCP SSE protocol is asynchronous:
    1. GET /mcp/sse → SSE stream (must stay open)
    2. Server sends event: endpoint with data: /mcp/messages?session_id=xxx
    3. Client POSTs JSON-RPC to that session URL → 202 Accepted
    4. Server sends the JSON-RPC response as an SSE event: message
    5. Client reads response from the SSE stream, then closes
    """
    import urllib.parse
    import threading
    import json as _json

    clean_headers = {k: v for k, v in headers.items() if k != "Content-Type"}
    clean_headers["Accept"] = "text/event-stream"

    messages_url = None
    response_data = {}
    error_holder = [None]
    rpc_id = str(rpc_body.get("id", 1))

    def _run_sse():
        nonlocal messages_url, response_data
        try:
            with httpx.Client(timeout=httpx.Timeout(timeout, read=timeout)) as client:
                with client.stream("GET", base_url, headers=clean_headers) as resp:
                    resp.raise_for_status()
                    buffer = ""
                    current_event = ""
                    for chunk in resp.iter_raw():
                        buffer += chunk.decode("utf-8", errors="replace")
                        # Process complete lines
                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            line = line.rstrip("\r")
                            if line.startswith("event: "):
                                current_event = line[7:].strip()
                            elif line.startswith("data: "):
                                data_str = line[6:].strip()
                                if current_event == "endpoint":
                                    # Resolve the messages URL
                                    if data_str.startswith("/"):
                                        parsed = urllib.parse.urlparse(base_url)
                                        messages_url = f"{parsed.scheme}://{parsed.netloc}{data_str}"
                                    elif data_str.startswith("http"):
                                        messages_url = data_str
                                    else:
                                        b = base_url.rsplit("/", 1)[0]
                                        messages_url = f"{b}/{data_str}"
                                elif current_event == "message":
                                    # JSON-RPC response
                                    try:
                                        response_data.update(_json.loads(data_str))
                                    except _json.JSONDecodeError:
                                        pass
                                    return  # Got our response, done
                            elif line == "":
                                current_event = ""
                        # Exit if we got the response
                        if response_data:
                            return
        except Exception as e:
            error_holder[0] = e

    # Run SSE in background thread
    sse_thread = threading.Thread(target=_run_sse, daemon=True)
    sse_thread.start()

    # Wait for session URL
    deadline = time.time() + timeout
    while not messages_url and time.time() < deadline:
        time.sleep(0.05)
        if error_holder[0]:
            raise error_holder[0]

    if not messages_url:
        raise RuntimeError(f"SSE endpoint at {base_url} did not return a messages URL within {timeout}s")

    # POST the JSON-RPC request
    with httpx.Client(timeout=float(timeout), follow_redirects=True) as client:
        resp = client.post(messages_url, json=rpc_body, headers=headers)
        # 202 Accepted is the expected SSE response — result comes via stream
        if resp.status_code not in (200, 202):
            raise RuntimeError(f"MCP POST failed: HTTP {resp.status_code}: {resp.text[:200]}")

    # Wait for SSE response
    sse_thread.join(timeout=timeout)
    if error_holder[0]:
        raise error_holder[0]
    if not response_data:
        raise RuntimeError("No JSON-RPC response received from SSE stream")

    return response_data


# ---------------------------------------------------------------------------
# Tool discovery (via HTTP to MCP server)
# ---------------------------------------------------------------------------

def _build_auth_headers(connector: MCPServerConnector) -> Dict[str, str]:
    """Build authentication headers for an MCP server request."""
    headers = {"Content-Type": "application/json"}
    if connector.custom_headers:
        headers.update(connector.custom_headers)
    if connector.auth_type == "none" or not connector.auth_token:
        return headers
    header_name = connector.auth_header or "Authorization"
    if connector.auth_type == "bearer":
        headers[header_name] = f"Bearer {connector.auth_token}"
    elif connector.auth_type == "api_key":
        headers[header_name] = connector.auth_token
    elif connector.auth_type == "basic":
        headers[header_name] = f"Basic {connector.auth_token}"
    return headers


def discover_tools(db: Session, connector: MCPServerConnector, timeout: int = 15) -> Dict[str, Any]:
    """Discover available tools from an MCP server via tools/list JSON-RPC call.

    Works with SSE and streamable-http transports.
    """
    headers = _build_auth_headers(connector)

    # JSON-RPC 2.0 request for tools/list
    rpc_body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {},
    }

    start = time.time()
    try:
        if connector.transport == "sse":
            data = _sse_jsonrpc_call(connector.server_url.rstrip("/"), headers, rpc_body, timeout=float(timeout))
        else:
            url = connector.server_url.rstrip("/")
            with httpx.Client(timeout=float(timeout), follow_redirects=True) as client:
                resp = client.post(url, json=rpc_body, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        duration_ms = int((time.time() - start) * 1000)

        tools = data.get("result", {}).get("tools", [])
        # Cache discovered tools
        tool_list = [
            {
                "name": t.get("name"),
                "description": t.get("description", ""),
                "input_schema": t.get("inputSchema", {}),
            }
            for t in tools
        ]
        connector.tools_discovered = tool_list
        connector.tool_count = len(tool_list)
        connector.status = "connected"
        connector.last_health_check = datetime.utcnow()
        connector.last_error = None
        db.commit()
        return {
            "status": "connected",
            "tool_count": len(tool_list),
            "tools": tool_list,
            "duration_ms": duration_ms,
        }
    except Exception as e:
        duration_ms = int((time.time() - start) * 1000)
        error_msg = str(e)
        connector.status = "error"
        connector.last_error = error_msg
        connector.last_health_check = datetime.utcnow()
        db.commit()
        logger.exception("Tool discovery failed for MCP server %s", connector.id)
        return {"status": "error", "error": error_msg, "duration_ms": duration_ms}


# ---------------------------------------------------------------------------
# Tool call proxy
# ---------------------------------------------------------------------------

def call_tool(
    db: Session,
    connector: MCPServerConnector,
    tool_name: str,
    arguments: Dict[str, Any],
    channel: str = "api",
    agent_slug: Optional[str] = None,
    timeout: int = 60,
) -> Dict[str, Any]:
    """Proxy a tool call to an external MCP server via JSON-RPC."""
    from app.schemas.safety_policy import ActionType, PolicyDecision, SafetyEnforcementRequest
    from app.services import safety_enforcement

    enforcement = safety_enforcement.enforce_action(
        db,
        tenant_id=connector.tenant_id,
        request=SafetyEnforcementRequest(
            action_type=ActionType.MCP_TOOL,
            action_name="call_mcp_tool",
            channel=channel,
            proposed_action={
                "connector_id": str(connector.id),
                "remote_tool_name": tool_name,
                "arguments": arguments,
            },
            world_state_facts=[],
            recent_observations=[],
            assumptions=["This action proxies execution to an external MCP server."],
            uncertainty_notes=["The downstream tool contract and side effects may not be fully inspectable locally."],
            context_summary=f"External MCP proxy call to '{tool_name}' via connector '{connector.name}'.",
            context_ref={"connector_id": str(connector.id), "tool_name": tool_name},
            expected_downside="An external MCP server may execute arbitrary high-impact tool actions.",
            agent_slug=agent_slug,
        ),
    )
    if enforcement.decision not in (PolicyDecision.ALLOW, PolicyDecision.ALLOW_WITH_LOGGING):
        return {
            "success": False,
            "tool_name": tool_name,
            "error": (
                f"Governed action 'call_mcp_tool' requires {enforcement.decision.value} "
                f"for channel '{channel}'. evidence_pack_id={enforcement.evidence_pack_id}"
            ),
        }

    headers = _build_auth_headers(connector)

    rpc_body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments,
        },
    }

    start = time.time()
    try:
        if connector.transport == "sse":
            data = _sse_jsonrpc_call(connector.server_url.rstrip("/"), headers, rpc_body, timeout=float(timeout))
        else:
            url = connector.server_url.rstrip("/")
            with httpx.Client(timeout=float(timeout), follow_redirects=True) as client:
                resp = client.post(url, json=rpc_body, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        duration_ms = int((time.time() - start) * 1000)

        result = data.get("result", {})
        # Extract text content from MCP response
        content = result.get("content", [])
        result_data = {}
        if content:
            for item in content:
                if item.get("type") == "text":
                    try:
                        import json
                        result_data = json.loads(item["text"])
                    except (json.JSONDecodeError, KeyError):
                        result_data = {"text": item.get("text", "")}
                    break
            if not result_data:
                result_data = {"content": content}
        else:
            result_data = result

        log_call(
            db, connector.tenant_id, connector.id, tool_name,
            arguments=arguments, result=result_data, success=True,
            duration_ms=duration_ms,
        )
        connector.call_count = (connector.call_count or 0) + 1
        db.commit()
        return {
            "success": True,
            "tool_name": tool_name,
            "result": result_data,
            "duration_ms": duration_ms,
        }
    except Exception as e:
        duration_ms = int((time.time() - start) * 1000)
        error_msg = str(e)
        log_call(
            db, connector.tenant_id, connector.id, tool_name,
            arguments=arguments, success=False, error_message=error_msg,
            duration_ms=duration_ms,
        )
        connector.error_count = (connector.error_count or 0) + 1
        db.commit()
        logger.exception("Tool call failed for MCP server %s tool %s", connector.id, tool_name)
        return {"success": False, "tool_name": tool_name, "error": error_msg, "duration_ms": duration_ms}


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

def health_check(db: Session, connector: MCPServerConnector, timeout: int = 10) -> Dict[str, Any]:
    """Ping an MCP server to verify connectivity."""
    headers = _build_auth_headers(connector)

    # Use initialize or ping JSON-RPC
    rpc_body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "servicetsunami", "version": "1.0.0"},
        },
    }

    start = time.time()
    try:
        if connector.transport == "sse":
            data = _sse_jsonrpc_call(connector.server_url.rstrip("/"), headers, rpc_body, timeout=float(timeout))
        else:
            url = connector.server_url.rstrip("/")
            with httpx.Client(timeout=float(timeout), follow_redirects=True) as client:
                resp = client.post(url, json=rpc_body, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        duration_ms = int((time.time() - start) * 1000)

        server_info = data.get("result", {}).get("serverInfo", {})
        connector.status = "connected"
        connector.last_health_check = datetime.utcnow()
        connector.last_error = None
        db.commit()
        return {
            "status": "connected",
            "server_info": server_info,
            "protocol_version": data.get("result", {}).get("protocolVersion"),
            "duration_ms": duration_ms,
        }
    except Exception as e:
        duration_ms = int((time.time() - start) * 1000)
        error_msg = str(e)
        connector.status = "error"
        connector.last_error = error_msg
        connector.last_health_check = datetime.utcnow()
        db.commit()
        return {"status": "error", "error": error_msg, "duration_ms": duration_ms}
