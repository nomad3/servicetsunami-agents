"""Lightweight HTTP client for interacting with the ADK API server."""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional
import uuid

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class ADKNotConfiguredError(RuntimeError):
    """Raised when ADK integration is requested without configuration."""


class ADKError(RuntimeError):
    """Raised when an ADK /run call fails with a specific error."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"ADK error {status_code}: {detail}")

    @property
    def user_message(self) -> str:
        """Return a user-friendly error message."""
        if "RESOURCE_EXHAUSTED" in self.detail or "quota" in self.detail.lower():
            return "The AI service has reached its daily usage limit. Please try again later or contact support."
        if "INVALID_ARGUMENT" in self.detail:
            return "The AI service rejected the request due to an invalid input. Please try a different message."
        if "Session not found" in self.detail or "404" in str(self.status_code):
            return "Your session has expired. Please start a new conversation."
        if "Cannot reach" in self.detail or "Connection" in self.detail or self.status_code == 503:
            return "Sorry, I can't process your message right now. The AI service is temporarily restarting. Please try again in a couple of minutes."
        return f"Sorry, something went wrong processing your message. Please try again in a moment."


def _extract_error_detail(response: httpx.Response) -> str:
    """Extract a meaningful error description from an ADK error response."""
    try:
        body = response.text
        # ADK errors often contain a traceback — look for the last meaningful line
        if "RESOURCE_EXHAUSTED" in body:
            return "RESOURCE_EXHAUSTED: Gemini API daily quota exceeded"
        if "INVALID_ARGUMENT" in body:
            # Try to find the specific field issue
            for line in body.split("\n"):
                if "INVALID_ARGUMENT" in line:
                    return line.strip()[:300]
            return "INVALID_ARGUMENT: Gemini rejected the request"
        if "Permission" in body or "PERMISSION_DENIED" in body:
            return "PERMISSION_DENIED: API key may be invalid or missing"
        if "NOT_FOUND" in body:
            return "NOT_FOUND: Model or session not found"
        # Generic: return last non-empty line of the traceback
        lines = [l.strip() for l in body.strip().split("\n") if l.strip()]
        if lines:
            return lines[-1][:300]
    except Exception:
        pass
    return f"HTTP {response.status_code}"


class ADKClient:
    """Simple wrapper around the ADK FastAPI server."""

    def __init__(
        self,
        *,
        base_url: str,
        app_name: str,
        timeout: float = 300.0,
        client: Optional[httpx.Client] = None,
    ) -> None:
        if not base_url:
            raise ADKNotConfiguredError("ADK_BASE_URL is not configured.")
        self.base_url = base_url.rstrip("/")
        self.app_name = app_name
        self._client = client or httpx.Client(base_url=self.base_url, timeout=timeout)

    def create_session(
        self,
        *,
        user_id: uuid.UUID,
        state: Optional[Dict[str, Any]] = None,
        events: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        if state:
            payload["state"] = state
        if events:
            payload["events"] = events

        response = self._client.post(
            f"/apps/{self.app_name}/users/{user_id}/sessions",
            json=payload or None,
        )
        response.raise_for_status()
        return response.json()

    def run(
        self,
        *,
        user_id: uuid.UUID,
        session_id: str,
        message: Optional[str] = None,
        parts: Optional[List[Dict[str, Any]]] = None,
        state_delta: Optional[Dict[str, Any]] = None,
        max_retries: int = 3,
    ) -> List[Dict[str, Any]]:
        # Build message parts: use explicit parts if provided, otherwise
        # fall back to a single text part from the message string.
        if parts is not None:
            message_parts = parts
        elif message is not None:
            message_parts = [{"text": message}]
        else:
            raise ValueError("Either 'message' or 'parts' must be provided.")

        body: Dict[str, Any] = {
            "app_name": self.app_name,
            "user_id": str(user_id),
            "session_id": session_id,
            "new_message": {
                "role": "user",
                "parts": message_parts,
            },
        }
        if state_delta:
            body["state_delta"] = state_delta

        last_exc: Optional[Exception] = None
        last_detail: str = ""
        for attempt in range(max_retries + 1):
            try:
                response = self._client.post("/run", json=body)
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, ConnectionError) as conn_err:
                # ADK pod may be restarting — retry connection errors
                if attempt < max_retries:
                    delay = 2 ** attempt
                    logger.warning(
                        "ADK /run connection failed (attempt %d/%d), retrying in %ds — %s",
                        attempt + 1, max_retries + 1, delay, str(conn_err)[:200],
                    )
                    time.sleep(delay)
                    last_detail = f"Connection failed: {conn_err}"
                    last_exc = conn_err
                    continue
                raise ADKError(503, f"Cannot reach ADK server: {conn_err}")

            if response.status_code == 200:
                return response.json()

            # Try to extract a meaningful error detail from the response body
            detail = _extract_error_detail(response)

            # Don't retry on quota exhaustion or context window overflow — it won't help
            if "RESOURCE_EXHAUSTED" in detail or "quota" in detail.lower():
                raise ADKError(response.status_code, detail)
            if "too long" in detail or "ContextWindow" in detail or "prompt is too long" in detail:
                raise ADKError(response.status_code, detail)

            # Retry on other 500s (transient failures)
            if response.status_code >= 500 and attempt < max_retries:
                delay = 2 ** attempt  # 1s, 2s, 4s
                logger.warning(
                    "ADK /run returned %s (attempt %d/%d), retrying in %ds — %s",
                    response.status_code, attempt + 1, max_retries + 1, delay,
                    detail[:200],
                )
                time.sleep(delay)
                last_detail = detail
                last_exc = httpx.HTTPStatusError(
                    message=f"Server error '{response.status_code}' for url '{response.url}'",
                    request=response.request,
                    response=response,
                )
                continue
            # Non-retryable error or last attempt
            raise ADKError(response.status_code, detail)

        # All retries exhausted
        if last_exc:
            raise ADKError(503, last_detail or str(last_exc))
        return []

    def close(self) -> None:
        self._client.close()


_adk_client: Optional[ADKClient] = None


def get_adk_client() -> ADKClient:
    """Return a cached ADK client instance."""
    global _adk_client
    if _adk_client is None:
        if not settings.ADK_BASE_URL:
            raise ADKNotConfiguredError("ADK_BASE_URL is not configured.")
        _adk_client = ADKClient(
            base_url=settings.ADK_BASE_URL,
            app_name=settings.ADK_APP_NAME,
        )
    return _adk_client
