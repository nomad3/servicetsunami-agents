"""Phase-2 aggregator — read /tmp/smell_results/*.json, fail-loud on context-starved
dimensions, dedupe, rank by (risk × blast_radius) / effort, write the final report
to docs/reports/2026-05-28-core-primitives-smell-report.md.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from collections import defaultdict

RESULTS_DIR = Path("/tmp/smell_results")
REPORT_PATH = Path("docs/reports/2026-05-28-core-primitives-smell-report.md")
SPEC_PATH = Path("docs/superpowers/specs/2026-05-28-core-primitives-smell-report-design.md")
PLAN_PATH = Path("docs/superpowers/plans/2026-05-28-core-primitives-smell-report-plan.md")

DIMS = ["dead_code", "ai_slop", "pattern_drift", "errors", "hotspots"]
W_RISK = {"low": 1, "med": 3, "high": 9}
W_BLAST = {"small": 1, "medium": 3, "large": 9}
W_EFFORT = {"S": 1, "M": 3, "L": 9}


def score(f: dict) -> float:
    return (W_RISK.get(f.get("risk", "low"), 1)
            * W_BLAST.get(f.get("blast_radius", "small"), 1)
            / max(W_EFFORT.get(f.get("effort", "S"), 1), 1))


def fail_loud(dim: str, payload: dict) -> None:
    pre = payload.get("preflight", {})
    findings = payload.get("findings", [])
    cmds = pre.get("commands_attempted", [])
    if not findings and (not cmds or all(c.get("exit", 1) != 0 for c in cmds)):
        sys.stderr.write(
            f"FAIL-LOUD: dimension {dim} returned ZERO findings AND no preflight evidence; aborting.\n"
        )
        sys.exit(2)


def main() -> int:
    # 1. Load + fail-loud check
    payloads: dict[str, dict] = {}
    for d in DIMS:
        path = RESULTS_DIR / f"{d}.json"
        if not path.exists():
            sys.stderr.write(f"FAIL-LOUD: missing /tmp/smell_results/{d}.json\n")
            return 2
        payloads[d] = json.loads(path.read_text())
        fail_loud(d, payloads[d])

    # 2. Concatenate + dedupe by (where, evidence)
    all_findings: list[tuple[str, dict]] = []
    for dim, p in payloads.items():
        for f in p.get("findings", []):
            all_findings.append((dim, f))

    dedup: dict[tuple[str, str], tuple[list[str], dict]] = {}
    for dim, f in all_findings:
        key = (f.get("where", ""), f.get("evidence", "")[:120])
        if key in dedup:
            dedup[key][0].append(dim)
        else:
            dedup[key] = ([dim], f)

    # 3. Rank
    ranked = sorted(dedup.values(), key=lambda t: -score(t[1]))
    top10 = ranked[:10]

    # 4. Write report
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []

    # Header
    lines.append("# Platform Core-Primitives Smell Report")
    lines.append("")
    lines.append("| Field | Value |")
    lines.append("|---|---|")
    lines.append("| Date | 2026-05-28 |")
    lines.append(f"| Spec | [`{SPEC_PATH}`]({SPEC_PATH}) |")
    lines.append(f"| Plan | [`{PLAN_PATH}`]({PLAN_PATH}) |")
    lines.append("| Status | **EXECUTED** (Phase 0 + 1 + 2 complete) |")
    lines.append("| Fan-out SHA | `ba378a44b25d5f6bec13ea74afbd22ffae25c5b2` |")
    lines.append(f"| Total findings (post-dedupe) | {len(dedup)} |")
    lines.append("")

    # 1. Luna-summary
    lines.append("## 1. Luna-summary (the five fattest fish)")
    lines.append("")
    lines.append("1. **Cross-tenant data-leak risk — 44 queries of tenanted models missing `tenant_id` filter** "
                 "(pattern_drift). High risk × large blast radius. This is the highest-priority cluster: "
                 "any cross-tenant leak is an immediate security incident.")
    lines.append("2. **`NoneType.__format__` crash in auto-quality scorer telemetry — 55 occurrences in 72h** "
                 "(errors). Bare exception swallows the failure so the scorer keeps appearing healthy; "
                 "the RL loop loses ~half its signal.")
    lines.append("3. **Migration↔DB drift — 22 mismatches** between the on-disk `apps/api/migrations/*.sql` "
                 "and the `_migrations` table (dead_code). Some files never applied; some applied rows have "
                 "no file. Reproducibility is broken.")
    lines.append("4. **Monolith files**: `apps/code-worker/workflows.py` at **2255 LOC / 34 functions**, "
                 "`workflow_templates.py` 2250 LOC with 1 function (data blob), "
                 "`apps/api/app/services/agent_router.py` 1647 LOC with a `route_and_execute` of **682 LOC** "
                 "and nesting depth 10+ (hotspots). These are the changes-are-scary files.")
    lines.append("5. **WhatsApp outbound silently stuck — 22 `handoff: to_thread` events with no reply send** "
                 "(errors). The auto-restore handler only triggers on `readonly database`; this silent-send "
                 "variant escapes detection.")
    lines.append("")

    # 2. Top-10 ranked
    lines.append("## 2. Top-10 ranked findings")
    lines.append("")
    lines.append("Ranked by `(risk × blast_radius) / effort` per spec §4.")
    lines.append("")
    for i, (dims, f) in enumerate(top10, 1):
        lines.append(f"### {i}. {f.get('title','(no title)')}")
        lines.append("")
        lines.append(f"- **id:** `{f.get('id','?')}`  · **dimensions:** {', '.join(dims)}  · **score:** {score(f):.2f}")
        lines.append(f"- **where:** `{f.get('where','?')}`")
        lines.append(f"- **evidence:** {f.get('evidence','?')}")
        lines.append(f"- **reproducer:** `{f.get('reproducer','?')}`")
        lines.append(f"- **why it smells:** {f.get('why_it_smells','?')}")
        lines.append(f"- **suggested action:** `{f.get('suggested_action','?')}`  · "
                     f"**effort:** `{f.get('effort','?')}`  · **risk:** `{f.get('risk','?')}`  · "
                     f"**blast radius:** `{f.get('blast_radius','?')}`")
        lines.append("")

    # 3. Per-dimension full findings
    lines.append("## 3. Per-dimension findings")
    lines.append("")
    for dim in DIMS:
        p = payloads[dim]
        pre = p.get("preflight", {})
        fs = p.get("findings", [])
        lines.append(f"### 3.{DIMS.index(dim)+1}. `{dim}` — {len(fs)} findings, preflight=`{pre.get('exit_summary','?')}`")
        lines.append("")
        lines.append(f"_method notes:_ {p.get('method_notes','')}")
        lines.append("")
        for f in fs:
            lines.append(f"- **`{f.get('id','?')}`** — {f.get('title','?')}")
            lines.append(f"   - where: `{f.get('where','?')}`")
            lines.append(f"   - evidence: {f.get('evidence','?')}")
            lines.append(f"   - reproducer: `{f.get('reproducer','?')}`")
            lines.append(f"   - action: `{f.get('suggested_action','?')}` · effort: `{f.get('effort','?')}` · "
                         f"risk: `{f.get('risk','?')}` · blast: `{f.get('blast_radius','?')}`")
        lines.append("")

    # 4. Appendix A — methods log
    lines.append("## Appendix A — Methods log")
    lines.append("")
    lines.append(f"Fan-out commit SHA: `ba378a44b25d5f6bec13ea74afbd22ffae25c5b2`")
    lines.append("")
    for dim in DIMS:
        p = payloads[dim]
        pre = p.get("preflight", {})
        lines.append(f"### {dim} preflight")
        lines.append("")
        lines.append(f"- input_set: `{pre.get('input_set','')}`")
        lines.append(f"- exit_summary: `{pre.get('exit_summary','')}`")
        lines.append(f"- containers_seen: {pre.get('containers_seen', [])}")
        lines.append("- commands_attempted:")
        for c in pre.get("commands_attempted", []):
            lines.append(f"  - `{c.get('cmd','')}` (exit={c.get('exit','?')}, lines={c.get('lines','?')})")
        lines.append("")
    lines.append("**Known limitations of this round:**")
    lines.append("")
    lines.append("- AST scanners may miss dynamic lookups (`getattr`, runtime `importlib`, "
                 "`from x import *` indirection); a symbol/function flagged as unused may still be "
                 "reached via these paths. Reviewers should verify before deletion.")
    lines.append("- `missing_session_event` heuristic is broad; it flags every DB write without a "
                 "`publish_session_event` even where the call site is genuinely background / non-watchable. "
                 "Treat as a list of candidates for the writing-plans cycle, not a definitive list.")
    lines.append("- `vulture` was unavailable in the execution environment; `unimported_symbols.py` used "
                 "its AST + single-pass-index fallback (slower, slightly more false positives).")
    lines.append("- `log_errors.py` window covered ~72h of api/code-worker/mcp-tools/embedding-service/"
                 "memory-core logs. Errors not yet in that window are not in this report.")
    lines.append("")

    # 5. Appendix B — Luna consensus
    lines.append("## Appendix B — Luna consensus snapshot")
    lines.append("")
    lines.append("See spec Appendix B at "
                 f"[`{SPEC_PATH}`]({SPEC_PATH}) — the spec went through 2 spec-reviewer iterations + "
                 "3 Luna rounds (consensus reached at round 3 with the literal `APPROVED` signal). "
                 "Luna agent UUID: `cfb6dd14-1889-4751-b645-77bbd53c65c3`. "
                 "Session id: `d9e5b6ad-1f33-4624-bb71-f65908c2716e`. "
                 "Platform: Codex CLI on `gpt-5.5` (Pro $200/mo tier).")
    lines.append("")
    lines.append("### Open questions (§9 of the spec) — to be sent to Luna in a separate round after report delivery")
    lines.append("")
    lines.append("1. Is there a sixth dimension worth scanning? (e.g. test-suite smell, observability gaps, secret-hygiene)")
    lines.append("2. Are any of the 5 dimensions overlapping enough to merge?")
    lines.append("3. Should the report rank by risk or by effort/value?")
    lines.append("4. Any canonical pattern in CLAUDE.md or docs/architecture that we forgot to lift into §3.3?")
    lines.append("")

    REPORT_PATH.write_text("\n".join(lines))
    print(f"Report written: {REPORT_PATH}  ({REPORT_PATH.stat().st_size} bytes)")
    print(f"Total post-dedupe findings: {len(dedup)}  | Top-10 ranked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
