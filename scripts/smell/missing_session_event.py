"""§3.3 pattern-drift check — functions that commit DB writes without publishing a session event.

For each function in `apps/api/app/services/` and `apps/api/app/workflows/`:
- detect a session write (`.commit()`, `.add(...)`, `.delete(...)`, `.merge(...)`)
- detect a `publish_session_event(...)` call within the same function (or
  anywhere else in the same module — module-level fan-out helpers are common)
- if write present + no publish in the function and no publish in the module → finding.
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from scripts.smell._findings import CommandRecord, Finding, Preflight, emit

WRITE_METHODS = {"commit", "add", "delete", "merge", "bulk_save_objects", "execute"}
EVENT_FN = "publish_session_event"


def has_call(node: ast.AST, name: str) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            fn = sub.func
            if isinstance(fn, ast.Name) and fn.id == name:
                return True
            if isinstance(fn, ast.Attribute) and fn.attr == name:
                return True
    return False


def has_write(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
            if sub.func.attr in WRITE_METHODS:
                # Heuristic: callee receiver name suggests session/db
                recv = sub.func.value
                recv_name = ""
                if isinstance(recv, ast.Name):
                    recv_name = recv.id
                elif isinstance(recv, ast.Attribute):
                    recv_name = recv.attr
                if recv_name in {"db", "session", "self", "_session", "_db"} or "session" in recv_name.lower():
                    return True
    return False


def scan(roots: list[Path], pre: Preflight) -> list[Finding]:
    findings: list[Finding] = []
    n = 0
    for root in roots:
        if not root.exists():
            continue
        for py in root.rglob("*.py"):
            s = str(py)
            if any(x in s for x in ("/.venv/", "/__pycache__/", "/tests/")):
                continue
            try:
                src = py.read_text(errors="replace")
                tree = ast.parse(src, filename=str(py))
            except SyntaxError:
                continue
            module_has_event = EVENT_FN in src
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if not has_write(node):
                    continue
                if has_call(node, EVENT_FN):
                    continue
                if module_has_event:
                    # benefit of the doubt — another fn in same module emits; downgrade to nit
                    continue
                n += 1
                findings.append(Finding(
                    id=f"F3.sessev.{n}",
                    title=f"DB write without publish_session_event: {node.name}",
                    where=f"{py}:{node.lineno}",
                    evidence=f"function calls a session write ({sorted(WRITE_METHODS)} on db/session) but no publish_session_event in the function or module",
                    reproducer=f"python3 scripts/smell/missing_session_event.py {root}",
                    why_it_smells="state-mutating action invisible to human/agent watchers — breaks Alpha CLI kernel §4",
                    suggested_action="refactor",
                    effort="M",
                    risk="med",
                    blast_radius="medium",
                ))
    pre.commands_attempted.append(CommandRecord(cmd=f"ast-scan {roots}", exit=0, lines=n))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("roots", nargs="*", default=["apps/api/app/services", "apps/api/app/workflows"])
    args = parser.parse_args()
    roots = [Path(r) for r in args.roots]
    pre = Preflight(input_set=", ".join(str(r) for r in roots))
    findings = scan(roots, pre)
    emit(pre, findings, method_notes=f"flagged {len(findings)} writes without session event")
    return 0


if __name__ == "__main__":
    sys.exit(main())
