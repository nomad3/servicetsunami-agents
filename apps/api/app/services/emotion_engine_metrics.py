"""Prometheus instrumentation for the emotion engine.

Closes audit item #6 from
`docs/plans/2026-05-20-weekly-plans-implementation-audit-and-tech-debt.md`:

    > Add emotion-engine observability — Prometheus counters for
    > appraise-clamp and decay convergence. Needed to tune per-tenant
    > RLCF constants in Phase 3 without blind guessing.

Design choices:

- The pure-functional `emotion_engine` module stays unmodified.
  Instrumentation lives at the IO boundary (`emotion_engine_io`)
  where appraisal results flow into persistence — emitting metrics
  there preserves the pure functions' testability and avoids global
  side effects in the math layer.

- Counters use `tenant_id` labels (string UUIDs). Cardinality is
  bounded by active-tenant count (low). If we ever need per-agent
  metrics, add `agent_id` as a second label, but only after we've
  confirmed the live cardinality. NEVER label with raw PAD values —
  those are unbounded float space.

- The module is import-safe: if `prometheus_client` is unavailable
  (the platform's metrics endpoint already shields against this via
  a 503 in `apps/api/app/api/v1/metrics.py:13-15`), the counters fall
  back to no-op stubs so the chat hot path never crashes on a
  missing-dep edge case.
"""
from __future__ import annotations

from typing import Optional

try:
    from prometheus_client import Counter, Histogram

    _PROMETHEUS_AVAILABLE = True
except ImportError:  # pragma: no cover — graceful degradation
    _PROMETHEUS_AVAILABLE = False

    class _NoOp:
        """No-op stand-in for prometheus_client.Counter/Histogram when
        the library isn't installed. Keeps call sites identical."""

        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def labels(self, *_args, **_kwargs) -> "_NoOp":
            return self

        def inc(self, *_args, **_kwargs) -> None:
            pass

        def observe(self, *_args, **_kwargs) -> None:
            pass

    Counter = _NoOp  # type: ignore[assignment,misc]
    Histogram = _NoOp  # type: ignore[assignment,misc]


# ── Metric definitions ────────────────────────────────────────────────


# Counts how often appraise_event's output clamps a PAD axis to its
# bound (-1 or +1). High counts on a specific axis suggest the
# tuning constants for that axis are too aggressive — Phase 3 RLCF
# can use this signal to learn per-tenant offsets.
emotion_appraise_clamp_events = Counter(
    "emotion_appraise_clamp_events_total",
    "Total PAD-axis clamps observed in emotion appraisal output, "
    "per-tenant per-axis. High counts indicate tuning constants "
    "may exceed the dynamic range for that axis.",
    labelnames=("tenant_id", "axis", "bound"),
)

# Counts each event-type passed through appraise_event. Useful for
# verifying appraisal volume per tenant. Phase 1.5 (Luna-approved
# 2026-05-20) introduced the user_signal event_type — it's now an
# expected non-zero label, not a regression signal.
emotion_appraise_events_total = Counter(
    "emotion_appraise_events_total",
    "Total appraisal events processed by the emotion engine, "
    "labelled by event type (tool_outcome, tool_failure, peer_signal, "
    "user_signal). Phase 1.5+: user_signal is bounded by the "
    "user_signal_classifier boundary + small gain constants — see "
    "emotion_engine.py module docstring.",
    labelnames=("tenant_id", "event_type"),
)

# Decay convergence: how many ticks until the magnitude of the
# difference from baseline is within 5% of zero. Lets operators
# verify the decay rate produces the design's "~6 ticks to ~70%
# recovery" curve in production.
emotion_decay_convergence_ticks = Histogram(
    "emotion_decay_convergence_ticks",
    "Number of decay ticks observed before the PAD vector returns "
    "within 5% of baseline on all three axes. Buckets align with "
    "the design doc's '6-tick target' invariant.",
    labelnames=("tenant_id",),
    buckets=(1, 2, 3, 4, 5, 6, 8, 10, 15, 20, 30, 60),
)

# Counts each successful write of an affect_vector to a
# conversation_episode (post-appraisal persistence). Companion to
# `emotion_appraise_events_total` — useful to detect silent IO
# failures (event count grows but write count stagnates).
emotion_affect_writes_total = Counter(
    "emotion_affect_writes_total",
    "Total successful writes of affect_vector to conversation_"
    "episodes. Diverging from emotion_appraise_events_total "
    "indicates silent IO drops.",
    labelnames=("tenant_id",),
)


# ── Helpers ───────────────────────────────────────────────────────────


def record_appraise_event(
    *,
    tenant_id: Optional[str],
    event_type: str,
) -> None:
    """Increment the event-type counter for this tenant.

    Best-effort: never raises. Missing tenant_id labels as "unknown"
    so the counter still ticks and the cardinality stays bounded.
    """
    label = tenant_id or "unknown"
    try:
        emotion_appraise_events_total.labels(
            tenant_id=label, event_type=event_type,
        ).inc()
    except Exception:  # noqa: BLE001 — metrics never crash the hot path
        pass


def record_clamp_events(
    *,
    tenant_id: Optional[str],
    pleasure: float,
    arousal: float,
    dominance: float,
    clamp_threshold: float = 0.999,
) -> None:
    """Inspect the output of an appraise_event call. For each axis
    that's pegged at +/- 1 (within clamp_threshold), bump the
    per-axis-per-bound counter.

    `clamp_threshold` defaults to 0.999 — values closer to the bound
    than 1e-3 count as clamped. This is generous enough to catch
    legitimate clamps without false positives from floating-point
    drift.
    """
    label = tenant_id or "unknown"
    for axis_name, value in (("pleasure", pleasure), ("arousal", arousal), ("dominance", dominance)):
        try:
            if value >= clamp_threshold:
                emotion_appraise_clamp_events.labels(
                    tenant_id=label, axis=axis_name, bound="upper",
                ).inc()
            elif value <= -clamp_threshold:
                emotion_appraise_clamp_events.labels(
                    tenant_id=label, axis=axis_name, bound="lower",
                ).inc()
        except Exception:  # noqa: BLE001
            pass


def record_decay_convergence(
    *,
    tenant_id: Optional[str],
    ticks: int,
) -> None:
    """Observe the number of decay ticks until convergence-within-5%.

    Caller is responsible for actually measuring the convergence; this
    helper just records the observation into the histogram.
    """
    label = tenant_id or "unknown"
    try:
        emotion_decay_convergence_ticks.labels(tenant_id=label).observe(ticks)
    except Exception:  # noqa: BLE001
        pass


def record_affect_write(
    *,
    tenant_id: Optional[str],
) -> None:
    """Bump the affect-write counter after a successful persistence."""
    label = tenant_id or "unknown"
    try:
        emotion_affect_writes_total.labels(tenant_id=label).inc()
    except Exception:  # noqa: BLE001
        pass


__all__ = [
    "emotion_appraise_clamp_events",
    "emotion_appraise_events_total",
    "emotion_decay_convergence_ticks",
    "emotion_affect_writes_total",
    "record_appraise_event",
    "record_clamp_events",
    "record_decay_convergence",
    "record_affect_write",
]
