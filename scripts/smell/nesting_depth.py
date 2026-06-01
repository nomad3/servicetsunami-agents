"""§3.5 refactor-hotspot check — top-N functions by AST nesting depth.

Walks every .py under the given roots (default: services + workflows), computes
max nesting depth of If/For/While/With/Try/AsyncFor/AsyncWith, and emits the
top-30 as Findings.
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from scripts.smell._findings import CommandRecord, Finding, Preflight, emit

NESTING_TYPES = (ast.If, ast.For, ast.While, ast.With, ast.Try, ast.AsyncFor, ast.AsyncWith)


def max_depth(node: ast.AST, current: int = 0) -> int:
    best = current
    for child in ast.iter_child_nodes(node):
        if isinstance(child, NESTING_TYPES):
            best = max(best, max_depth(child, current + 1))
        else:
            best = max(best, max_depth(child, current))
    return best


def func_loc(node: ast.AST) -> int:
    if hasattr(node, "end_lineno") and node.end_lineno:
        return node.end_lineno - node.lineno + 1
    return 0


def scan(roots: list[Path], pre: Preflight) -> list[Finding]:
    candidates: list[tuple[int, int, Path, int, str]] = []  # (depth, loc, file, lineno, name)
    for root in roots:
        if not root.exists():
            continue
        for py in root.rglob("*.py"):
            s = str(py)
            if any(x in s for x in ("/.venv/", "/__pycache__/", "/tests/", "/migrations/")):
                continue
            try:
                tree = ast.parse(py.read_text(errors="replace"), filename=str(py))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                d = max_depth(node)
                if d < 3:
                    continue  # only worth flagging from depth 3 up
                candidates.append((d, func_loc(node), py, node.lineno, node.name))
    candidates.sort(key=lambda t: (-t[0], -t[1]))
    pre.commands_attempted.append(CommandRecord(cmd=f"ast-scan {roots} for nesting", exit=0, lines=len(candidates)))

    findings: list[Finding] = []
    for i, (depth, loc, py, lineno, name) in enumerate(candidates[:30], 1):
        risk = "high" if depth >= 6 else "med" if depth >= 4 else "low"
        blast = "large" if loc >= 200 else "medium" if loc >= 80 else "small"
        findings.append(Finding(
            id=f"F5.nest.{i}",
            title=f"deeply-nested function: {name} (depth={depth}, LOC={loc})",
            where=f"{py}:{lineno}",
            evidence=f"max nesting depth = {depth}; function LOC = {loc}",
            reproducer=f"python3 scripts/smell/nesting_depth.py {' '.join(str(r) for r in roots)}",
            why_it_smells="cyclomatic depth this high is hard to read, test, and safely change",
            suggested_action="refactor",
            effort="M" if loc < 200 else "L",
            risk=risk,
            blast_radius=blast,
        ))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("roots", nargs="*", default=["apps/api/app/services", "apps/api/app/workflows"])
    args = parser.parse_args()
    roots = [Path(r) for r in args.roots]
    pre = Preflight(input_set=", ".join(str(r) for r in roots))
    findings = scan(roots, pre)
    emit(pre, findings, method_notes=f"top {len(findings)} by depth (≥3)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
