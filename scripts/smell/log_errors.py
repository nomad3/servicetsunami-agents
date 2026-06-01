"""§3.4 live-error capture — multi-format log fingerprint scanner.

Runs `docker logs --since <window> <container>` per --container, captures lines
matching any of three log formats the platform emits (JSON envelope, leading
timestamp + level, bare-prefix), normalizes the message into a fingerprint, and
returns the top-20 by occurrence count.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from scripts.smell._findings import CommandRecord, Finding, Preflight, emit

# Combined ERROR/WARNING grep across the three formats
ERR_RE = re.compile(
    r'("level":\s*"(ERROR|WARNING)")'
    r'|(^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.Z+-]+\s+(ERROR|WARN(ING)?)\b)'
    r'|(^(ERROR|WARN(ING)?):)'
)

# Token-strippers to make a fingerprint
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
HEX_RE = re.compile(r"\b[0-9a-f]{16,}\b", re.I)
TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}T?\d{2}:\d{2}:\d{2}[\.\d:Z+\-]*")
NUM_RE = re.compile(r"\b\d{4,}\b")
LINE_RE = re.compile(r"line \d+|:\d+:")


def fingerprint(line: str) -> str:
    s = line
    s = UUID_RE.sub("<uuid>", s)
    s = HEX_RE.sub("<hex>", s)
    s = TS_RE.sub("<ts>", s)
    s = LINE_RE.sub("line <n>", s)
    s = NUM_RE.sub("<n>", s)
    # collapse whitespace, trim
    s = " ".join(s.split())
    # keep first 220 chars
    return s[:220]


def list_containers(pre: Preflight) -> list[str]:
    try:
        r = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pre.commands_attempted.append(CommandRecord(cmd="docker ps", exit=127))
        return []
    pre.commands_attempted.append(CommandRecord(cmd="docker ps --format '{{.Names}}'", exit=r.returncode, lines=len(r.stdout.splitlines())))
    return sorted(set(r.stdout.split()))


def scan_container(container: str, since: str, pre: Preflight) -> list[tuple[str, int, str]]:
    """Return list of (fingerprint, count, sample_line) sorted desc by count, top-20."""
    cmd = ["docker", "logs", "--since", since, container]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pre.commands_attempted.append(CommandRecord(cmd=" ".join(cmd), exit=124))
        return []
    pre.commands_attempted.append(CommandRecord(cmd=" ".join(cmd), exit=r.returncode, lines=len(r.stdout.splitlines()) + len(r.stderr.splitlines())))

    merged = (r.stdout + "\n" + r.stderr).splitlines()
    matches = [ln for ln in merged if ERR_RE.search(ln)]
    counts: Counter[str] = Counter()
    samples: dict[str, str] = {}
    for ln in matches:
        fp = fingerprint(ln)
        counts[fp] += 1
        samples.setdefault(fp, ln)
    top = counts.most_common(20)
    return [(fp, c, samples[fp]) for fp, c in top]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default="72h")
    parser.add_argument("--container", action="append", default=[])
    args = parser.parse_args()

    pre = Preflight(input_set=f"--since {args.since}; --container {args.container}")
    pre.containers_seen = list_containers(pre)

    if not args.container:
        args.container = [c for c in pre.containers_seen if c.startswith("agentprovision-agents-")]

    findings: list[Finding] = []
    n = 0
    any_ok = False
    for c in args.container:
        if c not in pre.containers_seen:
            pre.commands_attempted.append(CommandRecord(cmd=f"missing container {c}", exit=2))
            continue
        any_ok = True
        for fp, count, sample in scan_container(c, args.since, pre):
            n += 1
            risk = "high" if count >= 50 else "med" if count >= 10 else "low"
            findings.append(Finding(
                id=f"F4.err.{n}",
                title=f"[{c}] ×{count}: {fp[:80]}",
                where=c,
                evidence=f"sample line: {sample[:200]}",
                reproducer=f"docker logs --since {args.since} {c} | grep -E '\"level\":\"(ERROR|WARNING)\"|^[0-9-]+T[0-9:.Z+-]+\\s+(ERROR|WARN(ING)?)\\b|^(ERROR|WARN(ING)?):' | head",
                why_it_smells=f"recurring error fingerprint ({count} occurrences) in {args.since} window",
                suggested_action="refactor",
                effort="M",
                risk=risk,
                blast_radius="medium",
            ))

    if not any_ok:
        pre.exit_summary = "degraded"

    emit(pre, findings, method_notes=f"scanned {len(args.container)} containers; {len(findings)} fingerprints")
    return 0


if __name__ == "__main__":
    sys.exit(main())
