import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings

# In tests, we still want Postgres.
# If running locally (not in Docker), 'db' hostname won't resolve,
# so we fallback to localhost:8003 and use a dedicated test database.
#
# Safety: both TESTING=True AND PYTEST_CURRENT_TEST must be set. The
# PYTEST_CURRENT_TEST env var is injected automatically by pytest per-test,
# so stray TESTING=True in a prod container cannot cause a silent redirect
# to a non-existent test DB.
db_url = settings.DATABASE_URL
if os.environ.get("TESTING") == "True" and os.environ.get("PYTEST_CURRENT_TEST"):
    if "@db:5432" in db_url:
        db_url = db_url.replace("@db:5432", "@localhost:8003")
    # Always use the dedicated test database when pytest is actively running.
    if "/agentprovision" in db_url and not db_url.endswith("_test"):
        db_url = db_url.replace("/agentprovision", "/agentprovision_test")

engine = create_engine(
    db_url,
    pool_pre_ping=True,
    pool_size=20,
    max_overflow=10,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
