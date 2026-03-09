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
        for attempt in range(max_retries + 1):
            response = self._client.post("/run", json=body)
            if response.status_code == 200:
                return response.json()
            # Retry on 500 (ADK wraps Vertex AI 429 rate limits as 500)
            if response.status_code >= 500 and attempt < max_retries:
                delay = 2 ** attempt  # 1s, 2s, 4s
                logger.warning(
                    "ADK /run returned %s (attempt %d/%d), retrying in %ds",
                    response.status_code, attempt + 1, max_retries + 1, delay,
                )
                time.sleep(delay)
                last_exc = httpx.HTTPStatusError(
                    message=f"Server error '{response.status_code}' for url '{response.url}'",
                    request=response.request,
                    response=response,
                )
                continue
            # Non-retryable error or last attempt
            response.raise_for_status()

        # Should not reach here, but just in case
        if last_exc:
            raise last_exc
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
