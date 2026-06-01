"""§3.1 dead-code check — FastAPI route modules not imported by the router aggregator.

For every `apps/api/app/api/v1/*.py` (excluding __init__, routes, deps), check whether
its module stem appears in `apps/api/app/api/v1/routes.py` (or `__init__.py`) as part
of any `from … import` / `include_router(...)` statement.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from scripts.smell._findings import CommandRecord, Finding, Preflight, emit

ROUTES_DIR = Path("apps/api/app/api/v1")
ROUTES_FILE = ROUTES_DIR / "routes.py"
INIT_FILE = ROUTES_DIR / "__init__.py"
EXCLUDE = {"__init__", "routes", "deps"}


def main() -> int:
    pre = Preflight(input_set=str(ROUTES_DIR))
    findings: list[Finding] = []

    if not ROUTES_DIR.exists():
        pre.exit_summary = "degraded"
        pre.commands_attempted.append(CommandRecord(cmd=f"ls {ROUTES_DIR}", exit=2))
        emit(pre, findings, method_notes="routes dir missing")
        return 0

    aggregator_text = ""
    for f in (ROUTES_FILE, INIT_FILE):
        if f.exists():
            aggregator_text += f.read_text()
            pre.commands_attempted.append(CommandRecord(cmd=f"read {f}", exit=0, lines=aggregator_text.count("\n")))

    if not aggregator_text:
        pre.exit_summary = "degraded"
        emit(pre, findings, method_notes="no routes aggregator file found")
        return 0

    n = 0
    for py in sorted(ROUTES_DIR.glob("*.py")):
        stem = py.stem
        if stem in EXCLUDE:
            continue
        # Look for any reference to the module name in the aggregator file
        pattern = rf"\b{re.escape(stem)}\b"
        if not re.search(pattern, aggregator_text):
            n += 1
            findings.append(Finding(
                id=f"F1.unmounted.{n}",
                title=f"unmounted route module: {stem}",
                where=str(py),
                evidence=f"name '{stem}' not referenced in routes.py / __init__.py",
                reproducer="python3 scripts/smell/unmounted_routes.py",
                why_it_smells="route file exists but is never imported by the router aggregator → dead",
                suggested_action="delete",
                effort="S",
                risk="low",
                blast_radius="small",
            ))

    emit(pre, findings, method_notes=f"scanned {sum(1 for _ in ROUTES_DIR.glob('*.py'))} files in {ROUTES_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
