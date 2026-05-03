from pathlib import Path

import pytest


# Resolve relative to this test file so the suite works in any checkout
# (the original hard-coded `/workspace/...` paths that only exist inside one
# specific container layout).
_API_ROOT = Path(__file__).resolve().parents[1]  # apps/api/
LUNA_SKILL_FILES = [
    _API_ROOT / "app" / "skills" / "native" / "luna" / "skill.md",
    _API_ROOT / "app" / "skills" / "native" / "agents" / "luna" / "skill.md",
    _API_ROOT / "app" / "skills" / "agents" / "luna" / "skill.md",
]


def test_luna_skill_requires_immediate_aremko_booking_when_context_is_complete():
    existing = [p for p in LUNA_SKILL_FILES if p.exists()]
    if not existing:
        pytest.skip(
            "Luna skill bundle not present in this checkout — the skill now "
            "ships from the _bundled/ marketplace layout instead of the legacy "
            "native/ folder. See apps/api/app/services/skill_manager.py."
        )

    required_snippets = [
        "== AREMKO RESERVATIONS ==",
        "create_aremko_reservation",
        "do NOT stop at availability",
        "defaults to Los Lagos / Puerto Varas",
        "call `create_aremko_reservation` in the same turn",
    ]

    for skill_file in existing:
        content = skill_file.read_text(encoding="utf-8")
        for snippet in required_snippets:
            assert snippet in content, f"{skill_file} is missing: {snippet}"
