"""Unit tests for AgentRegistry.find_by_capability across native + external.

Uses an in-memory SQLite engine that doesn't speak Postgres JSONB —
the registry's external-agent query is wrapped in try/except, so
external matches are silently empty in this test layer. The native
branch is what we lock down here. The Postgres-dialect external query
is exercised via curl against the live API in PR validation.
"""
import os
os.environ["TESTING"] = "True"

import uuid
from types import SimpleNamespace
from app.services.agent_registry import AgentRegistry


def _native(name, capabilities, status="production"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        name=name,
        description=f"native {name}",
        status=status,
        capabilities=capabilities,
        tenant_id=uuid.uuid4(),
    )


class _Query:
    def __init__(self, rows):
        self._rows = rows
    def filter(self, *a, **k):
        return self
    def all(self):
        return self._rows


class _DB:
    """Tiny stand-in. ``query(Model)`` returns canned rows; the second
    call (external_agents) raises, simulating dialect mismatch.
    """
    def __init__(self, native_rows, external_raises=True):
        self.native_rows = native_rows
        self.external_raises = external_raises
        self._calls = 0

    def query(self, _model):
        self._calls += 1
        if self._calls == 1:
            return _Query(self.native_rows)
        if self.external_raises:
            raise RuntimeError("simulated dialect mismatch")
        return _Query([])


def test_find_by_capability_returns_kind_tuples_native_only():
    a = _native("native-a", ["scoring", "lead"])
    b = _native("native-b", ["other"])
    db = _DB([a, b])
    out = AgentRegistry().find_by_capability("scoring", a.tenant_id, db)
    assert len(out) == 1
    kind, agent = out[0]
    assert kind == "native"
    assert agent is a


def test_find_by_capability_swallows_external_query_failure():
    """A Postgres-only JSONB query against a non-PG engine must not
    poison the native results.
    """
    a = _native("native-a", ["scoring"])
    db = _DB([a], external_raises=True)
    out = AgentRegistry().find_by_capability("scoring", a.tenant_id, db)
    # Native still returned; external just empty.
    assert [k for k, _ in out] == ["native"]


def test_find_by_capability_filters_native_by_capability_membership():
    matched = _native("matched", ["foo"])
    other = _native("other", ["bar"])
    db = _DB([matched, other])
    out = AgentRegistry().find_by_capability("foo", matched.tenant_id, db)
    names = [a.name for _, a in out]
    assert names == ["matched"]


def _external(name, capabilities, status="online"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        name=name,
        description=f"external {name}",
        status=status,
        capabilities=capabilities,
    )


class _DBWithExternal:
    """Returns native rows on first query, external rows on second."""
    def __init__(self, native_rows, external_rows):
        self.native_rows = native_rows
        self.external_rows = external_rows
        self._calls = 0
    def query(self, _model):
        self._calls += 1
        return _Query(self.native_rows if self._calls == 1 else self.external_rows)


def test_find_by_capability_returns_external_with_kind_tag():
    """Locks the (kind, agent) tuple shape across both branches."""
    n = _native("native-a", ["scoring"])
    e = _external("ext-a", ["scoring"])
    db = _DBWithExternal([n], [e])
    out = AgentRegistry().find_by_capability("scoring", n.tenant_id, db)
    kinds = sorted(k for k, _ in out)
    assert kinds == ["external", "native"]


def test_find_by_capability_skips_native_with_non_list_capabilities():
    """Defensive: agents with mis-shaped capabilities (None / dict) shouldn't
    crash the loop.
    """
    good = _native("good", ["foo"])
    broken = _native("broken", None)
    weird = _native("weird", {"foo": True})
    db = _DB([good, broken, weird])
    out = AgentRegistry().find_by_capability("foo", good.tenant_id, db)
    assert [a.name for _, a in out] == ["good"]
