"""§3.1 migration ↔ DB drift check with explicit preflight.

Diffs the set of migration filenames recorded in the `_migrations` table against
the set of `apps/api/migrations/*.sql` files. Reports both sides of the drift
(applied-but-file-missing and file-but-not-applied). If the DB container is not
reachable, emits preflight.exit_summary='degraded' so the Phase-2 fail-loud rule
trips correctly instead of swallowing silently.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from scripts.smell._findings import CommandRecord, Finding, Preflight, emit

MIGRATIONS_DIR = Path("apps/api/migrations")
DEFAULT_CONTAINER = "agentprovision-agents-db-1"
DEFAULT_DB = "agentprovision"


def container_running(name: str, pre: Preflight) -> bool:
    try:
        r = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pre.commands_attempted.append(CommandRecord(cmd="docker ps", exit=127))
        return False
    pre.commands_attempted.append(CommandRecord(cmd="docker ps --format '{{.Names}}'", exit=r.returncode, lines=len(r.stdout.splitlines())))
    pre.containers_seen = sorted(set(r.stdout.split()))
    return name in pre.containers_seen


def fetch_applied(container: str, db: str, pre: Preflight) -> list[str] | None:
    cmd = ["docker", "exec", container, "psql", "-U", "postgres", db, "-t", "-A", "-c",
           "SELECT filename FROM _migrations ORDER BY filename;"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        pre.commands_attempted.append(CommandRecord(cmd=" ".join(cmd), exit=124))
        return None
    pre.commands_attempted.append(CommandRecord(cmd=" ".join(cmd), exit=r.returncode, lines=len(r.stdout.splitlines())))
    if r.returncode != 0:
        return None
    return sorted(line.strip() for line in r.stdout.splitlines() if line.strip())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--container", default=DEFAULT_CONTAINER)
    parser.add_argument("--db", default=DEFAULT_DB)
    args = parser.parse_args()

    pre = Preflight(input_set=f"{args.container}:_migrations vs {MIGRATIONS_DIR}/*.sql")

    if not MIGRATIONS_DIR.exists():
        pre.exit_summary = "degraded"
        emit(pre, [], method_notes="migrations dir missing")
        return 0

    files_on_disk = sorted(p.name for p in MIGRATIONS_DIR.glob("*.sql"))
    pre.commands_attempted.append(CommandRecord(
        cmd=f"ls {MIGRATIONS_DIR}/*.sql | xargs -n1 basename | sort",
        exit=0, lines=len(files_on_disk),
    ))

    findings: list[Finding] = []

    if not container_running(args.container, pre):
        pre.exit_summary = "degraded"
        emit(pre, findings, method_notes=f"container '{args.container}' not running; DB side of diff skipped")
        return 0

    applied = fetch_applied(args.container, args.db, pre)
    if applied is None:
        pre.exit_summary = "degraded"
        emit(pre, findings, method_notes="psql query failed (no _migrations table, auth error, or other)")
        return 0

    applied_set = set(applied)
    disk_set = set(files_on_disk)

    n = 0
    for missing_file in sorted(applied_set - disk_set):
        n += 1
        findings.append(Finding(
            id=f"F1.migdrift.{n}",
            title=f"applied migration has no file: {missing_file}",
            where=f"_migrations row → no apps/api/migrations/{missing_file}",
            evidence=f"{missing_file} listed in _migrations but absent from {MIGRATIONS_DIR}",
            reproducer=f"docker exec {args.container} psql -U postgres {args.db} -c \"SELECT filename FROM _migrations WHERE filename='{missing_file}';\"",
            why_it_smells="schema migration applied to DB but its SQL is gone from the repo → reproducibility broken",
            suggested_action="document",
            effort="S",
            risk="med",
            blast_radius="medium",
        ))
    for missing_apply in sorted(disk_set - applied_set):
        n += 1
        findings.append(Finding(
            id=f"F1.migdrift.{n}",
            title=f"file not applied: {missing_apply}",
            where=f"{MIGRATIONS_DIR}/{missing_apply}",
            evidence=f"file exists on disk but no _migrations row recorded",
            reproducer=f"ls {MIGRATIONS_DIR}/{missing_apply} && docker exec {args.container} psql -U postgres {args.db} -c \"SELECT 1 FROM _migrations WHERE filename='{missing_apply}';\"",
            why_it_smells="migration .sql file in repo never applied — either dead/abandoned or missed in deploy",
            suggested_action="document",
            effort="S",
            risk="med",
            blast_radius="medium",
        ))

    emit(pre, findings, method_notes=f"{len(files_on_disk)} on disk, {len(applied)} applied; {len(findings)} drifts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
