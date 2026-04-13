"""Redis pub/sub publisher for collaboration events.

Two channel types:
  session:{chat_session_id}      — session-level events (collaboration_started)
  collaboration:{collab_id}      — per-collaboration events (phase_started, blackboard_entry, ...)
"""

import json
import logging
import time
from typing import Generator, Optional

import redis

from app.core.config import settings

logger = logging.getLogger(__name__)

_pool: Optional[redis.ConnectionPool] = None


def _get_pool() -> redis.ConnectionPool:
    global _pool
    if _pool is None:
        _pool = redis.ConnectionPool.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            max_connections=20,
        )
    return _pool


def _get_redis() -> redis.Redis:
    return redis.Redis(connection_pool=_get_pool())


def publish_event(collaboration_id: str, event_type: str, payload: dict) -> None:
    """Publish a per-collaboration event to Redis pub/sub."""
    channel = f"collaboration:{collaboration_id}"
    message = json.dumps({
        "event_type": event_type,
        "payload": payload,
        "timestamp": time.time(),
    })
    try:
        r = _get_redis()
        r.publish(channel, message)
    except Exception as e:
        logger.warning("Redis publish failed (collaboration %s): %s", collaboration_id, e)


def publish_session_event(chat_session_id: str, event_type: str, payload: dict) -> None:
    """Publish a session-level event (e.g. collaboration_started) to Redis pub/sub."""
    channel = f"session:{chat_session_id}"
    message = json.dumps({
        "event_type": event_type,
        "payload": payload,
        "timestamp": time.time(),
    })
    try:
        r = _get_redis()
        r.publish(channel, message)
    except Exception as e:
        logger.warning("Redis publish failed (session %s): %s", chat_session_id, e)


def subscribe_collaboration(collaboration_id: str) -> Generator[str, None, None]:
    """SSE generator for collaboration events via Redis pub/sub.

    Yields Server-Sent Events strings. Reconnects on failure (up to 3 attempts).
    Closes when collaboration_completed event received.
    """
    channel = f"collaboration:{collaboration_id}"
    attempts = 0
    max_attempts = 3
    heartbeat_interval = 15  # seconds

    while attempts < max_attempts:
        try:
            r = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
            pubsub = r.pubsub()
            pubsub.subscribe(channel)
            last_heartbeat = time.time()

            for message in pubsub.listen():
                if message["type"] == "message":
                    attempts = 0  # reset on successful message
                    yield f"data: {message['data']}\n\n"

                    # Check if collaboration is done — close stream
                    try:
                        data = json.loads(message["data"])
                        if data.get("event_type") == "collaboration_completed":
                            pubsub.unsubscribe(channel)
                            return
                    except Exception:
                        pass

                # Heartbeat to keep connection alive through proxies
                if time.time() - last_heartbeat > heartbeat_interval:
                    yield ": heartbeat\n\n"
                    last_heartbeat = time.time()

        except Exception as e:
            attempts += 1
            logger.warning("Redis subscription error (attempt %d/%d): %s", attempts, max_attempts, e)
            if attempts < max_attempts:
                time.sleep(1)
            else:
                yield f"data: {json.dumps({'event_type': 'error', 'payload': {'detail': 'Stream connection lost'}})}\n\n"


def subscribe_session(chat_session_id: str) -> Generator[str, None, None]:
    """SSE generator for session-level events (collaboration_started, etc.)."""
    channel = f"session:{chat_session_id}"
    attempts = 0
    max_attempts = 3
    heartbeat_interval = 15

    while attempts < max_attempts:
        try:
            r = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
            pubsub = r.pubsub()
            pubsub.subscribe(channel)
            last_heartbeat = time.time()

            for message in pubsub.listen():
                if message["type"] == "message":
                    attempts = 0
                    yield f"data: {message['data']}\n\n"

                if time.time() - last_heartbeat > heartbeat_interval:
                    yield ": heartbeat\n\n"
                    last_heartbeat = time.time()

        except Exception as e:
            attempts += 1
            logger.warning("Redis session subscription error (attempt %d/%d): %s", attempts, max_attempts, e)
            if attempts < max_attempts:
                time.sleep(1)
            else:
                yield f"data: {json.dumps({'event_type': 'error', 'payload': {'detail': 'Stream connection lost'}})}\n\n"
