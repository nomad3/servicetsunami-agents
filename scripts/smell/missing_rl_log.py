"""§3.3 pattern-drift check — autonomous-decision functions missing an rl_experience log.

Flags functions in `apps/api/app/services/` whose name matches a routing/decision
verb (route|select|dispatch|pick|choose|fallback) and that return a chosen value
without calling any `rl_experience_service.log_*` / `record_rl_experience` /
`log_rl_experience` / `RLExperience` insert.
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from scripts.smell._findings import CommandRecord, Finding, Preflight, emit

DECISION_VERB = re.compile(r"^(route|select|dispatch|pick|choose|fallback)_\w+$")
RL_HINTS = (
    "rl_experience_service.log",
    "record_rl_experience",
    "log_rl_experience",
    "RLExperience(",
    ".log_chat_response",
    ".log_routing_decision",
)


def function_text(src: str, node: ast.AST) -> str:
    start = node.lineno - 1
    end = node.end_lineno
    return "\n".join(src.splitlines()[start:end])


def scan(root: Path, pre: Preflight) -> list[Finding]:
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
        module_has_rl = any(h in src for h in RL_HINTS)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not DECISION_VERB.match(node.name):
                continue
            ftxt = function_text(src, node)
            if any(h in ftxt for h in RL_HINTS):
                continue
            if module_has_rl:
                continue  # module-level helper might log
            n += 1
            findings.append(Finding(
                id=f"F3.rllog.{n}",
                title=f"decision function without RL log: {node.name}",
                where=f"{py}:{node.lineno}",
                evidence=f"name matches decision-verb regex and no rl_experience log call found in the function or module",
                reproducer=f"python3 scripts/smell/missing_rl_log.py {root}",
                why_it_smells="autonomous decision goes un-tracked — breaks Alpha CLI kernel §5 RL discipline",
                suggested_action="refactor",
                effort="M",
                risk="med",
                blast_radius="medium",
            ))
    pre.commands_attempted.append(CommandRecord(cmd=f"ast-scan {root}", exit=0, lines=n))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", default="apps/api/app/services")
    args = parser.parse_args()
    root = Path(args.root)
    pre = Preflight(input_set=str(root))
    findings = scan(root, pre)
    emit(pre, findings, method_notes=f"flagged {len(findings)} decision functions without RL log")
    return 0


if __name__ == "__main__":
    sys.exit(main())
