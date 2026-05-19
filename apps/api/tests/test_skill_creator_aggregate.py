"""Tests for the Phase-3 benchmark aggregator + analyzer.

Three surfaces are exercised:

  1. ``app.services.skill_creator.aggregate`` — pure-Python math
     (``_mean``, ``_stddev_sample``) and the public ``aggregate_iteration``
     against a stub DB.
  2. ``app.services.skill_creator.analyzer.analyze`` — the heuristic
     notes against fabricated runs/grading payloads.
  3. ``GET /api/v1/skills/{skill_id}/evals/iterations/{N}/benchmark`` —
     the endpoint, mocked via the same ``_StubDB`` pattern as the
     existing ``test_skill_evals_endpoint.py`` / ``test_skill_eval_runner.py``.

No live Postgres, no live Temporal — the aggregator's math is verified
with synthesized rows so a regression in ``_mean`` or
``_stddev_sample`` shows up as a test diff.
"""

from __future__ import annotations

import os
os.environ["TESTING"] = "True"

import json
import math
import uuid
from typing import Any, Dict, List, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deps
from app.api.v1 import skill_evals as skill_evals_module
from app.models.user import User
from app.services.skill_creator import aggregate, analyzer


# ──────────────────────────────────────────────────────────────────────────
# Stat helpers — math correctness
# ──────────────────────────────────────────────────────────────────────────


def test_mean_empty_is_zero():
    assert aggregate._mean([]) == 0.0


def test_mean_basic():
    assert aggregate._mean([1.0, 2.0, 3.0]) == 2.0


def test_stddev_sample_zero_or_one_value():
    """Sample stddev is undefined for n<=1; we emit 0.0 so the viewer has
    a number."""
    assert aggregate._stddev_sample([]) == 0.0
    assert aggregate._stddev_sample([5.0]) == 0.0


def test_stddev_sample_known_values():
    # Sample (Bessel-corrected, n-1) stddev of [2, 4, 4, 4, 5, 5, 7, 9].
    # Mean = 5, sum-sq-dev = 32, n-1 = 7 → stddev = sqrt(32/7) ≈ 2.1381.
    # (Population stddev with n=8 would be 2.0 — we explicitly use n-1.)
    vals = [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]
    assert math.isclose(
        aggregate._stddev_sample(vals),
        math.sqrt(32.0 / 7.0),
        abs_tol=1e-9,
    )


def test_stats_block_shape():
    """Verifies the mean+stddev keys and rounding precision."""
    out = aggregate._stats_block([1.0, 0.0], [100.0, 200.0], [50.0, 100.0])
    assert set(out.keys()) == {"pass_rate", "timing_ms", "tokens"}
    for metric in out.values():
        assert set(metric.keys()) == {"mean", "stddev"}
    # pass_rate mean is 0.5
    assert out["pass_rate"]["mean"] == 0.5


def test_delta_block_emits_null_stddev():
    """Schemas.md: producers MUST emit null (not 0) for delta stddev."""
    with_stats = aggregate._stats_block([1.0, 1.0], [100.0, 110.0], [50.0, 60.0])
    without_stats = aggregate._stats_block([0.0, 0.0], [90.0, 100.0], [40.0, 50.0])
    delta = aggregate._delta_block(with_stats, without_stats)
    for metric in ("pass_rate", "timing_ms", "tokens"):
        assert delta[metric]["stddev"] is None, (
            f"delta.{metric}.stddev must be null, got {delta[metric]['stddev']!r}"
        )
    # mean delta: with - without
    assert delta["pass_rate"]["mean"] == 1.0  # 1.0 - 0.0
    assert delta["timing_ms"]["mean"] == 10.0  # 105 - 95
    assert delta["tokens"]["mean"] == 10.0     # 55 - 45


# ──────────────────────────────────────────────────────────────────────────
# Stub DB — recognizes the queries aggregate.py + analyzer.py issue.
# ──────────────────────────────────────────────────────────────────────────


class _StubResult:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _StubDB:
    """Recognizes the queries aggregate + analyzer + the endpoint issue.

    Layered atop a list of (run_id, eval_id, with_skill, timing_ms,
    token_usage_dict, status, grading_score_or_None,
    grading_payload_or_None) tuples. The stub assembles the join shape
    aggregate._load_runs_and_grading expects.
    """

    def __init__(
        self,
        *,
        skill_tenant_id: Optional[uuid.UUID] = None,
        skill_name: str = "expense-classifier",
        runs: Optional[List[Dict[str, Any]]] = None,
    ):
        self._skill_tenant_id = skill_tenant_id
        self._skill_name = skill_name
        self._runs = runs or []
        self.executed: List[Dict[str, Any]] = []
        self.committed = False
        self.rolled_back = False

    def execute(self, statement, params=None):
        sql = str(statement)
        self.executed.append({"sql": sql, "params": params or {}})

        # _verify_tenant_owns_skill — selects tenant_id only.
        if "FROM skills WHERE" in sql and "name" not in sql and "tenant_id" in sql:
            if self._skill_tenant_id is None:
                return _StubResult(row=None)
            return _StubResult(row=(str(self._skill_tenant_id),))

        # aggregate_for_skill — SELECT name FROM skills
        if "SELECT name FROM skills" in sql:
            if self._skill_tenant_id is None:
                return _StubResult(row=None)
            return _StubResult(row=(self._skill_name,))

        # aggregate._load_runs_and_grading
        if "FROM skill_eval_runs r" in sql and "skill_eval_grading g" in sql and "g.grading" in sql and "r.token_usage" in sql:
            rows = []
            for r in self._runs:
                rows.append((
                    uuid.UUID(r["run_id"]),
                    uuid.UUID(r["eval_id"]),
                    bool(r["with_skill"]),
                    r.get("timing_ms"),
                    r.get("token_usage") or {},
                    r.get("status", "ok"),
                    r.get("grading_score"),
                    r.get("grading_payload"),
                ))
            return _StubResult(rows=rows)

        # analyzer._expectations_signal_from_grading — selects only
        # with_skill + grading and joins skill_eval_grading.
        if "FROM skill_eval_runs r" in sql and "JOIN skill_eval_grading g" in sql and "g.grading" in sql:
            rows = []
            for r in self._runs:
                if r.get("grading_payload") is None:
                    continue
                rows.append((bool(r["with_skill"]), r["grading_payload"]))
            return _StubResult(rows=rows)

        # analyzer._check_flaky_evals — SELECT eval_id, with_skill, g.score
        if "FROM skill_eval_runs r" in sql and "g.score" in sql and "LEFT JOIN skill_eval_grading g" in sql:
            rows = []
            for r in self._runs:
                rows.append((
                    uuid.UUID(r["eval_id"]),
                    bool(r["with_skill"]),
                    r.get("grading_score"),
                ))
            return _StubResult(rows=rows)

        return _StubResult(row=None)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


def _user(tenant_id: Optional[uuid.UUID] = None) -> User:
    return User(
        id=uuid.uuid4(),
        email=f"user-{uuid.uuid4().hex[:6]}@test.com",
        tenant_id=tenant_id or uuid.uuid4(),
        is_active=True,
        is_superuser=False,
        hashed_password="x",
    )


def _build_client(user: User, db: _StubDB) -> TestClient:
    app = FastAPI()
    app.dependency_overrides[deps.get_current_user] = lambda: user

    def _stub_db():
        yield db

    app.dependency_overrides[deps.get_db] = _stub_db
    app.include_router(skill_evals_module.router, prefix="/api/v1/skills")
    return TestClient(app, raise_server_exceptions=False)


# ──────────────────────────────────────────────────────────────────────────
# aggregate_iteration — happy path and edge cases
# ──────────────────────────────────────────────────────────────────────────


def _mk_run(
    *,
    eval_id: str,
    with_skill: bool,
    timing_ms: int,
    tokens: int,
    score: Optional[float],
    expectations: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Synthesize a single ``skill_eval_runs`` row with optional grading."""
    payload = None
    if expectations is not None:
        payload = {
            "version": 1,
            "eval_id": eval_id,
            "score": score if score is not None else 0.0,
            "passed": (score is not None and score >= 1.0),
            "expectations": expectations,
        }
    return {
        "run_id": str(uuid.uuid4()),
        "eval_id": eval_id,
        "with_skill": with_skill,
        "timing_ms": timing_ms,
        "token_usage": {"input": 0, "output": tokens, "total": tokens},
        "status": "ok",
        "grading_score": score,
        "grading_payload": payload,
    }


def test_aggregate_iteration_happy_path_math():
    """Verify mean+stddev correctness against hand-computed values."""
    skill_id = uuid.uuid4()
    eval_a, eval_b = str(uuid.uuid4()), str(uuid.uuid4())

    runs = [
        # with_skill: both pass, time 100/200, tokens 50/100
        _mk_run(eval_id=eval_a, with_skill=True, timing_ms=100, tokens=50, score=1.0),
        _mk_run(eval_id=eval_b, with_skill=True, timing_ms=200, tokens=100, score=1.0),
        # without_skill: both fail, time 80/120, tokens 40/60
        _mk_run(eval_id=eval_a, with_skill=False, timing_ms=80, tokens=40, score=0.0),
        _mk_run(eval_id=eval_b, with_skill=False, timing_ms=120, tokens=60, score=0.0),
    ]
    db = _StubDB(skill_tenant_id=uuid.uuid4(), runs=runs)

    bench = aggregate.aggregate_iteration(
        db, skill_id=skill_id, iteration=1, skill_slug="x",
    )

    rs = bench["run_summary"]
    # with_skill: pass=1.0 (both 1.0); timing mean=150; tokens mean=75
    assert rs["with_skill"]["pass_rate"]["mean"] == 1.0
    assert rs["with_skill"]["timing_ms"]["mean"] == 150.0
    assert rs["with_skill"]["tokens"]["mean"] == 75.0
    # without_skill: pass=0.0; timing mean=100; tokens mean=50
    assert rs["without_skill"]["pass_rate"]["mean"] == 0.0
    assert rs["without_skill"]["timing_ms"]["mean"] == 100.0
    assert rs["without_skill"]["tokens"]["mean"] == 50.0
    # delta means: 1.0, 50.0, 25.0 — and stddev null
    assert rs["delta"]["pass_rate"]["mean"] == 1.0
    assert rs["delta"]["pass_rate"]["stddev"] is None
    assert rs["delta"]["timing_ms"]["mean"] == 50.0
    assert rs["delta"]["timing_ms"]["stddev"] is None
    assert rs["delta"]["tokens"]["mean"] == 25.0
    assert rs["delta"]["tokens"]["stddev"] is None
    # eval_count = 2 (two distinct eval_ids)
    assert bench["eval_count"] == 2
    assert bench["skill_slug"] == "x"
    assert bench["iteration"] == 1
    assert bench["version"] == 1


def test_aggregate_iteration_stddev_correct():
    """Stddev of with_skill timing [100, 200] = ~70.71 (sample stddev)."""
    eA, eB = str(uuid.uuid4()), str(uuid.uuid4())
    runs = [
        _mk_run(eval_id=eA, with_skill=True, timing_ms=100, tokens=50, score=1.0),
        _mk_run(eval_id=eB, with_skill=True, timing_ms=200, tokens=50, score=1.0),
        _mk_run(eval_id=eA, with_skill=False, timing_ms=50, tokens=50, score=0.0),
        _mk_run(eval_id=eB, with_skill=False, timing_ms=50, tokens=50, score=0.0),
    ]
    db = _StubDB(skill_tenant_id=uuid.uuid4(), runs=runs)

    bench = aggregate.aggregate_iteration(
        db, skill_id=uuid.uuid4(), iteration=1, skill_slug="x",
    )
    # Sample stddev of [100, 200] = sqrt(((100-150)^2 + (200-150)^2) / 1)
    #                              = sqrt(2500 + 2500) = sqrt(5000) ≈ 70.71
    assert math.isclose(
        bench["run_summary"]["with_skill"]["timing_ms"]["stddev"],
        70.71,
        abs_tol=0.05,
    )


def test_aggregate_iteration_ungraded_runs_still_aggregate_timing():
    """Run without a grading row → pass_rate contributes 0 but timing/tokens
    still aggregated. Matches missing-data handling docstring."""
    eA = str(uuid.uuid4())
    runs = [
        # ungraded run, no payload, no score
        _mk_run(eval_id=eA, with_skill=True, timing_ms=500, tokens=300, score=None),
        _mk_run(eval_id=eA, with_skill=False, timing_ms=400, tokens=200, score=None),
    ]
    db = _StubDB(skill_tenant_id=uuid.uuid4(), runs=runs)

    bench = aggregate.aggregate_iteration(
        db, skill_id=uuid.uuid4(), iteration=1, skill_slug="x",
    )
    rs = bench["run_summary"]
    assert rs["with_skill"]["pass_rate"]["mean"] == 0.0
    assert rs["with_skill"]["timing_ms"]["mean"] == 500.0
    assert rs["with_skill"]["tokens"]["mean"] == 300.0
    assert rs["without_skill"]["timing_ms"]["mean"] == 400.0


def test_aggregate_iteration_empty_raises_not_found():
    """No rows for the (skill, iteration) → AggregateNotFound."""
    db = _StubDB(skill_tenant_id=uuid.uuid4(), runs=[])
    with pytest.raises(aggregate.AggregateNotFound):
        aggregate.aggregate_iteration(
            db, skill_id=uuid.uuid4(), iteration=1, skill_slug="x",
        )


def test_aggregate_iteration_per_eval_pairs():
    """per_eval rows must pair the two legs by eval_id."""
    eA = str(uuid.uuid4())
    runs = [
        _mk_run(eval_id=eA, with_skill=True, timing_ms=100, tokens=50, score=1.0),
        _mk_run(eval_id=eA, with_skill=False, timing_ms=80, tokens=40, score=0.0),
    ]
    db = _StubDB(skill_tenant_id=uuid.uuid4(), runs=runs)
    bench = aggregate.aggregate_iteration(
        db, skill_id=uuid.uuid4(), iteration=1, skill_slug="x",
    )
    assert len(bench["per_eval"]) == 1
    row = bench["per_eval"][0]
    assert row["eval_id"] == eA
    assert row["with_skill"]["passed"] is True
    assert row["without_skill"]["passed"] is False


# ──────────────────────────────────────────────────────────────────────────
# Analyzer — heuristic notes
# ──────────────────────────────────────────────────────────────────────────


def test_analyzer_flags_non_discriminating_expectation():
    """An expectation that passed identically in both legs across all runs
    must surface as a 'non-discriminating' note."""
    skill_id = uuid.uuid4()
    eA = str(uuid.uuid4())

    exp_signal = {"id": "e-signal", "description": "Real signal", "passed": True}
    exp_signal_neg = {"id": "e-signal", "description": "Real signal", "passed": False}
    exp_dud_pass = {"id": "e-dud", "description": "Always passes", "passed": True}

    runs = [
        # with_skill: e-signal passes, e-dud passes
        _mk_run(
            eval_id=eA, with_skill=True, timing_ms=100, tokens=50, score=1.0,
            expectations=[exp_signal, exp_dud_pass],
        ),
        # without_skill: e-signal fails, e-dud still passes (the dud)
        _mk_run(
            eval_id=eA, with_skill=False, timing_ms=80, tokens=40, score=0.5,
            expectations=[exp_signal_neg, exp_dud_pass],
        ),
    ]
    db = _StubDB(skill_tenant_id=uuid.uuid4(), runs=runs)
    bench = aggregate.aggregate_iteration(
        db, skill_id=skill_id, iteration=1, skill_slug="x",
    )
    notes = analyzer.analyze(
        db, skill_id=skill_id, iteration=1, benchmark=bench,
    )
    # At least one note about non-discriminating e-dud
    assert any("Non-discriminating" in n and "e-dud" in n for n in notes), (
        f"expected a non-discriminating note for e-dud; got: {notes!r}"
    )
    # And NOT one for e-signal (it discriminates)
    assert not any("Non-discriminating" in n and "e-signal" in n for n in notes)


def test_analyzer_flags_negative_pass_rate_delta():
    """Skill makes the baseline worse → negative delta note."""
    skill_id = uuid.uuid4()
    eA = str(uuid.uuid4())
    runs = [
        _mk_run(eval_id=eA, with_skill=True, timing_ms=100, tokens=50, score=0.0),
        _mk_run(eval_id=eA, with_skill=False, timing_ms=100, tokens=50, score=1.0),
    ]
    db = _StubDB(skill_tenant_id=uuid.uuid4(), runs=runs)
    bench = aggregate.aggregate_iteration(
        db, skill_id=skill_id, iteration=1, skill_slug="x",
    )
    notes = analyzer.analyze(
        db, skill_id=skill_id, iteration=1, benchmark=bench,
    )
    assert any("Negative pass_rate" in n for n in notes)


def test_analyzer_flags_flaky_eval():
    """Same eval, same leg, both passed and failed across re-runs."""
    skill_id = uuid.uuid4()
    eA = str(uuid.uuid4())
    runs = [
        _mk_run(eval_id=eA, with_skill=True, timing_ms=100, tokens=50, score=1.0),
        _mk_run(eval_id=eA, with_skill=True, timing_ms=110, tokens=55, score=0.0),
        _mk_run(eval_id=eA, with_skill=False, timing_ms=80, tokens=40, score=0.0),
    ]
    db = _StubDB(skill_tenant_id=uuid.uuid4(), runs=runs)
    bench = aggregate.aggregate_iteration(
        db, skill_id=skill_id, iteration=1, skill_slug="x",
    )
    notes = analyzer.analyze(
        db, skill_id=skill_id, iteration=1, benchmark=bench,
    )
    assert any("Flaky eval" in n for n in notes), (
        f"expected a flaky-eval note; got: {notes!r}"
    )


def test_analyzer_flags_time_token_tradeoff():
    """Faster but more tokens → tradeoff note."""
    skill_id = uuid.uuid4()
    eA = str(uuid.uuid4())
    runs = [
        # with_skill: faster (50ms), more tokens (200)
        _mk_run(eval_id=eA, with_skill=True, timing_ms=50, tokens=200, score=1.0),
        # without_skill: slower (200ms), fewer tokens (50)
        _mk_run(eval_id=eA, with_skill=False, timing_ms=200, tokens=50, score=1.0),
    ]
    db = _StubDB(skill_tenant_id=uuid.uuid4(), runs=runs)
    bench = aggregate.aggregate_iteration(
        db, skill_id=skill_id, iteration=1, skill_slug="x",
    )
    notes = analyzer.analyze(
        db, skill_id=skill_id, iteration=1, benchmark=bench,
    )
    assert any("Tradeoff" in n for n in notes), (
        f"expected a tradeoff note; got: {notes!r}"
    )


def test_analyzer_empty_when_no_signals():
    """Skill obviously helping AND tight stddev AND discriminating
    expectations → no notes."""
    skill_id = uuid.uuid4()
    eA = str(uuid.uuid4())
    # All four runs same timing/tokens; only pass_rate differs.
    runs = [
        _mk_run(eval_id=eA, with_skill=True, timing_ms=100, tokens=50, score=1.0,
                expectations=[{"id": "e1", "description": "x", "passed": True}]),
        _mk_run(eval_id=eA, with_skill=False, timing_ms=100, tokens=50, score=0.0,
                expectations=[{"id": "e1", "description": "x", "passed": False}]),
    ]
    db = _StubDB(skill_tenant_id=uuid.uuid4(), runs=runs)
    bench = aggregate.aggregate_iteration(
        db, skill_id=skill_id, iteration=1, skill_slug="x",
    )
    notes = analyzer.analyze(
        db, skill_id=skill_id, iteration=1, benchmark=bench,
    )
    # No tradeoff, no flaky, no non-discriminating, big positive delta → empty
    assert notes == [], f"expected no notes; got: {notes!r}"


# ──────────────────────────────────────────────────────────────────────────
# Endpoint — GET /api/v1/skills/{skill_id}/evals/iterations/{N}/benchmark
# ──────────────────────────────────────────────────────────────────────────


def test_benchmark_endpoint_happy_path():
    tenant_id = uuid.uuid4()
    skill_id = uuid.uuid4()
    eA = str(uuid.uuid4())
    runs = [
        _mk_run(eval_id=eA, with_skill=True, timing_ms=100, tokens=50, score=1.0),
        _mk_run(eval_id=eA, with_skill=False, timing_ms=80, tokens=40, score=0.0),
    ]
    user = _user(tenant_id=tenant_id)
    db = _StubDB(skill_tenant_id=tenant_id, runs=runs)
    client = _build_client(user, db)

    resp = client.get(
        f"/api/v1/skills/{skill_id}/evals/iterations/1/benchmark"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "benchmark" in body
    assert "notes" in body
    assert isinstance(body["notes"], list)
    bench = body["benchmark"]
    assert bench["iteration"] == 1
    assert bench["eval_count"] == 1
    assert bench["run_summary"]["delta"]["pass_rate"]["mean"] == 1.0


def test_benchmark_endpoint_empty_iteration_404():
    tenant_id = uuid.uuid4()
    skill_id = uuid.uuid4()
    user = _user(tenant_id=tenant_id)
    db = _StubDB(skill_tenant_id=tenant_id, runs=[])
    client = _build_client(user, db)

    resp = client.get(
        f"/api/v1/skills/{skill_id}/evals/iterations/1/benchmark"
    )
    assert resp.status_code == 404


def test_benchmark_endpoint_foreign_tenant_404():
    """Foreign tenant must 404 (not 403), matching #563 / #579 pattern."""
    user = _user()
    foreign_tenant = uuid.uuid4()
    db = _StubDB(skill_tenant_id=foreign_tenant, runs=[])
    client = _build_client(user, db)

    resp = client.get(
        f"/api/v1/skills/{uuid.uuid4()}/evals/iterations/1/benchmark"
    )
    assert resp.status_code == 404


def test_benchmark_endpoint_unknown_skill_404():
    user = _user()
    db = _StubDB(skill_tenant_id=None, runs=[])
    client = _build_client(user, db)

    resp = client.get(
        f"/api/v1/skills/{uuid.uuid4()}/evals/iterations/1/benchmark"
    )
    assert resp.status_code == 404


def test_benchmark_endpoint_bad_iteration_400():
    tenant_id = uuid.uuid4()
    user = _user(tenant_id=tenant_id)
    db = _StubDB(skill_tenant_id=tenant_id, runs=[])
    client = _build_client(user, db)

    resp = client.get(
        f"/api/v1/skills/{uuid.uuid4()}/evals/iterations/0/benchmark"
    )
    assert resp.status_code == 400


def test_benchmark_endpoint_analyzer_crash_does_not_500(monkeypatch):
    """Analyzer is best-effort — a crash must NOT 500 the endpoint.

    Verifies the try/except wrapper around analyzer_module.analyze in
    the endpoint handler returns the benchmark with notes=[] instead.
    """
    tenant_id = uuid.uuid4()
    skill_id = uuid.uuid4()
    eA = str(uuid.uuid4())
    runs = [
        _mk_run(eval_id=eA, with_skill=True, timing_ms=100, tokens=50, score=1.0),
        _mk_run(eval_id=eA, with_skill=False, timing_ms=80, tokens=40, score=0.0),
    ]
    user = _user(tenant_id=tenant_id)
    db = _StubDB(skill_tenant_id=tenant_id, runs=runs)
    client = _build_client(user, db)

    def _boom(*args, **kwargs):
        raise RuntimeError("analyzer goes boom")

    monkeypatch.setattr(
        skill_evals_module.analyzer_module, "analyze", _boom,
    )

    resp = client.get(
        f"/api/v1/skills/{skill_id}/evals/iterations/1/benchmark"
    )
    assert resp.status_code == 200
    assert resp.json()["notes"] == []
