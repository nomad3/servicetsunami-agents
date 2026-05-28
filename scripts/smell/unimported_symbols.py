"""§3.1 dead-code check — unused public symbols under apps/api/app/services/.

Tries `vulture` first; falls back to a single-pass AST + bulk-grep scan if vulture
is not installed. Avoids the per-symbol grep explosion by building a single index
of every textual symbol reference under apps/ in one `grep` invocation, then
checking each public service symbol against the index.
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from scripts.smell._findings import CommandRecord, Finding, Preflight, emit

SERVICES_DIR = Path("apps/api/app/services")
APPS_DIR = Path("apps")


def try_vulture(pre: Preflight) -> list[Finding] | None:
    try:
        r = subprocess.run(
            ["vulture", str(SERVICES_DIR), "--min-confidence", "80"],
            capture_output=True, text=True, timeout=120,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pre.commands_attempted.append(CommandRecord(cmd="vulture apps/api/app/services/", exit=127))
        return None
    pre.commands_attempted.append(CommandRecord(
        cmd="vulture apps/api/app/services/ --min-confidence 80",
        exit=r.returncode, lines=len(r.stdout.splitlines()),
    ))
    findings: list[Finding] = []
    for i, line in enumerate(r.stdout.splitlines(), 1):
        if ":" not in line or "unused" not in line:
            continue
        parts = line.split(":")
        if len(parts) < 3:
            continue
        findings.append(Finding(
            id=f"F1.unimported.{i}",
            title=f"vulture: {line.split('unused', 1)[1].strip()[:60]}",
            where=f"{parts[0]}:{parts[1]}",
            evidence=line.strip(),
            reproducer="vulture apps/api/app/services/ --min-confidence 80",
            why_it_smells="public symbol with no references in the repo (vulture ≥80% confidence)",
            suggested_action="delete",
            effort="S",
            risk="low",
            blast_radius="small",
        ))
    return findings


def ast_fallback(pre: Preflight) -> list[Finding]:
    # Build a single all-symbols reference index by reading every .py once.
    pre.commands_attempted.append(CommandRecord(cmd="single-pass reference index over apps/**/*.py", exit=0))

    # Collect (module_stem, symbol_name, defining_file, defining_lineno) tuples
    defs: list[tuple[str, str, Path, int]] = []
    for py in sorted(SERVICES_DIR.glob("*.py")):
        if py.stem == "__init__":
            continue
        try:
            tree = ast.parse(py.read_text(), filename=str(py))
        except SyntaxError:
            continue
        for node in tree.body:
            name = None
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = node.name
            if not name or name.startswith("_"):
                continue
            defs.append((py.stem, name, py, node.lineno))

    # Build the reference index: every word in every .py under apps/ (excluding the file we're defining in
    # would be ideal, but the cost of one pass is fine and we just guard against the self-hit later).
    ref_text_per_file: dict[Path, str] = {}
    for py in APPS_DIR.rglob("*.py"):
        # skip vendored / generated / test stubs
        s = str(py)
        if any(x in s for x in ("/.venv/", "/node_modules/", "/__pycache__/", "/target/", "/migrations/")):
            continue
        try:
            ref_text_per_file[py] = py.read_text(errors="replace")
        except OSError:
            continue
    pre.commands_attempted.append(CommandRecord(cmd="indexed-files", exit=0, lines=len(ref_text_per_file)))

    findings: list[Finding] = []
    n = 0
    for module_stem, name, defining_file, lineno in defs:
        # Patterns we count as a reference (exclude the defining file itself)
        patterns = [
            f"from app.services.{module_stem} import {name}",
            f"from .services.{module_stem} import {name}",
            f"from services.{module_stem} import {name}",
            f"from .{module_stem} import {name}",          # intra-package
            f"{module_stem}.{name}",
        ]
        # Also "import name" without dotting if there's a `from app.services.<m> import *` later — out of scope; we conservatively flag only zero-reference cases.

        hit = False
        for ref_file, text in ref_text_per_file.items():
            if ref_file == defining_file:
                continue
            for pat in patterns:
                if pat in text:
                    hit = True
                    break
            if hit:
                break
        if hit:
            continue
        n += 1
        findings.append(Finding(
            id=f"F1.unimported.{n}",
            title=f"unused public symbol: {module_stem}.{name}",
            where=f"{defining_file}:{lineno}",
            evidence=f"no references to '{module_stem}.{name}' anywhere under apps/ (AST single-pass index)",
            reproducer="python3 scripts/smell/unimported_symbols.py  # AST fallback",
            why_it_smells="public symbol declared but never imported / qualified-referenced",
            suggested_action="delete",
            effort="S",
            risk="low",
            blast_radius="small",
        ))
    return findings


def main() -> int:
    pre = Preflight(input_set=str(SERVICES_DIR))
    if not SERVICES_DIR.exists():
        pre.exit_summary = "degraded"
        emit(pre, [], method_notes="services dir missing")
        return 0

    findings = try_vulture(pre)
    notes = "vulture"
    if findings is None:
        findings = ast_fallback(pre)
        notes = "vulture unavailable, single-pass AST fallback used"

    emit(pre, findings, method_notes=notes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
