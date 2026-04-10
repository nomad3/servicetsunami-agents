import os
import sys
import uuid
from sqlalchemy.orm import Session
from sqlalchemy import text

# Add apps/api to path
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from app.db.session import SessionLocal
from app.services import embedding_service
from app.memory import _query

def main():
    tenant_id = uuid.UUID("0f134606-3906-44a5-9e88-6c2020f0f776")
    query = "Who am I? Check your memory about my role and recent commitments"
    
    db = SessionLocal()
    try:
        print(f"Query: {query}")
        emb = embedding_service.embed_text(query, task_type="RETRIEVAL_QUERY")
        
        print("\n--- TOP ENTITIES ---")
        entities = _query.search_entities(db, tenant_id, emb, top_k=10, agent_slug="luna")
        for e in entities:
            print(f"- {e.name} (sim={e.similarity:.3f}, type={e.source_type})")
            
        print("\n--- TOP OBSERVATIONS ---")
        entity_ids = [e.id for e in entities]
        observations = _query.search_observations(db, tenant_id, entity_ids, emb, top_k=10)
        for o in observations:
            print(f"- {o.content[:100]} (sim={o.similarity:.3f})")
            
        print("\n--- TOP EPISODES ---")
        episodes = _query.search_episodes(db, tenant_id, emb, top_k=5)
        for ep in episodes:
            print(f"- {ep.summary[:100]} (sim={ep.similarity:.3f})")

    finally:
        db.close()

if __name__ == "__main__":
    main()
