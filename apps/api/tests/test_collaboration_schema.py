import os
os.environ["TESTING"] = "True"

from app.schemas.collaboration import (
    CollaborationPattern,
    CollaborationPhase,
    PATTERN_PHASES,
    PHASE_REQUIRED_ROLES,
)


def test_incident_investigation_pattern_exists():
    assert CollaborationPattern.INCIDENT_INVESTIGATION == "incident_investigation"


def test_incident_investigation_phases():
    phases = PATTERN_PHASES["incident_investigation"]
    assert phases == ["triage", "investigate", "analyze", "command"]


def test_incident_investigation_roles():
    assert PHASE_REQUIRED_ROLES["triage"] == ["triage_agent"]
    assert PHASE_REQUIRED_ROLES["investigate"] == ["investigator"]
    assert PHASE_REQUIRED_ROLES["analyze"] == ["analyst"]
    assert PHASE_REQUIRED_ROLES["command"] == ["commander"]


def test_new_phase_enums_exist():
    assert CollaborationPhase.TRIAGE == "triage"
    assert CollaborationPhase.INVESTIGATE == "investigate"
    assert CollaborationPhase.ANALYZE == "analyze"
    assert CollaborationPhase.COMMAND == "command"
