"""Temporal activities for git history extraction and PR outcome tracking.

Activities:
- extract_git_history: Scans recent git commits and stores as knowledge entities/observations
- poll_pr_outcomes: Checks open PRs created by code-worker and processes outcomes
"""
import json
import logging
import os
import subprocess
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from temporalio import activity

logger = logging.getLogger(__name__)

WORKSPACE = os.environ.get("CODE_WORKSPACE", "/workspace")


@dataclass
class GitHistoryInput:
    tenant_id: str
    repo_name: str = "nomad3/servicetsunami-agents"
    since_hours: int = 24


@dataclass
class GitHistoryResult:
    contributors_created: int = 0
    commits_stored: int = 0
    relations_created: int = 0
    hotspots_detected: int = 0
    error: Optional[str] = None


@dataclass
class PROutcomeInput:
    tenant_id: str
    repo_name: str = "nomad3/servicetsunami-agents"


@dataclass
class PROutcomeResult:
    prs_processed: int = 0
    rewards_assigned: int = 0
    error: Optional[str] = None


def _run_git(cmd: str, cwd: str = WORKSPACE, timeout: int = 30) -> str:
    """Run a git command and return stdout."""
    try:
        result = subprocess.run(
            cmd, shell=True, cwd=cwd,
            capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode != 0:
            logger.warning("Git command failed: %s — %s", cmd, result.stderr[:500])
            return ""
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, Exception) as e:
        logger.warning("Git command error: %s — %s", cmd, e)
        return ""


def _parse_git_log(raw_output: str) -> list:
    """Parse git log output into structured commit records."""
    commits = []
    if not raw_output:
        return commits

    for line in raw_output.strip().split("\n"):
        if not line.strip():
            continue
        try:
            parts = line.split("\t", 4)
            if len(parts) >= 4:
                commits.append({
                    "hash": parts[0],
                    "author": parts[1],
                    "email": parts[2],
                    "date": parts[3],
                    "subject": parts[4] if len(parts) > 4 else "",
                    "files_changed": 0,  # Will be enriched below
                })
        except Exception:
            continue

    return commits


def _count_file_changes(since_hours: int, cwd: str = WORKSPACE) -> dict:
    """Count file changes per directory over a time window."""
    raw = _run_git(
        f'git log --since="{since_hours} hours ago" --name-only --pretty=format:""',
        cwd=cwd,
    )
    if not raw:
        return {}

    dir_counts = defaultdict(int)
    for line in raw.split("\n"):
        line = line.strip()
        if not line:
            continue
        # Extract top-level directory (or file if at root)
        parts = line.split("/")
        if len(parts) >= 2:
            directory = "/".join(parts[:2]) + "/"
        else:
            directory = line
        dir_counts[directory] += 1

    return dict(dir_counts)


@activity.defn
async def extract_git_history(task_input: GitHistoryInput) -> GitHistoryResult:
    """Extract recent git history and store as knowledge entities + observations.

    Runs in the consolidation cycle. Parses commits, creates contributor entities,
    repository entity, contributes_to relations, and git_commit observations.
    Also detects file hotspots (directories with >5 changes).
    """
    try:
        # 1. Run git log for recent commits
        since = f"{task_input.since_hours} hours ago"
        raw_log = _run_git(
            f'git log --since="{since}" --format="%H\t%an\t%ae\t%aI\t%s"',
            cwd=WORKSPACE,
        )

        commits = _parse_git_log(raw_log)
        if not commits:
            return GitHistoryResult(error="No recent commits found")

        # 2. Enrich with file change counts per commit
        for commit in commits:
            stat = _run_git(
                f'git diff --shortstat {commit["hash"]}~1 {commit["hash"]} 2>/dev/null',
                cwd=WORKSPACE,
            )
            if stat:
                # "3 files changed, 10 insertions(+), 5 deletions(-)"
                import re
                match = re.search(r'(\d+) file', stat)
                if match:
                    commit["files_changed"] = int(match.group(1))

        # 3. Store in knowledge graph via service
        from app.db.session import SessionLocal
        from app.services import knowledge

        db = SessionLocal()
        try:
            # Store commits as entities + observations
            stats = knowledge.store_git_context(
                db,
                tenant_id=uuid.UUID(task_input.tenant_id),
                commits=commits,
                repo_name=task_input.repo_name,
            )

            # 4. Detect file hotspots
            file_changes = _count_file_changes(
                since_hours=168,  # 7-day rolling window
                cwd=WORKSPACE,
            )
            hotspots = knowledge.detect_file_hotspots(
                db,
                tenant_id=uuid.UUID(task_input.tenant_id),
                file_changes=file_changes,
                repo_name=task_input.repo_name,
                threshold=5,
            )

            return GitHistoryResult(
                contributors_created=stats["contributors_created"],
                commits_stored=stats["commits_stored"],
                relations_created=stats["relations_created"],
                hotspots_detected=hotspots,
            )
        finally:
            db.close()

    except Exception as e:
        logger.exception("Git history extraction failed: %s", e)
        return GitHistoryResult(error=str(e))


@activity.defn
async def poll_pr_outcomes(task_input: PROutcomeInput) -> PROutcomeResult:
    """Poll open PRs created by code-worker and process outcomes.

    Checks PRs on branches matching 'code/*' pattern. For merged/closed PRs,
    stores observations and assigns RL rewards.
    """
    try:
        # List PRs created by code-worker (code/* branches)
        raw = _run_git(
            'gh pr list --state all --limit 20 --json number,title,state,headRefName,reviews '
            '--jq \'.[] | select(.headRefName | startswith("code/"))\'',
            cwd=WORKSPACE,
            timeout=30,
        )

        if not raw:
            return PROutcomeResult()

        from app.db.session import SessionLocal
        from app.services import knowledge, rl_experience_service

        db = SessionLocal()
        try:
            prs_processed = 0
            rewards_assigned = 0

            # Parse each PR (gh outputs JSON lines)
            for line in raw.strip().split("\n"):
                if not line.strip():
                    continue
                try:
                    pr = json.loads(line)
                except json.JSONDecodeError:
                    continue

                pr_number = pr.get("number")
                state = pr.get("state", "").upper()
                title = pr.get("title", "")
                reviews = pr.get("reviews", [])

                # Only process completed PRs
                if state not in ("MERGED", "CLOSED"):
                    continue

                outcome = "merged" if state == "MERGED" else "closed"
                review_comments = [
                    r.get("body", "")[:200]
                    for r in (reviews or [])
                    if r.get("body")
                ]

                # Store PR outcome as observation
                result = knowledge.store_pr_outcome(
                    db,
                    tenant_id=uuid.UUID(task_input.tenant_id),
                    repo=task_input.repo_name,
                    pr_number=pr_number,
                    outcome=outcome,
                    title=title,
                    review_comments=review_comments,
                )

                # Try to find and reward the RL experience for this PR
                try:
                    from sqlalchemy import text as sql_text
                    exp = db.execute(
                        sql_text("""
                            SELECT id FROM rl_experiences
                            WHERE tenant_id = CAST(:tid AS uuid)
                            AND decision_point = 'code_task'
                            AND state::text LIKE :pr_pattern
                            AND reward IS NULL
                            ORDER BY created_at DESC LIMIT 1
                        """),
                        {
                            "tid": task_input.tenant_id,
                            "pr_pattern": f"%PR #{pr_number}%",
                        },
                    ).fetchone()

                    if exp:
                        rl_experience_service.assign_reward(
                            db,
                            experience_id=exp.id,
                            reward=result["rl_reward"],
                            reward_components={
                                "pr_outcome": outcome,
                                "pr_number": pr_number,
                                "review_count": len(review_comments),
                            },
                            reward_source="git_pr_outcome",
                        )
                        rewards_assigned += 1
                except Exception:
                    logger.debug("RL reward assignment skipped for PR #%s", pr_number)

                prs_processed += 1

            return PROutcomeResult(
                prs_processed=prs_processed,
                rewards_assigned=rewards_assigned,
            )
        finally:
            db.close()

    except Exception as e:
        logger.exception("PR outcome polling failed: %s", e)
        return PROutcomeResult(error=str(e))
