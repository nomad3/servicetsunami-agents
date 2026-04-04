from app.services.rl_experience_service import DECISION_POINTS


def test_tier_selection_in_decision_points():
    """tier_selection must appear in the known decision points."""
    assert "tier_selection" in DECISION_POINTS
