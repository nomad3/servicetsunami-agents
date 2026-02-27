from pydantic import BaseModel
from typing import Optional, Literal
from datetime import datetime
import uuid

# Supported connector types
ConnectorType = Literal["snowflake", "postgres", "mysql", "s3", "gcs", "databricks", "api"]
ConnectorStatus = Literal["pending", "active", "error"]

class ConnectorBase(BaseModel):
    name: str
    description: Optional[str] = None
    type: ConnectorType
    config: dict

class ConnectorCreate(ConnectorBase):
    pass

class ConnectorUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    config: Optional[dict] = None

class ConnectorTestRequest(BaseModel):
    type: ConnectorType
    config: dict

class ConnectorTestResponse(BaseModel):
    success: bool
    message: str
    metadata: Optional[dict] = None

class Connector(ConnectorBase):
    id: uuid.UUID
    tenant_id: uuid.UUID
    status: ConnectorStatus = "pending"
    last_test_at: Optional[datetime] = None
    last_test_error: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True
        populate_by_name = True
