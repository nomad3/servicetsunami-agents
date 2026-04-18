import json
import logging
import uuid
from typing import Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.agent import Agent

try:
    import redis as redis_lib
    _REDIS_AVAILABLE = True
except ImportError:
    _REDIS_AVAILABLE = False

logger = logging.getLogger(__name__)

_HEARTBEAT_TTL = 90
_ADVERTISE_TTL = 300


class AgentRegistry:
    def __init__(self):
        self._redis = None

    def _get_redis(self):
        if not _REDIS_AVAILABLE:
            return None
        if self._redis is None:
            try:
                self._redis = redis_lib.from_url(settings.REDIS_URL)
            except Exception as exc:
                logger.warning("AgentRegistry: Redis connect failed: %s", exc)
                return None
        return self._redis

    def find_by_capability(self, capability: str, tenant_id, db: Session, max_error_rate: float = 0.1) -> list:
        agents = (
            db.query(Agent)
            .filter(Agent.status == "production", Agent.tenant_id == tenant_id)
            .all()
        )
        return [a for a in agents if isinstance(a.capabilities, list) and capability in a.capabilities]

    def find_available(self, tenant_id, db: Session) -> list:
        r = self._get_redis()
        if r is not None:
            try:
                keys = r.keys("agent:available:*")
                if keys:
                    agent_ids = []
                    for key in keys:
                        key_str = key.decode() if isinstance(key, bytes) else key
                        parts = key_str.split("agent:available:")
                        if len(parts) == 2 and parts[1]:
                            try:
                                agent_ids.append(uuid.UUID(parts[1]))
                            except ValueError:
                                pass
                    if agent_ids:
                        return (
                            db.query(Agent)
                            .filter(
                                Agent.id.in_(agent_ids),
                                Agent.tenant_id == tenant_id,
                                Agent.status == "production",
                            )
                            .all()
                        )
            except Exception as exc:
                logger.warning("AgentRegistry.find_available Redis error: %s", exc)

        return (
            db.query(Agent)
            .filter(Agent.status == "production", Agent.tenant_id == tenant_id)
            .all()
        )

    def advertise(self, agent_id, capabilities: list, db: Session) -> None:
        r = self._get_redis()
        if r is None:
            return
        try:
            r.set(f"agent:caps:{agent_id}", json.dumps(capabilities), ex=_ADVERTISE_TTL)
        except Exception as exc:
            logger.warning("AgentRegistry.advertise failed for agent %s: %s", agent_id, exc)

    def is_available(self, agent_id) -> bool:
        r = self._get_redis()
        if r is None:
            return True
        try:
            return bool(r.exists(f"agent:available:{agent_id}"))
        except Exception as exc:
            logger.warning("AgentRegistry.is_available failed for agent %s: %s", agent_id, exc)
            return True


registry = AgentRegistry()
