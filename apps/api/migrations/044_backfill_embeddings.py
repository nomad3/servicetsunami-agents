"""One-time backfill: generate embeddings for all existing knowledge entities and memory activities."""
import json
import os
import sys

# Add app to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.db.session import SessionLocal
from app.models.knowledge_entity import KnowledgeEntity
from app.models.memory_activity import MemoryActivity
from app.services import embedding_service


def backfill():
    db = SessionLocal()
    try:
        # Backfill knowledge entities
        entities = db.query(KnowledgeEntity).all()
        print(f"Backfilling {len(entities)} knowledge entities...")
        for i, entity in enumerate(entities):
            text = f"{entity.name} {entity.category or ''} {entity.description or ''}"
            if entity.properties and isinstance(entity.properties, dict):
                text += f" {json.dumps(entity.properties)[:500]}"
            try:
                embedding_service.embed_and_store(
                    db, str(entity.tenant_id), "entity", str(entity.id), text.strip()
                )
            except Exception as e:
                print(f"  Failed to embed entity {entity.name}: {e}")
            if (i + 1) % 50 == 0:
                db.commit()
                print(f"  ...{i + 1}/{len(entities)}")
        db.commit()
        print(f"Done: {len(entities)} entities processed.")

        # Backfill memory activities
        activities = db.query(MemoryActivity).all()
        print(f"Backfilling {len(activities)} memory activities...")
        for i, activity in enumerate(activities):
            text = f"{activity.event_type}: {activity.description or ''}"
            if activity.event_metadata and isinstance(activity.event_metadata, dict):
                text += f" {json.dumps(activity.event_metadata)[:500]}"
            try:
                embedding_service.embed_and_store(
                    db, str(activity.tenant_id), "memory_activity", str(activity.id), text.strip()
                )
            except Exception as e:
                print(f"  Failed to embed activity {activity.id}: {e}")
            if (i + 1) % 50 == 0:
                db.commit()
                print(f"  ...{i + 1}/{len(activities)}")
        db.commit()
        print(f"Done: {len(activities)} activities processed.")

    finally:
        db.close()


if __name__ == "__main__":
    backfill()
