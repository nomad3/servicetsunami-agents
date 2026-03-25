"""Phase 6: Skill auto-creation from detected gaps.

Converts high-severity skill gaps into draft custom skills (markdown engine)
so they appear in the skill marketplace for review and testing.
"""

import logging
import uuid
from datetime import datetime

from temporalio import activity

logger = logging.getLogger(__name__)

# Skill stub templates keyed by gap_type
_SKILL_TEMPLATES = {
    "tool_missing": """\
---
name: {skill_name}
description: {description}
version: 1
engine: markdown
category: auto-generated
status: draft
---

# {skill_name}

**Auto-generated from skill gap detection on {date}**
Industry: {industry}

## Task

{description}

## Instructions

1. Analyze the user's request carefully
2. Use available tools to gather necessary information
3. Provide a clear, actionable response

## Notes

This skill was automatically generated because Luna detected repeated failures
of type `{gap_type}` in the `{industry}` industry context.
Review and refine this stub before promoting to production.
""",
    "knowledge_gap": """\
---
name: {skill_name}
description: {description}
version: 1
engine: markdown
category: auto-generated
status: draft
---

# {skill_name}

**Auto-generated from knowledge gap detection on {date}**
Industry: {industry}

## Task

{description}

## Instructions

1. Search the knowledge graph for relevant context: `search_knowledge(query=...)`
2. If no entity found, create one: `create_entity(...)`
3. Provide a response grounded in the retrieved knowledge

## Notes

This skill was automatically generated because Luna detected knowledge gaps
in the `{industry}` industry context. Populate the knowledge graph and
refine this stub before promoting to production.
""",
    "prompt_weakness": """\
---
name: {skill_name}
description: {description}
version: 1
engine: markdown
category: auto-generated
status: draft
---

# {skill_name}

**Auto-generated from prompt weakness detection on {date}**
Industry: {industry}

## Task

{description}

## System Prompt

You are Luna, a business co-pilot specializing in the {industry} domain.
Be concise, specific, and action-oriented. Avoid generic responses.

## Instructions

1. Understand the user's specific {industry} context
2. Apply domain knowledge to give targeted advice
3. Suggest concrete next steps

## Notes

This skill was automatically generated because Luna's responses for this
scenario type were consistently rated below threshold. Refine the prompt
before promoting to production.
""",
}

_DEFAULT_TEMPLATE = """\
---
name: {skill_name}
description: {description}
version: 1
engine: markdown
category: auto-generated
status: draft
---

# {skill_name}

**Auto-generated from skill gap detection on {date}**
Gap type: {gap_type} | Industry: {industry}

## Task

{description}

## Instructions

Review and implement this skill based on the detected gap.
Proposed fix: {proposed_fix}
"""


@activity.defn(name="auto_create_skill_stubs")
async def auto_create_skill_stubs(tenant_id: str) -> dict:
    """Create draft skill stubs from high/medium severity unresolved skill gaps."""
    from app.db.session import SessionLocal
    from app.models.simulation import SkillGap
    from sqlalchemy import text
    import re

    db = SessionLocal()
    try:
        tenant_uuid = uuid.UUID(tenant_id)
        today = datetime.utcnow().date()

        # Get unresolved high/medium gaps without existing draft skills
        gaps = (
            db.query(SkillGap)
            .filter(
                SkillGap.tenant_id == tenant_uuid,
                SkillGap.status.in_(["detected", "acknowledged"]),
                SkillGap.severity.in_(["high", "medium"]),
            )
            .order_by(SkillGap.frequency.desc())
            .limit(10)
            .all()
        )

        created = 0
        skipped = 0

        for gap in gaps:
            skill_name = _gap_to_skill_name(gap)

            # Check if a draft skill for this gap already exists
            existing = db.execute(text("""
                SELECT id FROM skills
                WHERE tenant_id = CAST(:tid AS uuid)
                  AND name = :name
                  AND status = 'draft'
                LIMIT 1
            """), {"tid": tenant_id, "name": skill_name}).fetchone()

            if existing:
                skipped += 1
                continue

            # Build prompt.md content
            template = _SKILL_TEMPLATES.get(gap.gap_type or "tool_missing", _DEFAULT_TEMPLATE)
            prompt_content = template.format(
                skill_name=skill_name,
                description=gap.description or f"Handle {gap.gap_type} in {gap.industry or 'general'} context",
                date=today.isoformat(),
                industry=gap.industry or "general",
                gap_type=gap.gap_type or "unknown",
                proposed_fix=gap.proposed_fix or "Review simulation failures and implement appropriate logic.",
            )

            # Insert draft skill
            skill_id = str(uuid.uuid4())
            db.execute(text("""
                INSERT INTO skills (
                    id, tenant_id, name, description, version, engine,
                    category, status, prompt_content, created_at, updated_at,
                    is_active, skill_type
                ) VALUES (
                    CAST(:id AS uuid),
                    CAST(:tid AS uuid),
                    :name,
                    :description,
                    1,
                    'markdown',
                    'auto-generated',
                    'draft',
                    :prompt_content,
                    NOW(),
                    NOW(),
                    FALSE,
                    'custom'
                )
                ON CONFLICT DO NOTHING
            """), {
                "id": skill_id,
                "tid": tenant_id,
                "name": skill_name,
                "description": gap.description[:500] if gap.description else "",
                "prompt_content": prompt_content,
            })

            # Move gap to in_progress
            gap.status = "in_progress"
            created += 1
            logger.info(
                "Created draft skill '%s' from gap %s (tenant %s)",
                skill_name, str(gap.id)[:8], tenant_id[:8],
            )

        db.commit()
        logger.info(
            "Skill stubs: %d created, %d skipped (tenant %s)",
            created, skipped, tenant_id[:8],
        )
        return {"stubs_created": created, "stubs_skipped": skipped}
    except Exception as e:
        logger.error("auto_create_skill_stubs failed for %s: %s", tenant_id[:8], e)
        raise
    finally:
        db.close()


def _gap_to_skill_name(gap) -> str:
    """Convert a skill gap into a readable, unique skill name."""
    import re
    parts = []
    if gap.industry:
        parts.append(gap.industry.replace("_", " ").title())
    if gap.gap_type:
        type_label = {
            "tool_missing": "Tool Handler",
            "knowledge_gap": "Knowledge Lookup",
            "prompt_weakness": "Response Strategy",
        }.get(gap.gap_type, gap.gap_type.replace("_", " ").title())
        parts.append(type_label)
    if not parts:
        parts.append("Auto Skill")
    name = " — ".join(parts)
    # Append a short gap ID suffix to ensure uniqueness
    name += f" ({str(gap.id)[:6]})"
    return name
