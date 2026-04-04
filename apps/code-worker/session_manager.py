"""Persistent Claude Code session manager.

Keeps long-lived Claude processes alive and routes messages to them
via stdin/stdout using stream-json format. Sessions persist across
messages — full native Claude context, tool state, and memory.

Usage:
    manager = SessionManager()
    result = await manager.send_message(tenant_id, message, config)
"""

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional, Dict

import httpx

logger = logging.getLogger(__name__)

API_BASE_URL = os.environ.get("API_BASE_URL", "http://servicetsunami-api").strip()
API_INTERNAL_KEY = os.environ.get("API_INTERNAL_KEY", "dev_mcp_key").strip()

# Max idle time before killing a session (seconds)
SESSION_IDLE_TIMEOUT = 600  # 10 minutes
# Max concurrent sessions per worker
MAX_SESSIONS = 20


@dataclass
class SessionConfig:
    claude_md_content: str = ""
    mcp_config: str = ""
    oauth_token: str = ""
    model: str = ""        # Override model slug (e.g. "claude-haiku-4-5-20251001"); empty = use env default
    allowed_tools: str = ""  # Comma-separated tool list override; empty = derive from MCP config


@dataclass
class ActiveSession:
    tenant_id: str
    session_id: str
    process: asyncio.subprocess.Process
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_used: float = field(default_factory=time.time)
    message_count: int = 0


class SessionManager:
    """Manages persistent Claude Code sessions per tenant."""

    def __init__(self):
        self._sessions: Dict[str, ActiveSession] = {}  # tenant_id -> ActiveSession
        self._cleanup_task: Optional[asyncio.Task] = None

    async def start(self):
        """Start the background cleanup task."""
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("SessionManager started (max_sessions=%d, idle_timeout=%ds)", MAX_SESSIONS, SESSION_IDLE_TIMEOUT)

    async def stop(self):
        """Stop all sessions and cleanup."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
        for tid in list(self._sessions.keys()):
            await self._kill_session(tid)
        logger.info("SessionManager stopped")

    async def send_message(
        self,
        tenant_id: str,
        message: str,
        config: SessionConfig,
        image_path: Optional[str] = None,
    ) -> Dict:
        """Send a message to a tenant's persistent Claude session.

        Creates the session if it doesn't exist. Returns the response dict.
        """
        session = self._sessions.get(tenant_id)

        if not session or session.process.returncode is not None:
            # Session doesn't exist or process died — create new one
            session = await self._create_session(tenant_id, config)
            if not session:
                return {"success": False, "error": "Failed to create Claude session"}

        async with session.lock:
            session.last_used = time.time()
            session.message_count += 1

            try:
                # Send message via stdin (stream-json format)
                msg_payload = {
                    "type": "user_message",
                    "content": message,
                }
                stdin_line = json.dumps(msg_payload) + "\n"
                session.process.stdin.write(stdin_line.encode())
                await session.process.stdin.drain()

                # Read response lines until we get a result
                response_text = ""
                metadata = {}

                while True:
                    try:
                        line = await asyncio.wait_for(
                            session.process.stdout.readline(),
                            timeout=300,  # 5 min max per message
                        )
                    except asyncio.TimeoutError:
                        return {"success": False, "error": "Claude response timed out (5 min)"}

                    if not line:
                        # Process died
                        await self._kill_session(tenant_id)
                        return {"success": False, "error": "Claude session terminated unexpectedly"}

                    line_str = line.decode().strip()
                    if not line_str:
                        continue

                    try:
                        event = json.loads(line_str)
                    except json.JSONDecodeError:
                        continue

                    event_type = event.get("type", "")

                    # Collect the final result
                    if event_type == "result":
                        response_text = event.get("result", "")
                        metadata = {
                            "claude_session_id": event.get("session_id", ""),
                            "input_tokens": (event.get("usage") or {}).get("input_tokens", 0),
                            "output_tokens": (event.get("usage") or {}).get("output_tokens", 0),
                            "model": event.get("model", ""),
                            "cost_usd": event.get("total_cost_usd", 0),
                            "num_turns": event.get("num_turns", 1),
                        }
                        break

                    # Skip intermediate events (assistant_message, tool_use, etc.)

                return {
                    "success": True,
                    "response_text": response_text,
                    "metadata": metadata,
                }

            except Exception as e:
                logger.exception("Error sending message to session %s", tenant_id)
                # Kill broken session so next message creates fresh one
                await self._kill_session(tenant_id)
                return {"success": False, "error": str(e)}

    @staticmethod
    def _build_allowed_tools(config: SessionConfig) -> str:
        """Build --allowedTools string from MCP config.

        Derives wildcard patterns from MCP server keys so the CLI
        auto-approves tool calls for all connected MCP servers.
        """
        import json as _json
        base_tools = ["Read"]
        try:
            mcp = _json.loads(config.mcp_config) if config.mcp_config else {}
            for server_key in mcp.get("mcpServers", {}):
                # Convert server key to MCP tool prefix pattern
                tool_prefix = f"mcp__{server_key}__*"
                base_tools.append(tool_prefix)
        except Exception:
            # Fallback to just servicetsunami
            base_tools.append("mcp__servicetsunami__*")
        return ",".join(base_tools)

    async def _create_session(self, tenant_id: str, config: SessionConfig) -> Optional[ActiveSession]:
        """Create a new persistent Claude session."""
        # Enforce max sessions
        if len(self._sessions) >= MAX_SESSIONS:
            # Kill oldest idle session
            oldest = min(self._sessions.values(), key=lambda s: s.last_used)
            await self._kill_session(oldest.tenant_id)

        # Create session directory for CLAUDE.md and MCP config
        session_dir = os.path.join("/tmp", "st_sessions", tenant_id)
        os.makedirs(session_dir, exist_ok=True)

        # Write CLAUDE.md
        if config.claude_md_content:
            with open(os.path.join(session_dir, "CLAUDE.md"), "w") as f:
                f.write(config.claude_md_content)

        # Write MCP config
        if config.mcp_config:
            with open(os.path.join(session_dir, "mcp.json"), "w") as f:
                f.write(config.mcp_config)

        # Build command
        session_id = str(uuid.uuid4())
        _allowed = config.allowed_tools or self._build_allowed_tools(config)
        cmd = [
            "claude",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--session-id", session_id,
            "--allowedTools", _allowed,
            "--verbose",
        ]

        # Per-request model override — falls back to CLAUDE_CODE_MODEL env var when empty
        if config.model:
            cmd.extend(["--model", config.model])

        # Add system prompt
        claude_md_path = os.path.join(session_dir, "CLAUDE.md")
        if os.path.exists(claude_md_path):
            with open(claude_md_path) as f:
                system_prompt = f.read()
            if system_prompt.strip():
                cmd.extend(["--append-system-prompt", system_prompt[:16000]])

        # Add MCP config
        mcp_path = os.path.join(session_dir, "mcp.json")
        if os.path.exists(mcp_path):
            cmd.extend(["--mcp-config", mcp_path])

        # Add session dir for file access
        cmd.extend(["--add-dir", session_dir])

        # Set environment
        env = os.environ.copy()
        if config.oauth_token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = config.oauth_token

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=session_dir,
            )

            session = ActiveSession(
                tenant_id=tenant_id,
                session_id=session_id,
                process=process,
            )
            self._sessions[tenant_id] = session
            logger.info(
                "Created Claude session for tenant %s (pid=%d, session_id=%s)",
                tenant_id[:8], process.pid, session_id,
            )
            return session

        except Exception as e:
            logger.exception("Failed to create Claude session for tenant %s", tenant_id)
            return None

    async def _kill_session(self, tenant_id: str):
        """Kill a session and clean up."""
        session = self._sessions.pop(tenant_id, None)
        if not session:
            return

        try:
            if session.process.returncode is None:
                session.process.terminate()
                try:
                    await asyncio.wait_for(session.process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    session.process.kill()
            logger.info("Killed session for tenant %s (messages=%d)", tenant_id[:8], session.message_count)
        except Exception:
            pass

    async def _cleanup_loop(self):
        """Periodically kill idle sessions."""
        while True:
            try:
                await asyncio.sleep(60)  # Check every minute
                now = time.time()
                to_kill = [
                    tid for tid, s in self._sessions.items()
                    if (now - s.last_used) > SESSION_IDLE_TIMEOUT
                    or s.process.returncode is not None
                ]
                for tid in to_kill:
                    logger.info("Cleaning up idle session for tenant %s", tid[:8])
                    await self._kill_session(tid)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Cleanup loop error")


# Global singleton
_manager: Optional[SessionManager] = None


def get_session_manager() -> SessionManager:
    global _manager
    if _manager is None:
        _manager = SessionManager()
    return _manager
