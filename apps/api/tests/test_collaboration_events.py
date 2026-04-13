import os
os.environ["TESTING"] = "True"

import json
from unittest.mock import MagicMock, patch


def test_publish_event_sends_to_correct_channel():
    mock_redis = MagicMock()
    with patch("app.services.collaboration_events._get_redis", return_value=mock_redis):
        from app.services.collaboration_events import publish_event
        publish_event("collab-123", "phase_started", {"phase": "triage"})
        mock_redis.publish.assert_called_once()
        channel, message = mock_redis.publish.call_args[0]
        assert channel == "collaboration:collab-123"
        data = json.loads(message)
        assert data["event_type"] == "phase_started"
        assert data["payload"]["phase"] == "triage"
        assert "timestamp" in data


def test_publish_session_event_sends_to_session_channel():
    mock_redis = MagicMock()
    with patch("app.services.collaboration_events._get_redis", return_value=mock_redis):
        from app.services.collaboration_events import publish_session_event
        publish_session_event("session-456", "collaboration_started", {"collaboration_id": "collab-123"})
        channel, message = mock_redis.publish.call_args[0]
        assert channel == "session:session-456"
