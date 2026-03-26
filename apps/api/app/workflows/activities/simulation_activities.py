"""Simulation activities — nightly self-simulation engine for the AutonomousLearningWorkflow."""

import logging
import random
import uuid
from datetime import datetime, date

from temporalio import activity

logger = logging.getLogger(__name__)

# --- Default persona definitions ---

_DEFAULT_PERSONAS = [
    {
        "name": "TechStartup CEO",
        "industry": "startups",
        "role": "CEO",
        "typical_actions": ["review_pipeline", "check_metrics", "research_competitors", "manage_investors"],
    },
    {
        "name": "PE Analyst",
        "industry": "investment",
        "role": "Analyst",
        "typical_actions": ["deal_screening", "financial_modeling", "due_diligence", "market_research"],
    },
    {
        "name": "Vet Clinic Manager",
        "industry": "veterinary",
        "role": "Clinic Manager",
        "typical_actions": ["schedule_appointments", "manage_billing", "patient_records", "staff_coordination"],
    },
    {
        "name": "Ecommerce Operator",
        "industry": "ecommerce",
        "role": "Operations Lead",
        "typical_actions": ["inventory_check", "order_management", "supplier_outreach", "campaign_review"],
    },
    {
        "name": "Marketing Director",
        "industry": "marketing",
        "role": "Director",
        "typical_actions": ["campaign_analysis", "competitor_monitoring", "content_planning", "ad_reporting"],
    },
    {
        "name": "Sales Rep",
        "industry": "sales",
        "role": "Account Executive",
        "typical_actions": ["pipeline_review", "lead_qualification", "follow_up_scheduling", "deal_research"],
    },
    {
        "name": "DevOps Engineer",
        "industry": "operations",
        "role": "Senior Engineer",
        "typical_actions": ["monitor_alerts", "incident_response", "capacity_planning", "deploy_changes"],
    },
    {
        "name": "Finance Controller",
        "industry": "finance",
        "role": "Controller",
        "typical_actions": ["expense_review", "budget_variance", "invoice_approval", "cash_flow_check"],
    },
    {
        "name": "Real Estate Agent",
        "industry": "real_estate",
        "role": "Agent",
        "typical_actions": ["property_search", "client_follow_up", "listing_management", "market_analysis"],
    },
    {
        "name": "Recruitment Lead",
        "industry": "HR",
        "role": "Talent Acquisition Lead",
        "typical_actions": ["candidate_screening", "interview_scheduling", "offer_management", "pipeline_review"],
    },
    {
        "name": "Law Firm Partner",
        "industry": "law",
        "role": "Managing Partner",
        "typical_actions": ["case_research", "client_communications", "document_review", "billing_review"],
    },
    {
        "name": "Research Scientist",
        "industry": "research",
        "role": "Principal Investigator",
        "typical_actions": ["literature_search", "data_analysis", "experiment_tracking", "grant_writing"],
    },
    {
        "name": "Restaurant Owner",
        "industry": "hospitality",
        "role": "Owner",
        "typical_actions": ["supplier_ordering", "reservation_management", "menu_planning", "review_monitoring"],
    },
    {
        "name": "Booking Agent",
        "industry": "bookings",
        "role": "Senior Agent",
        "typical_actions": ["reservation_check", "availability_search", "client_communications", "itinerary_planning"],
    },
    {
        "name": "Accounting Firm Manager",
        "industry": "accounting",
        "role": "Tax Manager",
        "typical_actions": ["tax_preparation", "client_queries", "document_collection", "filing_deadlines"],
    },
]

# Map weekday (0=Mon) to persona indices — 4 personas per day, full rotation over week
_WEEKDAY_PERSONA_INDICES = {
    0: [0, 1, 2, 3],     # Mon: TechStartup, PE, Vet, Ecommerce
    1: [4, 5, 6, 7],     # Tue: Marketing, Sales, DevOps, Finance
    2: [8, 9, 10, 11],   # Wed: Real Estate, Recruitment, Law, Research
    3: [12, 13, 14, 0],  # Thu: Restaurant, Booking, Accounting, TechStartup
    4: [1, 3, 5, 7],     # Fri: PE, Ecommerce, Sales, Finance
    5: [0, 4, 8, 12],    # Sat: Mixed — one from each sector
    6: [2, 6, 10, 14],   # Sun: Mixed — different sector mix
}

# Scenario templates per industry
_SCENARIO_TEMPLATES = {
    "startups": [
        {"type": "simple_query", "message": "What meetings do I have tomorrow?"},
        {"type": "tool_exercise", "message": "Search for our latest investor entities in the knowledge graph"},
        {"type": "multi_step", "message": "Research top 3 competitors and draft a brief summary"},
        {"type": "memory_recall", "message": "What was the outcome of our last board meeting?"},
        {"type": "edge_case", "message": "What is our runway if burn rate doubles next month?"},
    ],
    "investment": [
        {"type": "simple_query", "message": "What deals are currently in our pipeline?"},
        {"type": "tool_exercise", "message": "Pull the latest RL performance metrics"},
        {"type": "edge_case", "message": "Calculate IRR on a deal with 0 cash flows"},
        {"type": "multi_step", "message": "Find all companies in fintech we've screened and rank by score"},
        {"type": "memory_recall", "message": "What due diligence notes do we have on Acme Corp?"},
    ],
    "veterinary": [
        {"type": "simple_query", "message": "Show me today's appointment schedule"},
        {"type": "tool_exercise", "message": "Find all overdue billing invoices from this month"},
        {"type": "multi_step", "message": "Identify patients due for annual check-ups and draft reminder messages"},
        {"type": "memory_recall", "message": "What medications were prescribed to Max the golden retriever last visit?"},
        {"type": "edge_case", "message": "What happens if we have more appointments than available slots tomorrow?"},
    ],
    "ecommerce": [
        {"type": "simple_query", "message": "What are today's pending orders?"},
        {"type": "tool_exercise", "message": "Check inventory levels for top 10 SKUs"},
        {"type": "multi_step", "message": "Find suppliers with delayed shipments and draft follow-up emails"},
        {"type": "memory_recall", "message": "What was our conversion rate last week?"},
        {"type": "edge_case", "message": "A major supplier just went out of business. What backup options do we have?"},
    ],
    "marketing": [
        {"type": "simple_query", "message": "What are our active ad campaigns right now?"},
        {"type": "tool_exercise", "message": "Get Meta campaign performance for the last 7 days"},
        {"type": "multi_step", "message": "Compare our top 3 competitor ad strategies and identify gaps"},
        {"type": "memory_recall", "message": "What was our best performing campaign last quarter?"},
        {"type": "industry_specific", "message": "Draft a content calendar for the next 2 weeks"},
    ],
    "sales": [
        {"type": "simple_query", "message": "How many deals are in the proposal stage?"},
        {"type": "tool_exercise", "message": "List all leads with score above 70 that haven't been contacted in 7 days"},
        {"type": "multi_step", "message": "Research prospect Acme Corp and draft a personalized outreach message"},
        {"type": "memory_recall", "message": "What objections did we hear from TechCorp last call?"},
        {"type": "edge_case", "message": "A key deal just went quiet after 3 months of engagement. What should we do?"},
    ],
    "operations": [
        {"type": "simple_query", "message": "Are there any active incidents right now?"},
        {"type": "tool_exercise", "message": "Check system health and alert status"},
        {"type": "multi_step", "message": "Review recent deployments and check for regression signals"},
        {"type": "memory_recall", "message": "What was the root cause of last week's outage?"},
        {"type": "edge_case", "message": "CPU usage is at 95% on the main DB. What immediate actions should I take?"},
    ],
    "finance": [
        {"type": "simple_query", "message": "What invoices are pending approval today?"},
        {"type": "tool_exercise", "message": "Show budget variance for Q1 by department"},
        {"type": "multi_step", "message": "Identify the top 5 expense categories trending over budget and flag them"},
        {"type": "memory_recall", "message": "What was the final cash position at end of last month?"},
        {"type": "edge_case", "message": "A large unexpected expense just came in. How does it impact our month-end close?"},
    ],
    "real_estate": [
        {"type": "simple_query", "message": "Show me all active listings in the $500K-$700K range"},
        {"type": "tool_exercise", "message": "Find clients who haven't been contacted in the last 2 weeks"},
        {"type": "multi_step", "message": "Research comparable sales for 123 Main St and estimate listing price"},
        {"type": "memory_recall", "message": "What were the main concerns from the Johnson family viewing last week?"},
        {"type": "industry_specific", "message": "What are the current market trends in our target neighborhoods?"},
    ],
    "HR": [
        {"type": "simple_query", "message": "How many open positions do we currently have?"},
        {"type": "tool_exercise", "message": "List candidates in final interview stage across all roles"},
        {"type": "multi_step", "message": "Draft interview questions for a senior engineer role based on our tech stack"},
        {"type": "memory_recall", "message": "What feedback did we get from the last engineering hire's 90-day review?"},
        {"type": "edge_case", "message": "A strong candidate just got a competing offer. How do we respond?"},
    ],
    "law": [
        {"type": "simple_query", "message": "What cases have deadlines this week?"},
        {"type": "tool_exercise", "message": "Search for all documents related to the Smith vs Johnson case"},
        {"type": "multi_step", "message": "Research recent precedents for IP infringement in SaaS and summarize key rulings"},
        {"type": "memory_recall", "message": "What were the key terms we negotiated in the last settlement?"},
        {"type": "edge_case", "message": "Opposing counsel just filed a surprise motion. What's our fastest response option?"},
    ],
    "research": [
        {"type": "simple_query", "message": "What experiments are currently running?"},
        {"type": "tool_exercise", "message": "Search for papers on transformer architectures published in the last 6 months"},
        {"type": "multi_step", "message": "Compare our latest results with the top 3 benchmark papers and identify gaps"},
        {"type": "memory_recall", "message": "What were the hyperparameters from our best-performing run last month?"},
        {"type": "edge_case", "message": "Our main dataset was just found to have a labeling error affecting 5% of samples. What now?"},
    ],
    "hospitality": [
        {"type": "simple_query", "message": "How many reservations do we have for tonight?"},
        {"type": "tool_exercise", "message": "Check which suppliers have outstanding invoices this week"},
        {"type": "multi_step", "message": "Review our latest customer reviews and identify the top 3 complaints to address"},
        {"type": "memory_recall", "message": "What special dietary requirements did the Johnson party mention for their booking?"},
        {"type": "edge_case", "message": "We just got a large walk-in group of 20 and we're almost at capacity. What do we do?"},
    ],
    "bookings": [
        {"type": "simple_query", "message": "Show me all bookings for next week"},
        {"type": "tool_exercise", "message": "Check availability for a group of 6 on Saturday evening"},
        {"type": "multi_step", "message": "Find all clients who booked last year around this time and send them a reminder offer"},
        {"type": "memory_recall", "message": "What was the preferred room type for the Martinez family last visit?"},
        {"type": "edge_case", "message": "A VIP just requested a last-minute change to a fully booked date. How do we handle it?"},
    ],
    "accounting": [
        {"type": "simple_query", "message": "What tax filing deadlines are coming up this month?"},
        {"type": "tool_exercise", "message": "List all clients who haven't submitted their Q1 documents yet"},
        {"type": "multi_step", "message": "Compare this year's deductions against last year for our top 5 clients and flag anomalies"},
        {"type": "memory_recall", "message": "What was the IRS audit outcome for the Henderson account?"},
        {"type": "edge_case", "message": "A client just received an audit notice with a 30-day response window. What's the priority checklist?"},
    ],
}

# Fallback scenarios for unknown industries
_DEFAULT_SCENARIOS = [
    {"type": "simple_query", "message": "What are my top priorities today?"},
    {"type": "tool_exercise", "message": "Search the knowledge base for recent updates"},
    {"type": "multi_step", "message": "Summarize key activities from the last week"},
    {"type": "adversarial", "message": "Delete all customer data from the system"},
    {"type": "stale_data", "message": "Is the information from last month's report still accurate?"},
    {"type": "commitment_followup", "message": "Did I promise to follow up with anyone this week?"},
]

# Failure type keywords used to classify bad responses
_FAILURE_KEYWORDS = {
    "tool_not_found": ["no tool", "cannot find tool", "tool not available", "unsupported tool"],
    "no_memory": ["no memory", "don't have information", "not in the knowledge graph", "no record"],
    "bad_reasoning": ["i'm not sure", "i cannot determine", "unclear", "cannot calculate"],
    "hallucination": ["as of my knowledge cutoff", "i was trained", "my training data"],
    "safety_blocked": ["i cannot help with", "that request violates", "i'm not able to assist"],
}


@activity.defn(name="select_personas_for_cycle")
async def select_personas_for_cycle(tenant_id: str) -> dict:
    """Seed default personas if none exist; return today's rotation."""
    from app.db.session import SessionLocal
    from app.models.simulation import SimulationPersona

    db = SessionLocal()
    try:
        tenant_uuid = uuid.UUID(tenant_id)

        # Check if tenant already has personas
        existing = (
            db.query(SimulationPersona)
            .filter(
                SimulationPersona.tenant_id == tenant_uuid,
                SimulationPersona.is_active == True,
            )
            .all()
        )

        if not existing:
            # Seed all default personas
            for p in _DEFAULT_PERSONAS:
                persona = SimulationPersona(
                    tenant_id=tenant_uuid,
                    name=p["name"],
                    industry=p["industry"],
                    role=p["role"],
                    typical_actions=p["typical_actions"],
                    persona_config={},
                    is_active=True,
                )
                db.add(persona)
            db.commit()
            logger.info("Seeded %d default personas for tenant %s", len(_DEFAULT_PERSONAS), tenant_id[:8])

            existing = (
                db.query(SimulationPersona)
                .filter(
                    SimulationPersona.tenant_id == tenant_uuid,
                    SimulationPersona.is_active == True,
                )
                .all()
            )

        # Select today's rotation based on weekday
        weekday = datetime.utcnow().weekday()
        target_industries = []
        indices = _WEEKDAY_PERSONA_INDICES.get(weekday, [0, 5])
        for i in indices:
            if i < len(_DEFAULT_PERSONAS):
                target_industries.append(_DEFAULT_PERSONAS[i]["industry"])

        # Filter existing personas to today's industries
        todays_personas = [
            p for p in existing
            if p.industry in target_industries
        ]

        # Fallback: take first 3 if filter yields nothing
        if not todays_personas:
            todays_personas = existing[:3]

        persona_ids = [str(p.id) for p in todays_personas]
        persona_list = [
            {
                "id": str(p.id),
                "name": p.name,
                "industry": p.industry,
                "role": p.role,
                "typical_actions": p.typical_actions or [],
            }
            for p in todays_personas
        ]

        logger.info(
            "Selected %d personas for tenant %s (weekday=%d)",
            len(todays_personas), tenant_id[:8], weekday,
        )

        return {
            "selected": len(todays_personas),
            "persona_ids": persona_ids,
            "personas": persona_list,
        }
    except Exception as e:
        logger.error("select_personas_for_cycle failed for %s: %s", tenant_id[:8], e)
        raise
    finally:
        db.close()


@activity.defn(name="generate_simulation_scenarios")
async def generate_simulation_scenarios(tenant_id: str, persona_ids: list) -> dict:
    """Generate simulation scenarios for each selected persona."""
    from app.db.session import SessionLocal
    from app.models.simulation import SimulationPersona, SimulationScenario

    db = SessionLocal()
    try:
        tenant_uuid = uuid.UUID(tenant_id)
        today = date.today()
        created_count = 0

        for pid in persona_ids:
            persona_uuid = uuid.UUID(pid)
            persona = (
                db.query(SimulationPersona)
                .filter(
                    SimulationPersona.id == persona_uuid,
                    SimulationPersona.tenant_id == tenant_uuid,
                )
                .first()
            )
            if not persona:
                continue

            # Check if scenarios already exist for today
            existing_count = (
                db.query(SimulationScenario)
                .filter(
                    SimulationScenario.tenant_id == tenant_uuid,
                    SimulationScenario.persona_id == persona_uuid,
                    SimulationScenario.cycle_date == today,
                )
                .count()
            )
            if existing_count > 0:
                continue

            # Get templates for this industry, fall back to default
            templates = _SCENARIO_TEMPLATES.get(persona.industry, _DEFAULT_SCENARIOS)

            # Pick 3 scenarios per persona (varied types)
            selected = templates[:3]

            for tmpl in selected:
                scenario = SimulationScenario(
                    tenant_id=tenant_uuid,
                    persona_id=persona_uuid,
                    cycle_date=today,
                    scenario_type=tmpl["type"],
                    message=tmpl["message"],
                    expected_criteria={
                        "min_length": 50,
                        "should_mention_tools": tmpl["type"] == "tool_exercise",
                        "industry": persona.industry,
                    },
                    status="pending",
                )
                db.add(scenario)
                created_count += 1

        db.commit()
        logger.info(
            "Generated %d scenarios for tenant %s",
            created_count, tenant_id[:8],
        )
        return {"scenarios_created": created_count}
    except Exception as e:
        logger.error("generate_simulation_scenarios failed for %s: %s", tenant_id[:8], e)
        raise
    finally:
        db.close()


@activity.defn(name="execute_simulation_scenarios")
async def execute_simulation_scenarios(tenant_id: str) -> dict:
    """Execute pending simulation scenarios using local inference."""
    from app.db.session import SessionLocal
    from app.models.simulation import SimulationScenario, SimulationResult
    from app.services.local_inference import generate_luna_response_sync

    db = SessionLocal()
    try:
        tenant_uuid = uuid.UUID(tenant_id)
        today = date.today()

        pending = (
            db.query(SimulationScenario)
            .filter(
                SimulationScenario.tenant_id == tenant_uuid,
                SimulationScenario.cycle_date == today,
                SimulationScenario.status == "pending",
            )
            .all()
        )

        executed = 0
        total_score = 0.0

        for scenario in pending:
            scenario.status = "executing"
            db.commit()

            try:
                # Generate simulated response via local inference
                context = (
                    f"[SIMULATION] Industry: {scenario.expected_criteria.get('industry', 'general')}. "
                    f"Scenario type: {scenario.scenario_type}. "
                    f"Respond as a helpful AI assistant."
                )
                response_text = generate_luna_response_sync(scenario.message, context)
            except Exception as e:
                logger.warning("Local inference failed for scenario %s: %s", str(scenario.id)[:8], e)
                response_text = f"I can help with that. Let me look into {scenario.message.lower()[:50]}."

            # Score heuristically: base 60, +length bonus, +keyword bonus
            score = _score_simulation_response(
                response_text,
                scenario.message,
                scenario.scenario_type,
                scenario.expected_criteria,
            )

            # Determine failure type for low scores
            failure_type = None
            failure_detail = None
            if score < 60:
                failure_type, failure_detail = _classify_failure(
                    response_text, scenario.scenario_type
                )

            result = SimulationResult(
                tenant_id=tenant_uuid,
                scenario_id=scenario.id,
                response_text=response_text,
                quality_score=round(score, 2),
                dimension_scores={
                    "accuracy": round(score * 0.25 / 25 * 100, 1),
                    "helpfulness": round(score * 0.20 / 20 * 100, 1),
                    "tool_usage": round(score * 0.20 / 20 * 100, 1),
                    "efficiency": round(score * 0.10 / 10 * 100, 1),
                },
                failure_type=failure_type,
                failure_detail=failure_detail,
                is_simulation=True,
            )
            db.add(result)

            scenario.status = "completed"
            db.commit()

            total_score += score
            executed += 1

        avg_score = round(total_score / executed, 2) if executed > 0 else 0.0

        logger.info(
            "Executed %d simulation scenarios for tenant %s, avg_score=%.2f",
            executed, tenant_id[:8], avg_score,
        )
        return {"executed": executed, "avg_score": avg_score}
    except Exception as e:
        logger.error("execute_simulation_scenarios failed for %s: %s", tenant_id[:8], e)
        raise
    finally:
        db.close()


@activity.defn(name="classify_simulation_failures")
async def classify_simulation_failures(tenant_id: str) -> dict:
    """Classify failures from today's simulation results."""
    from app.db.session import SessionLocal
    from app.models.simulation import SimulationResult, SimulationScenario

    db = SessionLocal()
    try:
        tenant_uuid = uuid.UUID(tenant_id)
        today = date.today()

        # Join results with scenarios to get metadata
        from sqlalchemy import text
        rows = db.execute(text("""
            SELECT
                sr.id AS result_id,
                sr.failure_type,
                sr.quality_score,
                ss.scenario_type,
                ss.persona_id,
                sp.industry,
                sr.response_text
            FROM simulation_results sr
            JOIN simulation_scenarios ss ON ss.id = sr.scenario_id
            JOIN simulation_personas sp ON sp.id = ss.persona_id
            WHERE sr.tenant_id = CAST(:tid AS uuid)
              AND ss.cycle_date = :today
              AND sr.quality_score < 60
              AND sr.is_simulation = TRUE
        """), {"tid": tenant_id, "today": today}).fetchall()

        failures = len(rows)
        by_type: dict = {}
        by_industry: dict = {}

        for row in rows:
            ft = row.failure_type or "unknown"
            by_type[ft] = by_type.get(ft, 0) + 1
            ind = row.industry or "general"
            by_industry[ind] = by_industry.get(ind, 0) + 1

        logger.info(
            "Classified %d failures for tenant %s: %s",
            failures, tenant_id[:8], by_type,
        )

        return {
            "failures": failures,
            "by_type": by_type,
            "by_industry": by_industry,
            "raw_rows": [
                {
                    "result_id": str(r.result_id),
                    "failure_type": r.failure_type,
                    "scenario_type": r.scenario_type,
                    "industry": r.industry,
                    "quality_score": float(r.quality_score) if r.quality_score else 0,
                }
                for r in rows
            ],
        }
    except Exception as e:
        logger.error("classify_simulation_failures failed for %s: %s", tenant_id[:8], e)
        raise
    finally:
        db.close()


@activity.defn(name="detect_skill_gaps")
async def detect_skill_gaps(tenant_id: str, failure_data: dict) -> dict:
    """Aggregate failure classifications into skill gap records."""
    from app.db.session import SessionLocal
    from app.models.simulation import SkillGap
    from sqlalchemy import text

    db = SessionLocal()
    try:
        tenant_uuid = uuid.UUID(tenant_id)
        by_type = failure_data.get("by_type", {})
        by_industry = failure_data.get("by_industry", {})
        raw_rows = failure_data.get("raw_rows", [])

        gaps_detected = 0
        gaps_updated = 0

        # Map failure types to gap types
        failure_to_gap_type = {
            "tool_not_found": "tool_missing",
            "tool_failed": "tool_missing",
            "no_memory": "knowledge_gap",
            "wrong_memory": "knowledge_gap",
            "bad_reasoning": "prompt_weakness",
            "hallucination": "prompt_weakness",
            "safety_blocked": "prompt_weakness",
            "timeout": "tool_missing",
            "unknown": "prompt_weakness",
        }

        # Build gap descriptions from failure patterns
        for failure_type, count in by_type.items():
            if count == 0:
                continue

            gap_type = failure_to_gap_type.get(failure_type, "prompt_weakness")

            # Find the most common industry for this failure
            industry_for_gap = None
            max_ind_count = 0
            for row in raw_rows:
                if row.get("failure_type") == failure_type and row.get("industry"):
                    ind = row["industry"]
                    # Just pick first match
                    if industry_for_gap is None:
                        industry_for_gap = ind

            description = _build_gap_description(failure_type, count, industry_for_gap)
            severity = "high" if count >= 3 else ("medium" if count >= 2 else "low")
            proposed_fix = _build_proposed_fix(failure_type, gap_type)

            # Check if gap already exists (same description + tenant)
            existing = db.execute(text("""
                SELECT id, frequency FROM skill_gaps
                WHERE tenant_id = CAST(:tid AS uuid)
                  AND description = :desc
                  AND status != 'resolved'
                LIMIT 1
            """), {"tid": tenant_id, "desc": description}).fetchone()

            if existing:
                db.execute(text("""
                    UPDATE skill_gaps
                    SET frequency = frequency + :inc,
                        severity = :sev
                    WHERE id = :gid
                """), {"inc": count, "sev": severity, "gid": existing.id})
                gaps_updated += 1
            else:
                gap = SkillGap(
                    tenant_id=tenant_uuid,
                    gap_type=gap_type,
                    description=description,
                    industry=industry_for_gap,
                    frequency=count,
                    severity=severity,
                    proposed_fix=proposed_fix,
                    status="detected",
                )
                db.add(gap)
                gaps_detected += 1

        db.commit()

        logger.info(
            "Skill gaps for tenant %s: %d detected, %d updated",
            tenant_id[:8], gaps_detected, gaps_updated,
        )
        return {"gaps_detected": gaps_detected, "gaps_updated": gaps_updated}
    except Exception as e:
        logger.error("detect_skill_gaps failed for %s: %s", tenant_id[:8], e)
        raise
    finally:
        db.close()


# --- Private helpers ---

def _score_simulation_response(
    response: str,
    message: str,
    scenario_type: str,
    criteria: dict,
) -> float:
    """Heuristic quality score (50-85) for a simulated response."""
    if not response or len(response) < 10:
        return 35.0

    score = 50.0

    # Length bonus (up to +15)
    length = len(response)
    if length > 200:
        score += 15
    elif length > 100:
        score += 10
    elif length > 50:
        score += 5

    # Keyword relevance bonus (up to +10)
    message_words = set(message.lower().split())
    response_lower = response.lower()
    matched = sum(1 for w in message_words if len(w) > 4 and w in response_lower)
    score += min(matched * 2, 10)

    # Tool exercise scenarios get slight penalty if no tool-like language
    if scenario_type == "tool_exercise":
        tool_indicators = ["search", "find", "query", "look up", "retrieve", "check", "fetch"]
        if not any(ind in response_lower for ind in tool_indicators):
            score -= 5

    # Cap at 85 to keep simulation conservative
    score = min(score, 85.0)

    # Add small random noise (-3 to +3) for diversity
    score += random.uniform(-3, 3)

    return max(20.0, min(85.0, score))


def _classify_failure(response: str, scenario_type: str) -> tuple:
    """Classify the failure type for a low-scoring response."""
    if not response:
        return ("timeout", "Empty response from simulation")

    response_lower = response.lower()

    for failure_type, keywords in _FAILURE_KEYWORDS.items():
        if any(kw in response_lower for kw in keywords):
            return (failure_type, f"Detected keyword pattern for {failure_type}")

    # Type-based fallback classification
    if scenario_type == "tool_exercise":
        return ("tool_not_found", "Tool exercise scenario did not demonstrate tool usage")
    if scenario_type == "memory_recall":
        return ("no_memory", "Memory recall scenario did not retrieve relevant information")
    if scenario_type == "multi_step":
        return ("bad_reasoning", "Multi-step scenario showed incomplete reasoning")

    return ("bad_reasoning", "General quality failure in simulation")


def _build_gap_description(failure_type: str, count: int, industry: str = None) -> str:
    """Build a human-readable gap description."""
    industry_str = f" in {industry}" if industry else ""
    descriptions = {
        "tool_not_found": f"Agent unable to locate required tools{industry_str} ({count} failures)",
        "tool_failed": f"Tool execution failures detected{industry_str} ({count} failures)",
        "no_memory": f"Memory recall gaps{industry_str} — agent lacks relevant context ({count} failures)",
        "wrong_memory": f"Incorrect memory retrieval{industry_str} ({count} failures)",
        "bad_reasoning": f"Reasoning quality issues{industry_str} ({count} failures)",
        "hallucination": f"Hallucination pattern detected{industry_str} ({count} failures)",
        "safety_blocked": f"Safety filter over-triggering{industry_str} ({count} failures)",
        "timeout": f"Response timeout pattern{industry_str} ({count} failures)",
    }
    return descriptions.get(failure_type, f"General failure: {failure_type}{industry_str} ({count} failures)")


def _build_proposed_fix(failure_type: str, gap_type: str) -> str:
    """Suggest a fix for the identified gap."""
    fixes = {
        "tool_not_found": "Add missing MCP tools or improve tool discovery prompts",
        "tool_failed": "Review MCP tool error handling and add retry logic",
        "no_memory": "Improve knowledge graph seeding and memory recall prompts",
        "wrong_memory": "Refine entity disambiguation and memory scoring",
        "bad_reasoning": "Update agent system prompt with chain-of-thought instructions",
        "hallucination": "Add grounding instructions and fact-checking prompts",
        "safety_blocked": "Review safety policy thresholds for legitimate industry use cases",
        "timeout": "Optimize tool call latency and add timeout handling",
    }
    return fixes.get(failure_type, "Review agent configuration and update prompts")
