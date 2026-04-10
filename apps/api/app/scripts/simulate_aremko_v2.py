import os
import sys
import uuid
from sqlalchemy.orm import Session

# Add apps/api to path
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from app.db.session import SessionLocal
from app.services.chat import post_user_message
from app.models.chat import ChatSession

def main():
    tenant_id = uuid.UUID("7f065369-1e08-4730-8e28-7fb7ecdd6093")
    user_id = uuid.UUID("d6391930-ed5c-4881-bf25-6f799a1a7d2f")
    session_id = uuid.UUID("afbe2d72-17bf-43fe-af4e-0362434fb619")
    
    db = SessionLocal()
    try:
        from app.memory.feature_flag import is_v2_enabled
        print(f"DEBUG: is_v2_enabled({tenant_id}) = {is_v2_enabled(tenant_id)}")
        
        session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
        if not session:
            print("Session not found")
            return

        print(f"Simulating user message for Aremko (tenant {tenant_id})...")
        user_msg, assistant_msg = post_user_message(
            db,
            session=session,
            user_id=user_id,
            content="Hola! ¿Quien soy yo? ¿Que tareas tenemos pendientes para hoy?",
        )
        
        print("\n--- USER MESSAGE ---")
        print(user_msg.content)
        print("\n--- ASSISTANT RESPONSE ---")
        print(assistant_msg.content)
        
    finally:
        db.close()

if __name__ == "__main__":
    main()
