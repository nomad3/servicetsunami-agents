import os
os.environ["TESTING"] = "True"

import pytest
from app.schemas.blackboard import EntryType, AuthorRole


def test_triage_phase_uses_evidence_entry_type():
    """advance_phase with triage phase should create an EVIDENCE entry."""
    from app.services.collaboration_service import _phase_entry_type, _phase_author_role
    assert _phase_entry_type("triage") == EntryType.EVIDENCE
    assert _phase_entry_type("investigate") == EntryType.EVIDENCE
    assert _phase_entry_type("analyze") == EntryType.CRITIQUE
    assert _phase_entry_type("command") == EntryType.SYNTHESIS


def test_triage_phase_uses_correct_author_role():
    from app.services.collaboration_service import _phase_author_role
    assert _phase_author_role("triage") == AuthorRole.RESEARCHER
    assert _phase_author_role("investigate") == AuthorRole.RESEARCHER
    assert _phase_author_role("analyze") == AuthorRole.CRITIC
    assert _phase_author_role("command") == AuthorRole.SYNTHESIZER


def test_unknown_phase_raises_value_error():
    from app.services.collaboration_service import _phase_entry_type
    with pytest.raises(ValueError, match="Unknown collaboration phase"):
        _phase_entry_type("nonexistent_phase")
