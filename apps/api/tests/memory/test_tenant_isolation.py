"""Cross-tenant isolation — the most important test in the file.

Anti-success criterion #6 (design doc §11.1): any cross-tenant data
leak is a HARD STOP. These tests are the canary that detects it.

NOTE on cleanup: `recall()` calls `db.commit()` internally to persist
recall_count updates (see recall.py:191). That commit also persists
any pending session inserts — including our test fixtures. So the
db_session fixture's rollback teardown is NOT sufficient for tests
that both insert data AND call recall(). Each test below uses an
explicit try/finally to DELETE its own test data via raw SQL on a
fresh transaction. We collect IDs up front so the cleanup is
deterministic regardless of session state.
"""
import pytest
from uuid import uuid4

from sqlalchemy import text

from app.memory import recall
from app.memory.types import RecallRequest
from app.models.tenant import Tenant
from app.models.knowledge_entity import KnowledgeEntity


def _purge(db_session, tenant_ids: list, entity_ids: list) -> None:
    """Hard-delete test data via raw SQL on a fresh transaction.

    We can't rely on the fixture rollback because recall() commits
    pending session state. We can't use the ORM either, because the
    test session may be in an inconsistent state after the SUT call.
    Raw DELETE on a fresh transaction is the safest cleanup.
    """
    try:
        db_session.rollback()
    except Exception:
        pass
    if entity_ids:
        db_session.execute(
            text("DELETE FROM knowledge_entities WHERE id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": [str(i) for i in entity_ids]},
        )
    if tenant_ids:
        db_session.execute(
            text("DELETE FROM tenants WHERE id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": [str(i) for i in tenant_ids]},
        )
    db_session.commit()


@pytest.mark.integration
def test_tenant_a_cannot_recall_tenant_b_entities(db_session):
    """Tenant A must not see entities owned by Tenant B, even when
    querying with the EXACT name. This is the canonical cross-tenant
    isolation canary for the semantic recall path."""
    tenant_a = Tenant(name=f"iso-a-{uuid4().hex[:8]}")
    tenant_b = Tenant(name=f"iso-b-{uuid4().hex[:8]}")
    db_session.add_all([tenant_a, tenant_b])
    db_session.flush()

    secret = KnowledgeEntity(
        tenant_id=tenant_b.id,
        name="Tenant B Secret Project Atlas Canary",
        entity_type="project",
        category="project",
        description="Top secret cross-tenant leak canary",
    )
    db_session.add(secret)
    db_session.flush()

    tenant_ids = [tenant_a.id, tenant_b.id]
    entity_ids = [secret.id]

    try:
        # Tenant A queries with the EXACT name — must not see it
        resp = recall(
            db_session,
            RecallRequest(
                tenant_id=tenant_a.id,
                agent_slug="luna",
                query="Tenant B Secret Project Atlas Canary",
            ),
        )
        assert not any("Atlas Canary" in e.name for e in resp.entities), (
            "CROSS-TENANT LEAK: tenant A saw tenant B's entity via semantic recall"
        )
        assert not any(
            "Atlas Canary" in (o.content or "") for o in resp.observations
        ), "CROSS-TENANT LEAK: tenant A saw tenant B's observation via semantic recall"
    finally:
        _purge(db_session, tenant_ids, entity_ids)


@pytest.mark.integration
def test_recall_with_invalid_tenant_id_returns_empty(db_session):
    """A fresh random UUID tenant returns empty without crashing.

    No fixture inserts here, so no cleanup is required.
    """
    resp = recall(
        db_session,
        RecallRequest(
            tenant_id=uuid4(),
            agent_slug="luna",
            query="anything",
        ),
    )
    assert resp.entities == []
    assert resp.observations == []


@pytest.mark.integration
def test_keyword_fallback_does_not_leak_across_tenants(db_session, monkeypatch):
    """Force the keyword fallback path (Task 11 reviewer flagged it as
    not having visibility filter — at least verify tenant isolation holds)."""
    from app.services import embedding_service

    tenant_a = Tenant(name=f"iso-a-kw-{uuid4().hex[:8]}")
    tenant_b = Tenant(name=f"iso-b-kw-{uuid4().hex[:8]}")
    db_session.add_all([tenant_a, tenant_b])
    db_session.flush()

    secret = KnowledgeEntity(
        tenant_id=tenant_b.id,
        name="Project Vector Bravo Keyword Canary",
        entity_type="project",
        category="project",
        description="Keyword fallback cross-tenant canary",
    )
    db_session.add(secret)
    db_session.flush()

    tenant_ids = [tenant_a.id, tenant_b.id]
    entity_ids = [secret.id]

    try:
        # Force keyword fallback by making embed_text raise
        def boom(*a, **kw):
            raise RuntimeError("forced fallback for isolation test")

        monkeypatch.setattr(embedding_service, "embed_text", boom)

        # Tenant A queries with the exact name — must NOT see the secret
        resp = recall(
            db_session,
            RecallRequest(
                tenant_id=tenant_a.id,
                agent_slug="luna",
                query="Project Vector Bravo Keyword Canary",
            ),
        )
        assert resp.metadata.used_keyword_fallback is True
        assert not any("Vector Bravo" in e.name for e in resp.entities), (
            "CROSS-TENANT LEAK: tenant A saw tenant B's entity via keyword fallback"
        )
        assert not any(
            "Vector Bravo" in (o.content or "") for o in resp.observations
        ), "CROSS-TENANT LEAK: tenant A saw tenant B's observation via keyword fallback"
    finally:
        # The keyword fallback test uses flush() to insert, but recall()
        # in this path does NOT call db.commit() (no entities → no
        # recall_count update), so the test data lives only in the
        # transaction. Still, _purge() handles both cases safely.
        _purge(db_session, tenant_ids, entity_ids)
