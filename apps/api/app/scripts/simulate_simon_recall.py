import os
import sys
import uuid
import logging
from sqlalchemy.orm import Session

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Add apps/api to path
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from app.db.session import SessionLocal
from app.services.chat import post_user_message
from app.models.chat import ChatSession

def main():
    tenant_id = uuid.UUID("0f134606-3906-44a5-9e88-6c2020f0f776")
    user_id = uuid.UUID("d18d0af0-6ff9-4226-a7f8-d9adfa884649")
    # Get the latest session for Simon
    db = SessionLocal()
    try:
        from app.memory.feature_flag import is_v2_enabled
        print(f"DEBUG: is_v2_enabled({tenant_id}) = {is_v2_enabled(tenant_id)}")
        
        session = db.query(ChatSession).filter(ChatSession.tenant_id == tenant_id).order_by(ChatSession.created_at.desc()).first()
        if not session:
            print("Session not found")
            return

        print(f"Simulating user message for Simon (tenant {tenant_id}, session {session.id})...")
        user_msg, assistant_msg = post_user_message(
            db,
            session=session,
            user_id=user_id,
            content="And tell me all memory you have about me",
        )
        
        print("\n--- USER MESSAGE ---")
        print(user_msg.content)
        print("\n--- ASSISTANT RESPONSE ---")
        print(assistant_msg.content)
        
        print("\n--- FULL METADATA ---")
        import json
        print(json.dumps(assistant_msg.context or {}, indent=2))
        
        # Check recalled entities in context
        recalled = (assistant_msg.context or {}).get("recalled_entity_names", [])
        print(f"\n--- RECALLED ENTITIES ({len(recalled)}) ---")
        for name in recalled:
            print(f"- {name}")
            
    finally:
        db.close()

if __name__ == "__main__":
    main()
