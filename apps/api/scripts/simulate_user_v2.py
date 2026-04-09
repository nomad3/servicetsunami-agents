import os
import sys
import uuid
import asyncio
from sqlalchemy.orm import Session

# Add apps/api to path
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from app.db.session import SessionLocal
from app.services.chat import post_user_message
from app.models.chat import ChatSession

def main():
    tenant_id = uuid.UUID("0f134606-3906-44a5-9e88-6c2020f0f776")
    user_id = uuid.UUID("d18d0af0-6ff9-4226-a7f8-d9adfa884649")
    session_id = uuid.UUID("29806365-47b7-41b1-a2f6-624adbd75ddc")
    
    db = SessionLocal()
    try:
        from app.memory.feature_flag import is_v2_enabled
        print(f"DEBUG: is_v2_enabled({tenant_id}) = {is_v2_enabled(tenant_id)}")
        
        session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
        if not session:
            print("Session not found")
            return

        print(f"Simulating user message for tenant {tenant_id}...")
        user_msg, assistant_msg = post_user_message(
            db,
            session=session,
            user_id=user_id,
            content="Who am I? Check your memory about my role and recent commitments.",
        )
        
        print("\n--- USER MESSAGE ---")
        print(user_msg.content)
        print("\n--- ASSISTANT RESPONSE ---")
        print(assistant_msg.content)
        print("\n--- METADATA ---")
        print(assistant_msg.context)
        
    finally:
        db.close()

if __name__ == "__main__":
    main()
