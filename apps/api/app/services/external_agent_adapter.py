import hashlib
import hmac
import json
import logging

import httpx
from sqlalchemy.orm import Session

from app.models.external_agent import ExternalAgent
from app.models.integration_credential import IntegrationCredential
from app.services.orchestration.credential_vault import retrieve_credential

logger = logging.getLogger(__name__)


class ExternalAgentAdapter:
    def dispatch(self, agent: ExternalAgent, task: str, context: dict, db: Session) -> str:
        """Route task to external agent based on protocol."""
        if agent.protocol == "openai_chat":
            return self._dispatch_openai_chat(agent, task, context, db)
        elif agent.protocol == "mcp_sse":
            return "MCP SSE dispatch not yet implemented for external agents"
        elif agent.protocol == "webhook":
            return self._dispatch_webhook(agent, task, context, db)
        elif agent.protocol == "a2a":
            return "A2A dispatch not yet implemented for external agent adapter"
        elif agent.protocol == "copilot_extension":
            return "Copilot Extension dispatch not yet implemented"
        else:
            raise RuntimeError(f"Unknown protocol: {agent.protocol}")

    def _dispatch_openai_chat(self, agent: ExternalAgent, task: str, context: dict, db: Session) -> str:
        messages = []
        if context:
            messages.append({"role": "system", "content": str(context)})
        messages.append({"role": "user", "content": task})

        token = self._get_credential(agent, db)
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        body = {
            "model": agent.metadata_.get("model", "gpt-4"),
            "messages": messages,
        }

        try:
            resp = httpx.post(
                f"{agent.endpoint_url.rstrip('/')}/v1/chat/completions",
                json=body,
                headers=headers,
                timeout=agent.metadata_.get("timeout", 30),
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except httpx.HTTPStatusError as e:
            raise RuntimeError(f"openai_chat request failed with status {e.response.status_code}") from e
        except Exception as e:
            raise RuntimeError(f"openai_chat request failed: {e}") from e

    def _dispatch_webhook(self, agent: ExternalAgent, task: str, context: dict, db: Session) -> str:
        payload = {"task": task, "context": context, "callback_url": None}
        body_json = json.dumps(payload)

        headers = {"Content-Type": "application/json"}
        if agent.auth_type == "hmac":
            secret = self._get_credential(agent, db)
            sig = hmac.new(secret.encode(), body_json.encode(), hashlib.sha256).hexdigest()
            headers["X-Signature"] = f"hmac-sha256={sig}"
        else:
            token = self._get_credential(agent, db)
            headers["Authorization"] = f"Bearer {token}"

        try:
            resp = httpx.post(
                f"{agent.endpoint_url.rstrip('/')}/tasks",
                content=body_json,
                headers=headers,
                timeout=agent.metadata_.get("timeout", 30),
            )
            if resp.status_code == 200:
                return str(resp.json())
            raise RuntimeError(f"webhook request failed with status {resp.status_code}")
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"webhook request failed: {e}") from e

    def _get_credential(self, agent: ExternalAgent, db: Session) -> str:
        if agent.credential_id is None:
            return ""
        try:
            plaintext = retrieve_credential(db, agent.credential_id, agent.tenant_id)
            return plaintext or ""
        except Exception as e:
            logger.warning("Could not load credential %s for agent %s: %s", agent.credential_id, agent.id, e)
            return ""


adapter = ExternalAgentAdapter()
