"""Agent package API routes for the STP marketplace."""
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api import deps
from app.models.user import User
from app.schemas.agent_package import (
    AgentPackageDownload,
    AgentPackageInDB,
    AgentPackagePublish,
    AgentPackageVerifyResponse,
)
from app.services import agent_packages as svc

router = APIRouter()


@router.post("/publish", response_model=AgentPackageInDB, status_code=201)
def publish_agent_package(
    item_in: AgentPackagePublish,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Publish or update an agent package for the current tenant."""
    package = svc.publish_agent_package(
        db,
        tenant_id=current_user.tenant_id,
        name=item_in.name,
        version=item_in.version,
        package_content=item_in.package_content,
        metadata=item_in.metadata,
        skill_id=item_in.skill_id,
        required_tools=item_in.required_tools,
        required_cli=item_in.required_cli,
        pricing_tier=item_in.pricing_tier,
        signature=item_in.signature,
        creator_public_key=item_in.creator_public_key,
        status=item_in.status,
    )
    return AgentPackageInDB.model_validate(package)


@router.get("", response_model=List[AgentPackageInDB])
def list_agent_packages(
    status: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """List agent packages for the current tenant."""
    packages = svc.list_agent_packages(db, current_user.tenant_id, status=status, skip=skip, limit=limit)
    return [AgentPackageInDB.model_validate(package) for package in packages]


@router.get("/{package_id}", response_model=AgentPackageInDB)
def get_agent_package(
    package_id: UUID,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Get package details without returning the full package content."""
    package = svc.get_agent_package(db, current_user.tenant_id, package_id)
    if not package:
        raise HTTPException(status_code=404, detail="Agent package not found")
    return AgentPackageInDB.model_validate(package)


@router.get("/{package_id}/download", response_model=AgentPackageDownload)
def download_agent_package(
    package_id: UUID,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Download the package payload and increment the download counter."""
    package = svc.get_agent_package(db, current_user.tenant_id, package_id)
    if not package:
        raise HTTPException(status_code=404, detail="Agent package not found")
    svc.record_download(db, package)
    return AgentPackageDownload(
        id=package.id,
        name=package.name,
        version=package.version,
        content_hash=package.content_hash,
        package_content=package.package_content,
        signature=package.signature,
        creator_public_key=package.creator_public_key,
        metadata=package.package_metadata,
    )


@router.post("/{package_id}/verify", response_model=AgentPackageVerifyResponse)
def verify_agent_package(
    package_id: UUID,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Verify the stored package hash and optional Ed25519 signature."""
    package = svc.get_agent_package(db, current_user.tenant_id, package_id)
    if not package:
        raise HTTPException(status_code=404, detail="Agent package not found")
    return AgentPackageVerifyResponse.model_validate(svc.verify_agent_package(package))
