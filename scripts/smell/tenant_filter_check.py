"""§3.3 pattern-drift check — multi-tenant queries missing `.filter(...tenant_id...)`.

Builds the set of tenant-scoped model class names from `apps/api/app/models/*.py`
(any class whose `__tablename__` is associated with a `tenant_id` Column). Then
scans `apps/api/app/services/` for `db.query(<Model>)` chains and flags any
chain that returns/yields without a `.filter(...tenant_id...)` call.
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from scripts.smell._findings import CommandRecord, Finding, Preflight, emit

MODELS_DIR = Path("apps/api/app/models")
TENANT_PATTERN = re.compile(r"tenant_id")


def tenanted_models() -> set[str]:
    out: set[str] = set()
    if not MODELS_DIR.exists():
        return out
    for py in MODELS_DIR.rglob("*.py"):
        try:
            src = py.read_text(errors="replace")
            tree = ast.parse(src, filename=str(py))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            body_src = ast.unparse(node) if hasattr(ast, "unparse") else ""
            if "tenant_id" in body_src and "Column" in body_src:
                out.add(node.name)
    return out


def scan_services(root: Path, models: set[str], pre: Preflight) -> list[Finding]:
    findings: list[Finding] = []
    n = 0
    if not root.exists():
        return findings
    for py in root.rglob("*.py"):
        s = str(py)
        if any(x in s for x in ("/.venv/", "/__pycache__/", "/tests/")):
            continue
        try:
            src = py.read_text(errors="replace")
            tree = ast.parse(src, filename=str(py))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            # Match db.query(Model) or session.query(Model)
            fn = node.func
            if not (isinstance(fn, ast.Attribute) and fn.attr == "query"):
                continue
            if not node.args:
                continue
            first = node.args[0]
            mname = first.id if isinstance(first, ast.Name) else None
            if not mname or mname not in models:
                continue
            # Walk the parent chain — find the enclosing Expression/Call chain and check for .filter(...tenant_id...)
            # Heuristic: look at next ~5 lines of source for a tenant_id mention.
            line = node.lineno
            window = "\n".join(src.splitlines()[line - 1 : line + 5])
            if TENANT_PATTERN.search(window):
                continue
            n += 1
            findings.append(Finding(
                id=f"F3.tenant.{n}",
                title=f"db.query({mname}) without tenant filter",
                where=f"{py}:{line}",
                evidence=f"query of tenanted model '{mname}' has no tenant_id mention in following 5 lines",
                reproducer="python3 scripts/smell/tenant_filter_check.py",
                why_it_smells="multi-tenant isolation violation — cross-tenant data leak risk",
                suggested_action="refactor",
                effort="S",
                risk="high",
                blast_radius="large",
            ))
    pre.commands_attempted.append(CommandRecord(cmd=f"ast-scan {root} for db.query(Tenanted)", exit=0, lines=n))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", default="apps/api/app/services")
    args = parser.parse_args()
    pre = Preflight(input_set=f"{MODELS_DIR} → tenant models; {args.root} → queries")
    models = tenanted_models()
    pre.commands_attempted.append(CommandRecord(cmd=f"ast-scan {MODELS_DIR} for tenant_id models", exit=0, lines=len(models)))
    findings = scan_services(Path(args.root), models, pre)
    emit(pre, findings, method_notes=f"{len(models)} tenanted models; {len(findings)} potential leaks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
