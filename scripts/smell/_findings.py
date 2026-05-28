"""Shared Finding / Preflight contract for all smell-report dimension scripts.

Every script under `scripts/smell/` emits exactly one JSON object on stdout via
`emit(...)` so the Phase-2 aggregator can parse one shape. See the implementation
plan at `docs/superpowers/plans/2026-05-28-core-primitives-smell-report-plan.md`
and the spec at `docs/superpowers/specs/2026-05-28-core-primitives-smell-report-design.md`
(§3 finding shape, §4 subagent JSON contract).
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from typing import Literal

Effort = Literal["S", "M", "L"]
Risk = Literal["low", "med", "high"]
BlastRadius = Literal["small", "medium", "large"]
Action = Literal["delete", "refactor", "document", "leave"]


@dataclass
class CommandRecord:
    cmd: str            # exact shell invocation
    exit: int           # process exit code
    lines: int = 0      # lines of output captured


@dataclass
class Finding:
    id: str
    title: str
    where: str
    evidence: str
    reproducer: str
    why_it_smells: str
    suggested_action: Action
    effort: Effort
    risk: Risk
    blast_radius: BlastRadius


@dataclass
class Preflight:
    # spec §4 contract field names
    commands_attempted: list = field(default_factory=list)   # list[CommandRecord]
    containers_seen: list = field(default_factory=list)      # list[str]
    input_set: str = ""
    # plan extension on top of spec §4 contract (used by Phase-2 fail-loud rule)
    exit_summary: str = "ok"  # "ok" | "degraded"


def emit(preflight: Preflight, findings: list[Finding], method_notes: str = "") -> None:
    """Write the single JSON object the aggregator expects, then a trailing newline."""
    json.dump(
        {
            "preflight": asdict(preflight),
            "findings": [asdict(f) for f in findings],
            "method_notes": method_notes,
        },
        sys.stdout,
        indent=2,
    )
    sys.stdout.write("\n")


if __name__ == "__main__":
    # Smoke: run with no args to emit an empty, well-formed envelope.
    emit(Preflight(input_set="smoke"), [], method_notes="self-smoke")
