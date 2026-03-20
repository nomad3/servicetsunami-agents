"""Service layer for STP agent packages."""
import base64
import binascii
import hashlib
import uuid
from typing import Any, Dict, List, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy.orm import Session

from app.models.agent_package import AgentPackage


def _decode_base64(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(value + padding)


def _verify_signature(package_content: str, signature: Optional[str], creator_public_key: Optional[str]) -> bool:
    if not signature or not creator_public_key:
        return False
    try:
        public_key = Ed25519PublicKey.from_public_bytes(_decode_base64(creator_public_key))
        public_key.verify(_decode_base64(signature), package_content.encode("utf-8"))
        return True
    except (InvalidSignature, ValueError, TypeError, binascii.Error):
        return False


def publish_agent_package(
    db: Session,
    tenant_id: uuid.UUID,
    *,
    name: str,
    version: str,
    package_content: str,
    metadata: Optional[Dict[str, Any]] = None,
    skill_id: Optional[uuid.UUID] = None,
    required_tools: Optional[List[str]] = None,
    required_cli: str = "any",
    pricing_tier: str = "simple",
    signature: Optional[str] = None,
    creator_public_key: Optional[str] = None,
    status: str = "published",
) -> AgentPackage:
    content_hash = hashlib.sha256(package_content.encode("utf-8")).hexdigest()
    package = db.query(AgentPackage).filter(
        AgentPackage.tenant_id == tenant_id,
        AgentPackage.name == name,
        AgentPackage.version == version,
    ).first()

    if package is None:
        package = AgentPackage(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            creator_tenant_id=tenant_id,
            name=name,
            version=version,
            package_content=package_content,
            content_hash=content_hash,
        )
        db.add(package)

    package.package_content = package_content
    package.content_hash = content_hash
    package.signature = signature
    package.creator_public_key = creator_public_key
    package.skill_id = skill_id
    package.package_metadata = metadata
    package.required_tools = required_tools or []
    package.required_cli = required_cli
    package.pricing_tier = pricing_tier
    package.status = status
    db.commit()
    db.refresh(package)
    return package


def list_agent_packages(
    db: Session,
    tenant_id: uuid.UUID,
    *,
    status: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
) -> List[AgentPackage]:
    query = db.query(AgentPackage).filter(AgentPackage.tenant_id == tenant_id)
    if status:
        query = query.filter(AgentPackage.status == status)
    return query.order_by(AgentPackage.created_at.desc()).offset(skip).limit(limit).all()


def get_agent_package(db: Session, tenant_id: uuid.UUID, package_id: uuid.UUID) -> Optional[AgentPackage]:
    return db.query(AgentPackage).filter(
        AgentPackage.id == package_id,
        AgentPackage.tenant_id == tenant_id,
    ).first()


def record_download(db: Session, package: AgentPackage) -> AgentPackage:
    package.downloads = (package.downloads or 0) + 1
    db.commit()
    db.refresh(package)
    return package


def verify_agent_package(package: AgentPackage) -> Dict[str, Any]:
    recalculated_hash = hashlib.sha256(package.package_content.encode("utf-8")).hexdigest()
    return {
        "package_id": package.id,
        "content_hash": package.content_hash,
        "hash_verified": recalculated_hash == package.content_hash,
        "signature_verified": _verify_signature(
            package.package_content,
            package.signature,
            package.creator_public_key,
        ),
    }
