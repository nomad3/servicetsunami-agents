from pathlib import Path


LUNA_SKILL_FILES = [
    Path("/workspace/apps/api/app/skills/native/luna/skill.md"),
    Path("/workspace/apps/api/app/skills/native/agents/luna/skill.md"),
    Path("/workspace/apps/api/app/skills/agents/luna/skill.md"),
]


def test_luna_skill_requires_immediate_aremko_booking_when_context_is_complete():
    required_snippets = [
        "== AREMKO RESERVATIONS ==",
        "create_aremko_reservation",
        "do NOT stop at availability",
        "defaults to Los Lagos / Puerto Varas",
        "call `create_aremko_reservation` in the same turn",
    ]

    for skill_file in LUNA_SKILL_FILES:
        content = skill_file.read_text(encoding="utf-8")
        for snippet in required_snippets:
            assert snippet in content, f"{skill_file} is missing: {snippet}"
