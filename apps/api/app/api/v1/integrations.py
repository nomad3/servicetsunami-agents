from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from temporalio.client import Client

from app.api import deps
from app.core.config import settings
from app.models.user import User
from app.services.chat_import import chat_import_service
from app.services.integration_status import get_connected_integrations, get_tool_mapping
from app.models.chat import ChatSession, ChatMessage
from app.workflows.knowledge_extraction import KnowledgeExtractionWorkflow

router = APIRouter()


# ---------------------------------------------------------------------------
# Integration status endpoints
# ---------------------------------------------------------------------------

@router.get("/status")
def integration_status(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """
    Return which integrations are connected for the current tenant.
    Each entry includes: connected (bool), name (display name), icon.
    """
    return get_connected_integrations(db, current_user.tenant_id)


@router.get("/tool-mapping")
def tool_mapping():
    """
    Return the static mapping of MCP tool names to their required integration.
    Tools mapped to null require no integration.
    """
    return get_tool_mapping()


@router.post("/import/chatgpt", status_code=201)
async def import_chatgpt_history(
    file: UploadFile = File(...),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """
    Import chat history from ChatGPT export (conversations.json).
    """
    if not file.filename.endswith('.json'):
        raise HTTPException(status_code=400, detail="File must be a JSON file")

    content = await file.read()
    try:
        sessions_data = chat_import_service.parse_chatgpt_export(content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    imported_count = 0
    session_ids = []

    for session_data in sessions_data:
        # Check if already imported (by external_id)
        existing = db.query(ChatSession).filter(
            ChatSession.tenant_id == current_user.tenant_id,
            ChatSession.external_id == session_data["external_id"],
            ChatSession.source == "chatgpt_import"
        ).first()

        if existing:
            continue

        # Create session
        db_session = ChatSession(
            title=session_data["title"],
            tenant_id=current_user.tenant_id,
            source="chatgpt_import",
            external_id=session_data["external_id"]
        )
        db.add(db_session)
        db.flush() # Get ID
        session_ids.append(db_session.id)

        # Create messages
        for msg in session_data["messages"]:
            db_msg = ChatMessage(
                session_id=db_session.id,
                role=msg["role"],
                content=msg["content"],
                # created_at could be set if we parse it correctly
            )
            db.add(db_msg)

        imported_count += 1

    db.commit()

    # Trigger knowledge extraction via Temporal Workflow
    try:
        temporal_client = await Client.connect(settings.TEMPORAL_ADDRESS)

        for session_id in session_ids:
            await temporal_client.start_workflow(
                KnowledgeExtractionWorkflow.run,
                args=[str(session_id), str(current_user.tenant_id)],
                id=f"knowledge-extraction-{session_id}",
                task_queue="servicetsunami-databricks",
            )
    except Exception as e:
        print(f"Failed to start Temporal workflow: {e}")

    return {"message": f"Successfully imported {imported_count} chat sessions from ChatGPT. Knowledge extraction started."}

@router.post("/import/claude", status_code=201)
async def import_claude_history(
    file: UploadFile = File(...),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """
    Import chat history from Claude export (conversations.json).
    """
    if not file.filename.endswith('.json'):
        raise HTTPException(status_code=400, detail="File must be a JSON file")

    content = await file.read()
    try:
        sessions_data = chat_import_service.parse_claude_export(content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    imported_count = 0
    session_ids = []

    for session_data in sessions_data:
        # Check if already imported
        existing = db.query(ChatSession).filter(
            ChatSession.tenant_id == current_user.tenant_id,
            ChatSession.external_id == session_data["external_id"],
            ChatSession.source == "claude_import"
        ).first()

        if existing:
            continue

        # Create session
        db_session = ChatSession(
            title=session_data["title"],
            tenant_id=current_user.tenant_id,
            source="claude_import",
            external_id=session_data["external_id"]
        )
        db.add(db_session)
        db.flush()
        session_ids.append(db_session.id)

        # Create messages
        for msg in session_data["messages"]:
            db_msg = ChatMessage(
                session_id=db_session.id,
                role=msg["role"],
                content=msg["content"],
            )
            db.add(db_msg)

        imported_count += 1

    db.commit()

    # Trigger knowledge extraction via Temporal Workflow
    try:
        temporal_client = await Client.connect(settings.TEMPORAL_ADDRESS)

        for session_id in session_ids:
            await temporal_client.start_workflow(
                KnowledgeExtractionWorkflow.run,
                args=[str(session_id), str(current_user.tenant_id)],
                id=f"knowledge-extraction-{session_id}",
                task_queue="servicetsunami-databricks", # Using the existing worker queue
            )
    except Exception as e:
        # Log error but don't fail the import response
        print(f"Failed to start Temporal workflow: {e}")

    return {"message": f"Successfully imported {imported_count} chat sessions from Claude. Knowledge extraction started."}
