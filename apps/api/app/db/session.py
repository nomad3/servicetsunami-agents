import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings

if os.environ.get("TESTING") == "True":
    SQLALCHEMY_DATABASE_URL = "sqlite:///./test.db"
    engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
else:
    engine = create_engine(
        settings.DATABASE_URL,
        pool_pre_ping=True,
        pool_size=20,
        max_overflow=10,
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)