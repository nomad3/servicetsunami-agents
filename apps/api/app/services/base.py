import uuid
from typing import Generic, List, Optional, Type, TypeVar

from sqlalchemy.orm import Session

from app.core.security import verify_password
from app.models.user import User

T = TypeVar("T")


class BaseService(Generic[T]):
    """Generic CRUD base service for SQLAlchemy models."""

    model_class: Type[T]

    def get(self, db: Session, id: uuid.UUID) -> Optional[T]:
        return db.query(self.model_class).filter(self.model_class.id == id).first()

    def get_by_tenant(self, db: Session, tenant_id: uuid.UUID, id: uuid.UUID) -> Optional[T]:
        return db.query(self.model_class).filter(
            self.model_class.id == id,
            self.model_class.tenant_id == tenant_id,
        ).first()

    def list_by_tenant(self, db: Session, tenant_id: uuid.UUID, limit: int = 100) -> List[T]:
        return db.query(self.model_class).filter(
            self.model_class.tenant_id == tenant_id,
        ).limit(limit).all()

    def create(self, db: Session, obj: T) -> T:
        db.add(obj)
        db.commit()
        db.refresh(obj)
        return obj

    def delete(self, db: Session, id: uuid.UUID) -> bool:
        obj = self.get(db, id)
        if obj:
            db.delete(obj)
            db.commit()
            return True
        return False

def authenticate_user(
    db: Session, *, email: str, password: str
) -> User | None:
    user = db.query(User).filter(User.email == email).first()
    if not user:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user
