"""Enhanced chat service integrating orchestration, memory, and multi-LLM."""
import time
from typing import Optional, Tuple, Dict, Any
import uuid

from sqlalchemy.orm import Session

from app.models.chat import ChatSession, ChatMessage
from app.services import chat as base_chat_service
from app.services.audit_log import write_audit_log
from app.services.memory.memory_service import MemoryService
from app.services.llm.router import LLMRouter


class EnhancedChatService:
    """Chat service with full orchestration, memory, and LLM integration."""

    def __init__(self, db: Session, tenant_id: uuid.UUID):
        self.db = db
        self.tenant_id = tenant_id
        self.memory_service = MemoryService(db)
        self.llm_router = LLMRouter(db)

    def create_session_with_orchestration(
        self,
        user_id: uuid.UUID,
        dataset_id: uuid.UUID,
        agent_kit_id: uuid.UUID,
        agent_group_id: Optional[uuid.UUID] = None,
        title: Optional[str] = None,
    ) -> ChatSession:
        """Create chat session with optional agent group orchestration."""
        session = base_chat_service.create_session(
            self.db,
            tenant_id=self.tenant_id,
            user_id=user_id,
            dataset_id=dataset_id,
            agent_kit_id=agent_kit_id,
            title=title,
        )

        # Link to agent group if provided
        if agent_group_id:
            session.agent_group_id = agent_group_id
            self.db.commit()
            self.db.refresh(session)

        return session

    def post_message_with_memory(
        self,
        session: ChatSession,
        user_id: uuid.UUID,
        content: str,
        agent_id: Optional[uuid.UUID] = None,
    ) -> Tuple[ChatMessage, ChatMessage]:
        """Post message with memory recall and storage."""
        _start = time.time()
        # Recall relevant memories for context
        memories = []
        if agent_id:
            memories = self.memory_service.get_relevant_memories(
                agent_id=agent_id,
                tenant_id=self.tenant_id,
                limit=5,
                min_importance=0.3,
            )

        # Inject memory context into session
        if memories:
            memory_context = {
                "recalled_memories": [
                    {
                        "content": m.content,
                        "type": m.memory_type,
                        "importance": m.importance,
                    }
                    for m in memories
                ],
            }
            session.memory_context = memory_context
            self.db.commit()

        # Post message using base service
        user_msg, assistant_msg = base_chat_service.post_user_message(
            self.db,
            session=session,
            user_id=user_id,
            content=content,
        )

        # Store new experience as memory
        if agent_id and assistant_msg:
            self.memory_service.store(
                agent_id=agent_id,
                tenant_id=self.tenant_id,
                content=f"User asked: {content}. I responded: {assistant_msg.content[:200]}",
                memory_type="experience",
                importance=0.5,
                source="conversation",
            )

        response_text = assistant_msg.content if assistant_msg else ""
        write_audit_log(
            tenant_id=self.tenant_id,
            agent_id=agent_id,
            invoked_by_user_id=user_id,
            session_id=session.id,
            invocation_type="chat",
            input_summary=content[:500],
            output_summary=response_text[:500],
            latency_ms=int((time.time() - _start) * 1000),
            status="success",
        )

        return user_msg, assistant_msg

    def select_llm_for_task(
        self,
        task_type: str,
        complexity: str = "medium",
        requires_vision: bool = False,
    ) -> Dict[str, Any]:
        """Select optimal LLM for a task using the router."""
        # Map complexity to priority
        priority_map = {
            "low": "cost",
            "medium": "balanced",
            "high": "quality",
        }
        priority = priority_map.get(complexity, "balanced")

        try:
            model = self.llm_router.select_model(
                tenant_id=self.tenant_id,
                task_type=task_type,
                priority=priority,
            )
            return {
                "model_id": str(model.id),
                "model_name": model.model_name,
                "provider": model.provider,
                "supports_vision": model.supports_vision,
                "max_tokens": model.max_tokens,
            }
        except ValueError as e:
            # Return fallback info if no model available
            return {
                "error": str(e),
                "model_name": "fallback",
                "provider": "none",
            }

    def get_session_with_context(
        self,
        session_id: uuid.UUID,
    ) -> Optional[ChatSession]:
        """Get session with full orchestration and memory context."""
        session = base_chat_service.get_session(
            self.db,
            session_id=session_id,
            tenant_id=self.tenant_id,
        )
        return session


def get_enhanced_chat_service(db: Session, tenant_id: uuid.UUID) -> EnhancedChatService:
    """Factory function to create EnhancedChatService."""
    return EnhancedChatService(db, tenant_id)
