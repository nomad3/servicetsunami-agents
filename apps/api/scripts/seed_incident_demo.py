#!/usr/bin/env python3
"""Idempotent demo seed for A2A incident investigation (Levi's MDM scenario).

Run before demo day:
    cd apps/api && python scripts/seed_incident_demo.py
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/agentprovision")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.user import User
from app.models.agent import Agent
from app.models.knowledge_entity import KnowledgeEntity
from app.models.knowledge_observation import KnowledgeObservation
from app.models.knowledge_relation import KnowledgeRelation


AGENTS = [
    {
        "name": "Triage Agent",
        "role": "triage_agent",
        "description": "Incident triage specialist — classifies severity and scopes blast radius across Levi's MDM pipeline",
        "persona_prompt": "You are a triage specialist for Levi's MDM incidents. Classify severity, identify affected systems, and scope the blast radius.",
    },
    {
        "name": "Data Investigator",
        "role": "investigator",
        "description": "Investigates SAP PI/PO integration flows and Informatica MDM sync logs to find root causes",
        "persona_prompt": "You investigate Levi's data pipeline issues. Correlate SAP PI/PO integration events with Informatica MDM validation logs.",
    },
    {
        "name": "Root Cause Analyst",
        "role": "analyst",
        "description": "Validates hypotheses with quantitative evidence from Informatica MDM and SAP S4/MA",
        "persona_prompt": "You validate root cause hypotheses for Levi's MDM incidents using schema change logs and sync metrics.",
    },
    {
        "name": "Incident Commander",
        "role": "commander",
        "description": "Synthesizes investigation findings into a remediation plan for Levi's MDM operations team",
        "persona_prompt": "You produce actionable remediation plans for Levi's SRE/MDM operations team.",
    },
]

ENTITIES = [
    {
        "name": "SAP S4/MA",
        "entity_type": "data_source",
        "description": "ERP system of record for product master data and pricing",
    },
    {
        "name": "SAP PI/PO",
        "entity_type": "integration_layer",
        "description": "Integration bus routing data from ERP sources to Informatica MDM and downstream systems",
    },
    {
        "name": "Informatica MDM",
        "entity_type": "infrastructure",
        "description": "Central master data management hub. Validates, transforms, and routes product data to downstream systems",
    },
    {
        "name": "Andes B2C Hybris",
        "entity_type": "service",
        "description": "Customer-facing e-commerce platform. EMEA and APAC regions receive pricing from Informatica MDM via SAP PI/PO",
    },
    {
        "name": "GDO / APO",
        "entity_type": "service",
        "description": "NA region planning and distribution path. Uses separate sync from SAP HANA/APO — unaffected by EMEA/APAC sync failures",
    },
]

OBSERVATIONS = [
    ("Informatica MDM", "Schema migration applied 2026-04-06: NOT NULL column currency_precision added to product_master mapping table"),
    ("Informatica MDM", "340 SKUs missing currency_precision value — failing validation silently, excluded from sync output"),
    ("SAP PI/PO", "Partial syncs running daily since 2026-04-06 but treating excluded records as no-change — no error raised"),
    ("SAP PI/PO", "Last full successful sync was 2026-04-06 — 6 days of stale data in EMEA and APAC downstream"),
    ("Andes B2C Hybris", "1,247 SKUs have stale prices in EMEA and APAC regions — customer-facing prices last updated 2026-04-06"),
    ("GDO / APO", "NA region operates on separate SAP HANA/APO sync path — fully unaffected by the Informatica MDM issue"),
    ("SAP S4/MA", "Source data is correct — all 1,247 SKUs have valid currency_precision values in the ERP"),
]

# (source_name, relation_type, target_name)
RELATIONS = [
    ("SAP S4/MA", "feeds", "Informatica MDM"),
    ("Informatica MDM", "syncs_to", "Andes B2C Hybris"),
    ("SAP PI/PO", "routes_through", "Informatica MDM"),
]


def get_or_create_agent(db, tenant_id, data):
    existing = (
        db.query(Agent)
        .filter_by(name=data["name"], tenant_id=tenant_id)
        .first()
    )
    if existing:
        print(f"  [EXISTS]   Agent: {data['name']}")
        return existing
    agent = Agent(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        name=data["name"],
        role=data["role"],
        description=data["description"],
        persona_prompt=data["persona_prompt"],
        config={},
    )
    db.add(agent)
    db.flush()
    print(f"  [CREATED]  Agent: {data['name']}")
    return agent


def get_or_create_entity(db, tenant_id, data):
    existing = (
        db.query(KnowledgeEntity)
        .filter_by(name=data["name"], tenant_id=tenant_id)
        .filter(KnowledgeEntity.deleted_at.is_(None))
        .first()
    )
    if existing:
        print(f"  [EXISTS]   Entity: {data['name']}")
        return existing
    entity = KnowledgeEntity(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        name=data["name"],
        entity_type=data["entity_type"],
        description=data["description"],
        status="verified",
    )
    db.add(entity)
    db.flush()
    print(f"  [CREATED]  Entity: {data['name']}")
    return entity


def create_observation(db, tenant_id, entity, content):
    obs = KnowledgeObservation(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        entity_id=entity.id,
        observation_text=content,
        observation_type="fact",
        source_type="conversation",
        source_channel="system",
    )
    db.add(obs)
    db.flush()
    print(f"  [CREATED]  Observation on {entity.name}: {content[:70]}...")


def get_or_create_relation(db, tenant_id, src_entity, rel_type, tgt_entity):
    existing = (
        db.query(KnowledgeRelation)
        .filter_by(
            tenant_id=tenant_id,
            from_entity_id=src_entity.id,
            relation_type=rel_type,
            to_entity_id=tgt_entity.id,
        )
        .first()
    )
    if existing:
        print(f"  [EXISTS]   Relation: {src_entity.name} --{rel_type}--> {tgt_entity.name}")
        return existing
    relation = KnowledgeRelation(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        from_entity_id=src_entity.id,
        to_entity_id=tgt_entity.id,
        relation_type=rel_type,
        confidence_source="manual",
    )
    db.add(relation)
    db.flush()
    print(f"  [CREATED]  Relation: {src_entity.name} --{rel_type}--> {tgt_entity.name}")
    return relation


def main():
    engine = create_engine(os.environ["DATABASE_URL"])
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        user = db.query(User).filter_by(email="test@example.com").first()
        if not user:
            print("ERROR: demo tenant test@example.com not found. Run the API first to seed the tenant.")
            sys.exit(1)
        tenant_id = user.tenant_id
        print(f"Seeding for tenant: {tenant_id}\n")

        # --- Agents ---
        print("=== Agents ===")
        for agent_data in AGENTS:
            get_or_create_agent(db, tenant_id, agent_data)

        # --- Knowledge Entities ---
        print("\n=== Knowledge Entities ===")
        entity_map = {}
        for entity_data in ENTITIES:
            entity = get_or_create_entity(db, tenant_id, entity_data)
            entity_map[entity_data["name"]] = entity

        # --- Observations (always insert — append-only) ---
        print("\n=== Observations ===")
        for entity_name, content in OBSERVATIONS:
            entity = entity_map[entity_name]
            create_observation(db, tenant_id, entity, content)

        # --- Relations ---
        print("\n=== Relations ===")
        for src_name, rel_type, tgt_name in RELATIONS:
            get_or_create_relation(
                db, tenant_id, entity_map[src_name], rel_type, entity_map[tgt_name]
            )

        db.commit()
        print("\nDone. Levi's MDM incident demo data is ready.")

    except Exception as e:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
