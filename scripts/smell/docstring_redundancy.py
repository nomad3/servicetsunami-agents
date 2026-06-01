"""§3.2 AI-slop check — docstrings that restate the function/class name.

Flags any function/class whose first docstring sentence equals (case-insensitive,
punctuation-stripped) the symbol name, or trivially restates it in prose
("Sends a message that sends a message"-style patterns).
"""
from __future__ import annotations

import argparse
import ast
import re
import string
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from scripts.smell._findings import CommandRecord, Finding, Preflight, emit


def first_sentence(s: str) -> str:
    s = s.strip()
    # Take up to the first period, or the whole thing
    m = re.split(r"\.\s|\n\n", s, maxsplit=1)
    return m[0].strip()


def normalize(s: str) -> str:
    return s.lower().translate(str.maketrans("", "", string.punctuation)).strip()


def name_to_phrase(name: str) -> str:
    """`send_chat_message` -> `send chat message`."""
    return name.replace("_", " ").lower()


def is_redundant(name: str, doc: str) -> bool:
    if not doc:
        return False
    sent = first_sentence(doc)
    sn = normalize(sent)
    if not sn:
        return False
    phrase = name_to_phrase(name)
    # Exact match: "send_chat_message" doc is literally "Send chat message."
    if sn == phrase:
        return True
    # Tight restatement: doc is just the name in code or as `<name>()`
    if sn == name.lower() or sn == name.lower() + "":
        return True
    if sn.startswith(phrase) and len(sn) <= len(phrase) + 5:
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
            if any(x in s for x in ("/.venv/", "/__pycache__/", "/node_modules/", "/migrations/", "/tests/")):
                continue
            try:
                tree = ast.parse(py.read_text(errors="replace"), filename=str(py))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                doc = ast.get_docstring(node) or ""
                if is_redundant(node.name, doc):
                    n += 1
                    findings.append(Finding(
                        id=f"F2.docredun.{n}",
                        title=f"redundant docstring: {node.name}",
                        where=f"{py}:{node.lineno}",
                        evidence=f"docstring restates symbol name: {first_sentence(doc)!r}",
                        reproducer=f"python3 scripts/smell/docstring_redundancy.py {root}",
                        why_it_smells="docstring adds zero information beyond the symbol name",
                        suggested_action="refactor",
                        effort="S",
                        risk="low",
                        blast_radius="small",
                    ))
    pre.commands_attempted.append(CommandRecord(cmd=f"ast-scan {roots}", exit=0, lines=n))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("roots", nargs="*", default=["apps/api/app/services"])
    args = parser.parse_args()
    roots = [Path(r) for r in args.roots]
    pre = Preflight(input_set=", ".join(str(r) for r in roots))
    findings = scan(roots, pre)
    emit(pre, findings, method_notes=f"flagged {len(findings)} redundant docstrings")
    return 0


if __name__ == "__main__":
    sys.exit(main())
