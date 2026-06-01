#!/usr/bin/env python3
"""Local bridge for Luna -> Claudia delegation.

This script is intentionally dependency-free so Simon can run it from a
local Claude Code hook, a laptop daemon, or a GitHub issue workflow.

Examples:
  python scripts/claudia_bridge.py init
  python scripts/claudia_bridge.py create --title "Review bridge plan" --body-file docs/plans/x.md
  python scripts/claudia_bridge.py poll
  CLAUDIA_BRIDGE_SECRET=... python scripts/claudia_bridge.py serve --host 127.0.0.1 --port 8765

Side effects:
  Creates and updates files under .claudia/ by default. It does not mutate
  production data or call external services.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


DEFAULT_ROOT = ".claudia"
QUEUE_DIRS = ("inbox", "status", "outbox", "archive")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return slug[:64] or "task"


def normalize_task_id(value: str | None, title: str, created_at: str) -> str:
    if value:
        return slugify(str(value))
    stamp = created_at.replace(":", "").replace("-", "")
    return f"{stamp}-{slugify(title)}"


def queue_root(path: str | Path) -> Path:
    return Path(path).resolve()


def ensure_queue(root: Path) -> None:
    for dirname in QUEUE_DIRS:
        (root / dirname).mkdir(parents=True, exist_ok=True)
    readme = root / "README.md"
    if not readme.exists():
        write_atomic(
            readme,
            "# Claudia Bridge Queue\n\n"
            "Repo-native mailbox for Luna, Claudia, Claude Code hooks, "
            "webhooks, and GitHub issue handoffs.\n",
        )


def write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def read_body(body: str | None, body_file: str | None) -> str:
    if body_file:
        return Path(body_file).read_text(encoding="utf-8")
    return body or ""


def render_task(
    *,
    title: str,
    body: str,
    source: str,
    reply_to: str,
    labels: list[str],
    task_id: str | None = None,
) -> tuple[str, str]:
    created_at = utc_now()
    ident = normalize_task_id(task_id, title, created_at)
    label_text = ", ".join(labels) if labels else "claudia-bridge"
    markdown = f"""# {title}

Task ID: `{ident}`
Created: `{created_at}`
Source: `{source}`
Labels: `{label_text}`
Reply location: `{reply_to}`

## Task

{body.strip() or "_No body provided._"}

## Contract

- Do not revert user or peer-agent changes.
- Verify repo, calendar, email, or live-state claims with the right tool before stating them as current.
- Keep uncertain ideas labeled as inference or hypothesis.
- Write status updates to `{reply_to}` or the linked GitHub issue/PR.
- For code changes, include changed files, tests run, and residual risk.
"""
    return ident, markdown


def create_task(args: argparse.Namespace) -> Path:
    root = queue_root(args.root)
    ensure_queue(root)
    body = read_body(args.body, args.body_file)
    reply_to = args.reply_to or f"{DEFAULT_ROOT}/outbox/<task-id>.md"
    task_id, markdown = render_task(
        title=args.title,
        body=body,
        source=args.source,
        reply_to=reply_to,
        labels=args.label,
        task_id=args.task_id,
    )
    path = root / "inbox" / f"{task_id}.md"
    write_atomic(path, markdown)
    return path


def poll_tasks(args: argparse.Namespace) -> int:
    root = queue_root(args.root)
    ensure_queue(root)
    tasks = sorted((root / "inbox").glob("*.md"))
    if not tasks:
        print("No pending Claudia tasks.")
        return 0
    for task in tasks[: args.limit]:
        print(f"--- {task.relative_to(root.parent)}")
        print(task.read_text(encoding="utf-8").strip())
        print()
    if len(tasks) > args.limit:
        print(f"... {len(tasks) - args.limit} more task(s) pending.")
    return 0


def render_issue(args: argparse.Namespace) -> int:
    body = read_body(args.body, args.body_file)
    _, markdown = render_task(
        title=args.title,
        body=body,
        source=args.source,
        reply_to=args.reply_to or "GitHub issue comments and linked PR",
        labels=args.label,
        task_id=args.task_id,
    )
    print(markdown)
    return 0


def verify_signature(secret: str, raw_body: bytes, header: str | None) -> bool:
    if not secret:
        return True
    if not header or not header.startswith("sha256="):
        return False
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={digest}", header)


def parse_json_body(raw_body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("JSON body must be an object")
    return payload


class BridgeHandler(BaseHTTPRequestHandler):
    root: Path
    secret: str

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write(f"claudia-bridge: {fmt % args}\n")

    def do_GET(self) -> None:
        if self.path != "/health":
            self.send_error(404, "not found")
            return
        self._send_json(200, {"ok": True, "time": utc_now()})

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", "0"))
        raw = self.rfile.read(length)
        if not verify_signature(self.secret, raw, self.headers.get("x-claudia-signature")):
            self._send_json(401, {"ok": False, "error": "invalid signature"})
            return
        try:
            payload = parse_json_body(raw)
            if self.path == "/tasks":
                path = self._write_task(payload)
            elif self.path == "/outbox":
                path = self._write_outbox(payload)
            else:
                self.send_error(404, "not found")
                return
        except ValueError as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
            return
        self._send_json(201, {"ok": True, "path": str(path)})

    def _write_task(self, payload: dict[str, Any]) -> Path:
        title = str(payload.get("title") or "").strip()
        if not title:
            raise ValueError("title is required")
        body = str(payload.get("body") or "")
        labels = payload.get("labels") or ["claudia-bridge"]
        if not isinstance(labels, list):
            raise ValueError("labels must be a list")
        task_id, markdown = render_task(
            title=title,
            body=body,
            source=str(payload.get("source") or "webhook"),
            reply_to=str(payload.get("reply_to") or f"{DEFAULT_ROOT}/outbox/<task-id>.md"),
            labels=[str(label) for label in labels],
            task_id=payload.get("task_id"),
        )
        path = self.root / "inbox" / f"{task_id}.md"
        write_atomic(path, markdown)
        return path

    def _write_outbox(self, payload: dict[str, Any]) -> Path:
        task_id = str(payload.get("task_id") or "").strip()
        body = str(payload.get("body") or "").strip()
        if not task_id:
            raise ValueError("task_id is required")
        if not body:
            raise ValueError("body is required")
        path = self.root / "outbox" / f"{slugify(task_id)}.md"
        write_atomic(path, f"# Claudia Reply\n\nTask ID: `{task_id}`\nCreated: `{utc_now()}`\n\n{body}\n")
        return path

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve(args: argparse.Namespace) -> int:
    root = queue_root(args.root)
    ensure_queue(root)

    class Handler(BridgeHandler):
        pass

    Handler.root = root
    Handler.secret = os.environ.get("CLAUDIA_BRIDGE_SECRET", "")
    if not Handler.secret and not args.allow_unsigned:
        print(
            "CLAUDIA_BRIDGE_SECRET is required for webhook mode; "
            "pass --allow-unsigned only for isolated local testing",
            file=sys.stderr,
        )
        return 2
    if not Handler.secret:
        print("warning: webhook requests are unsigned", file=sys.stderr)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"claudia bridge listening on http://{args.host}:{args.port}")
    server.serve_forever()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Luna/Claudia bridge utility")
    parser.add_argument("--root", default=DEFAULT_ROOT, help="queue root, default .claudia")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create queue directories")

    create = sub.add_parser("create", help="write a task to .claudia/inbox")
    create.add_argument("--title", required=True)
    create.add_argument("--body")
    create.add_argument("--body-file")
    create.add_argument("--source", default="luna-supervisor")
    create.add_argument("--reply-to")
    create.add_argument("--task-id")
    create.add_argument("--label", action="append", default=[])

    poll = sub.add_parser("poll", help="print pending inbox tasks for Claude Code hooks")
    poll.add_argument("--limit", type=int, default=5)

    issue = sub.add_parser("issue-body", help="render a GitHub issue body without writing files")
    issue.add_argument("--title", required=True)
    issue.add_argument("--body")
    issue.add_argument("--body-file")
    issue.add_argument("--source", default="luna-supervisor")
    issue.add_argument("--reply-to")
    issue.add_argument("--task-id")
    issue.add_argument("--label", action="append", default=[])

    http = sub.add_parser("serve", help="start signed local webhook receiver")
    http.add_argument("--host", default="127.0.0.1")
    http.add_argument("--port", type=int, default=8765)
    http.add_argument("--allow-unsigned", action="store_true", help="allow unsigned local test requests")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "init":
        ensure_queue(queue_root(args.root))
        return 0
    if args.command == "create":
        path = create_task(args)
        print(path)
        return 0
    if args.command == "poll":
        return poll_tasks(args)
    if args.command == "issue-body":
        return render_issue(args)
    if args.command == "serve":
        return serve(args)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
