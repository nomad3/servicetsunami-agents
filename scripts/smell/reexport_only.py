"""§3.2 AI-slop check — modules that exist only to re-export.

Flags `*_service.py`, `*_manager.py`, `*_client.py` under apps/api/app/services/
whose module body contains only imports / `__all__` assignments / docstring.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from scripts.smell._findings import CommandRecord, Finding, Preflight, emit

SERVICES_DIR = Path("apps/api/app/services")
SUFFIXES = ("_service.py", "_manager.py", "_client.py")


def is_reexport_only(tree: ast.Module) -> bool:
    if not tree.body:
        return False
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            # module docstring
            continue
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if targets == ["__all__"]:
                continue
        # any other top-level node disqualifies
        return False
    return True


def main() -> int:
    pre = Preflight(input_set=str(SERVICES_DIR))
    if not SERVICES_DIR.exists():
        pre.exit_summary = "degraded"
        emit(pre, [], method_notes="services dir missing")
        return 0

    findings: list[Finding] = []
    n = 0
    scanned = 0
    for py in sorted(SERVICES_DIR.iterdir()):
        if not py.is_file() or not any(py.name.endswith(s) for s in SUFFIXES):
            continue
        scanned += 1
        try:
            tree = ast.parse(py.read_text(), filename=str(py))
        except SyntaxError:
            continue
        if is_reexport_only(tree):
            n += 1
            findings.append(Finding(
                id=f"F2.reexport.{n}",
                title=f"re-export-only wrapper: {py.name}",
                where=str(py),
                evidence="module body contains only imports / docstring / __all__ — no logic",
                reproducer="python3 scripts/smell/reexport_only.py",
                why_it_smells="indirection layer with zero behavior — replace callers with direct imports",
                suggested_action="delete",
                effort="S",
                risk="low",
                blast_radius="small",
            ))
    pre.commands_attempted.append(CommandRecord(cmd=f"ast-scan {SERVICES_DIR} for re-export-only modules", exit=0, lines=scanned))
    emit(pre, findings, method_notes=f"scanned {scanned} *_service/_manager/_client.py files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
