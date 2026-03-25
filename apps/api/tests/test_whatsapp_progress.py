"""Tests for WhatsApp progress message behavior."""
import os
import sys
from pathlib import Path
from types import ModuleType
from sqlalchemy.types import UserDefinedType

os.environ["TESTING"] = "True"
sys.path.append(str(Path(__file__).resolve().parents[1]))

if "pgvector.sqlalchemy" not in sys.modules:
    pgvector_module = ModuleType("pgvector")
    pgvector_sqlalchemy = ModuleType("pgvector.sqlalchemy")
    class _FakeVector(UserDefinedType):
        def __init__(self, *a, **kw): pass
        def get_col_spec(self, **kw): return "VECTOR"
    pgvector_sqlalchemy.Vector = _FakeVector
    pgvector_module.sqlalchemy = pgvector_sqlalchemy
    sys.modules["pgvector"] = pgvector_module
    sys.modules["pgvector.sqlalchemy"] = pgvector_sqlalchemy


class TestProgressHelpers:
    def test_ack_message_for_general(self):
        from app.services.whatsapp_service import _build_ack_message
        ack = _build_ack_message("hello", "general")
        assert ack == "On it — thinking..."
        assert len(ack) < 100

    def test_ack_message_for_code(self):
        from app.services.whatsapp_service import _build_ack_message
        ack = _build_ack_message("review PR", "code")
        assert "code" in ack.lower() or "analyzing" in ack.lower()

    def test_ack_messages_all_short(self):
        from app.services.whatsapp_service import _build_ack_message
        for t in ["code", "research", "email", "calendar", "sales", "data", "general"]:
            assert len(_build_ack_message("test", t)) < 100

    def test_progress_messages_rotate(self):
        from app.services.whatsapp_service import _get_progress_message
        msgs = [_get_progress_message(i) for i in range(5)]
        assert len(set(msgs)) >= 3

    def test_progress_messages_all_short(self):
        from app.services.whatsapp_service import _get_progress_message
        for i in range(20):
            assert len(_get_progress_message(i)) < 100

    def test_completion_summary_for_long_response(self):
        from app.services.whatsapp_service import _build_completion_summary
        summary = _build_completion_summary("x" * 500, elapsed_seconds=120)
        assert summary is not None
        assert "done" in summary.lower()
        assert len(summary) < 150

    def test_no_summary_for_quick_response(self):
        from app.services.whatsapp_service import _build_completion_summary
        assert _build_completion_summary("short", elapsed_seconds=5) is None

    def test_no_summary_for_short_text(self):
        from app.services.whatsapp_service import _build_completion_summary
        assert _build_completion_summary("short", elapsed_seconds=60) is None
