from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status, Header, Body
from sqlalchemy.orm import Session

from app import schemas
from app.api import deps
from app.services import data_source as data_source_service
from app.models.user import User
from app.core.config import settings
import uuid

router = APIRouter()

@router.get("/", response_model=List[schemas.data_source.DataSource])
def read_data_sources(
    db: Session = Depends(deps.get_db),
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(deps.get_current_active_user),
):
    """
    Retrieve data sources for the current tenant.
    """
    data_sources = data_source_service.get_data_sources_by_tenant(
        db, tenant_id=current_user.tenant_id, skip=skip, limit=limit
    )
    return data_sources


@router.post("/", response_model=schemas.data_source.DataSource, status_code=status.HTTP_201_CREATED)
def create_data_source(
    *,
    db: Session = Depends(deps.get_db),
    item_in: schemas.data_source.DataSourceCreate,
    current_user: User = Depends(deps.get_current_active_user),
):
    """
    Create new data source for the current tenant.
    """
    item = data_source_service.create_tenant_data_source(db=db, item_in=item_in, tenant_id=current_user.tenant_id)
    return item

@router.get("/{data_source_id}", response_model=schemas.data_source.DataSource)
def read_data_source_by_id(
    data_source_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """
    Retrieve a specific data source by ID for the current tenant.
    """
    data_source = data_source_service.get_data_source(db, data_source_id=data_source_id)
    if not data_source or str(data_source.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Data source not found")
    return data_source

@router.put("/{data_source_id}", response_model=schemas.data_source.DataSource)
def update_data_source(
    *,
    db: Session = Depends(deps.get_db),
    data_source_id: uuid.UUID,
    item_in: schemas.data_source.DataSourceCreate,
    current_user: User = Depends(deps.get_current_active_user),
):
    """
    Update an existing data source for the current tenant.
    """
    data_source = data_source_service.get_data_source(db, data_source_id=data_source_id)
    if not data_source or str(data_source.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Data source not found")
    item = data_source_service.update_data_source(db=db, db_obj=data_source, obj_in=item_in)
    return item

@router.delete("/{data_source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_data_source(
    *,
    db: Session = Depends(deps.get_db),
    data_source_id: uuid.UUID,
    current_user: User = Depends(deps.get_current_active_user),
):
    """
    Delete a data source for the current tenant.
    """
    data_source = data_source_service.get_data_source(db, data_source_id=data_source_id)
    if not data_source or str(data_source.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Data source not found")
    data_source_service.delete_data_source(db=db, data_source_id=data_source_id)
    return {"message": "Data source deleted successfully"}


@router.post("/{data_source_id}/query", response_model=List[dict])
def execute_data_source_query(
    *,
    db: Session = Depends(deps.get_db),
    data_source_id: uuid.UUID,
    query: str = Body(..., embed=True),
    current_user: User = Depends(deps.get_current_active_user),
):
    """
    Execute a SQL query on the data source.
    """
    try:
        results = data_source_service.execute_query(db, data_source_id=data_source_id, query=query)
        return results
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except NotImplementedError as e:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(e))


# ==================== Internal Endpoints (ADK / MCP Server) ====================

@router.get("/internal/list")
def list_data_sources_internal(
    tenant_id: Optional[str] = None,
    db: Session = Depends(deps.get_db),
    x_internal_key: Optional[str] = Header(None, alias="X-Internal-Key"),
):
    """
    List data sources (internal, no JWT required).
    Used by ADK agents to discover queryable data sources.
    """
    if x_internal_key not in (settings.API_INTERNAL_KEY, settings.MCP_API_KEY):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid internal key")
    if tenant_id:
        try:
            tid = uuid.UUID(tenant_id)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid tenant_id: {tenant_id}")
        return data_source_service.get_data_sources_by_tenant(db, tenant_id=tid)
    return data_source_service.get_all_data_sources(db)


@router.post("/{data_source_id}/internal-query")
def execute_data_source_query_internal(
    *,
    db: Session = Depends(deps.get_db),
    data_source_id: uuid.UUID,
    query: str = Body(..., embed=True),
    tenant_id: Optional[str] = Body(None, embed=True),
    x_internal_key: Optional[str] = Header(None, alias="X-Internal-Key"),
):
    """
    Execute a query on a data source (internal, no JWT required).
    Used by ADK agents to query tenant data.
    """
    if x_internal_key not in (settings.API_INTERNAL_KEY, settings.MCP_API_KEY):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid internal key")
    try:
        results = data_source_service.execute_query(db, data_source_id=data_source_id, query=query)
        return results
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except NotImplementedError as e:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(e))


@router.get("/{data_source_id}/with-credentials", response_model=schemas.data_source.DataSourceWithCredentials)
def get_data_source_with_credentials(
    data_source_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    x_internal_key: Optional[str] = Header(None, alias="X-Internal-Key"),
):
    """
    Get data source with decrypted credentials.

    INTERNAL USE ONLY - requires X-Internal-Key header.
    Used by MCP server to fetch connection credentials.
    """
    # Verify internal key
    if x_internal_key != settings.API_INTERNAL_KEY:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid internal key")

    data_source = data_source_service.get_data_source(db, data_source_id=data_source_id)
    if not data_source:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Data source not found")

    # In production, decrypt sensitive fields here
    # For MVP, config is stored as-is
    return data_source
