"""§3.1 dead-code check — Temporal workflow classes not registered in any worker.

A workflow class is any class decorated with `@workflow.defn` (Temporal Python SDK).
It is "registered" if its name appears in a `workflows=[...]` keyword in any of:
- apps/api/app/workers/*.py
- apps/code-worker/*.py
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from scripts.smell._findings import CommandRecord, Finding, Preflight, emit

WORKFLOW_DIRS = [Path("apps/api/app/workflows"), Path("apps/code-worker")]
WORKER_GLOBS = ["apps/api/app/workers/*.py", "apps/code-worker/*.py"]


def collect_workflow_classes() -> list[tuple[Path, int, str]]:
    out: list[tuple[Path, int, str]] = []
    for d in WORKFLOW_DIRS:
        if not d.exists():
            continue
        for py in sorted(d.rglob("*.py")):
            try:
                tree = ast.parse(py.read_text(), filename=str(py))
            except SyntaxError:
                continue
            for node in tree.body:
                if not isinstance(node, ast.ClassDef):
                    continue
                for deco in node.decorator_list:
                    # @workflow.defn or @workflow.defn(name="...")
                    s = ast.unparse(deco) if hasattr(ast, "unparse") else ""
                    if "workflow.defn" in s or s.endswith(".defn"):
                        out.append((py, node.lineno, node.name))
                        break
    return out


def collect_registered_names(pre: Preflight) -> set[str]:
    names: set[str] = set()
    for pattern in WORKER_GLOBS:
        r = subprocess.run(["bash", "-c", f"grep -rn 'workflows\\s*=\\s*\\[' {pattern} || true"],
                           capture_output=True, text=True)
        pre.commands_attempted.append(CommandRecord(cmd=f"grep workflows=[ in {pattern}", exit=r.returncode, lines=len(r.stdout.splitlines())))
        # Also grab a wider window: workflows=[ ... ]
        files_with_hits = sorted({line.split(":")[0] for line in r.stdout.splitlines() if line})
        for f in files_with_hits:
            try:
                text = Path(f).read_text()
            except FileNotFoundError:
                continue
            # Find each workflows=[…] block (greedy across newlines)
            for m in re.finditer(r"workflows\s*=\s*\[(.*?)\]", text, re.DOTALL):
                block = m.group(1)
                # Identifiers are simple words
                for ident in re.findall(r"\b([A-Z][A-Za-z0-9_]+)\b", block):
                    names.add(ident)
    return names


def main() -> int:
    pre = Preflight(input_set="apps/api/app/workflows + apps/code-worker")
    classes = collect_workflow_classes()
    registered = collect_registered_names(pre)

    findings: list[Finding] = []
    n = 0
    for py, lineno, name in classes:
        if name in registered:
            continue
        n += 1
        findings.append(Finding(
            id=f"F1.workflow.{n}",
            title=f"unregistered workflow: {name}",
            where=f"{py}:{lineno}",
            evidence=f"class '{name}' has @workflow.defn but no worker lists it in workflows=[…]",
            reproducer="python3 scripts/smell/unregistered_workflows.py",
            why_it_smells="workflow exists in code but no Temporal worker will ever pick it up",
            suggested_action="delete",
            effort="S",
            risk="med",
            blast_radius="medium",
        ))

    emit(pre, findings, method_notes=f"{len(classes)} workflow classes; {len(registered)} registered names found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
