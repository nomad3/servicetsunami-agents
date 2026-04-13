import os
os.environ["TESTING"] = "True"

import uuid
from app.schemas.blackboard import BlackboardCreate, BlackboardInDB


def test_blackboard_create_accepts_chat_session_id():
    board = BlackboardCreate(title="Test board", chat_session_id=uuid.uuid4())
    assert board.chat_session_id is not None


def test_blackboard_create_chat_session_id_optional():
    board = BlackboardCreate(title="Test board")
    assert board.chat_session_id is None


def test_blackboard_in_db_has_chat_session_id():
    assert "chat_session_id" in BlackboardInDB.model_fields
