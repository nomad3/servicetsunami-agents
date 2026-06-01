from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "claudia_bridge", ROOT / "scripts" / "claudia_bridge.py"
)
claudia_bridge = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(claudia_bridge)


def test_create_task_writes_contract_and_queue_dirs(tmp_path):
    code = claudia_bridge.main(
        [
            "--root",
            str(tmp_path / ".claudia"),
            "create",
            "--title",
            "Review bridge plan",
            "--body",
            "Check the hook plus webhook plus GitHub issue contract.",
            "--task-id",
            "bridge-plan",
            "--label",
            "claudia",
        ]
    )

    assert code == 0
    root = tmp_path / ".claudia"
    task = root / "inbox" / "bridge-plan.md"
    assert task.exists()
    for dirname in ("inbox", "status", "outbox", "archive"):
        assert (root / dirname).is_dir()
    body = task.read_text()
    assert "Review bridge plan" in body
    assert "Do not revert user or peer-agent changes." in body
    assert "Check the hook plus webhook plus GitHub issue contract." in body


def test_explicit_task_id_is_filename_safe(tmp_path):
    code = claudia_bridge.main(
        [
            "--root",
            str(tmp_path / ".claudia"),
            "create",
            "--title",
            "Unsafe ID",
            "--body",
            "Body",
            "--task-id",
            "../bad path",
        ]
    )

    assert code == 0
    assert (tmp_path / ".claudia" / "inbox" / "bad-path.md").exists()
    assert not (tmp_path / "bad path.md").exists()


def test_signature_verification_requires_sha256_hmac():
    secret = "shared-secret"
    raw = json.dumps({"title": "x"}).encode()
    digest = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()

    assert claudia_bridge.verify_signature(secret, raw, f"sha256={digest}")
    assert not claudia_bridge.verify_signature(secret, raw, "sha256=bad")
    assert not claudia_bridge.verify_signature(secret, raw, None)
    assert claudia_bridge.verify_signature("", raw, None)


def test_issue_body_renders_without_writing_queue(tmp_path, capsys):
    code = claudia_bridge.main(
        [
            "--root",
            str(tmp_path / ".claudia"),
            "issue-body",
            "--title",
            "Manual consensus with Claudia",
            "--body",
            "Use this as the GitHub issue handoff.",
            "--task-id",
            "consensus",
        ]
    )

    assert code == 0
    output = capsys.readouterr().out
    assert "Manual consensus with Claudia" in output
    assert "Use this as the GitHub issue handoff." in output
    assert not (tmp_path / ".claudia").exists()


def test_webhook_mode_requires_secret_by_default(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("CLAUDIA_BRIDGE_SECRET", raising=False)

    code = claudia_bridge.main(
        [
            "--root",
            str(tmp_path / ".claudia"),
            "serve",
            "--port",
            "0",
        ]
    )

    assert code == 2
    assert "CLAUDIA_BRIDGE_SECRET is required" in capsys.readouterr().err
