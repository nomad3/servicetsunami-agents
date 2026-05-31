"""cli_runtime.apply_git_ssh — per-turn SSH keyfile + GIT_SSH_COMMAND (PR2).

Security-critical: ephemeral 0600 keyfile, GIT_SSH_COMMAND in the per-turn env
only (set-or-strip, no cross-tenant bleed), cleaned up after the turn.
"""
import os
import stat

import cli_runtime

_FAKE_KEY = "-----BEGIN OPENSSH PRIVATE KEY-----\nabcDEF123\n-----END OPENSSH PRIVATE KEY-----"


def test_no_key_strips_git_ssh_command():
    env = {"GIT_SSH_COMMAND": "stale-from-prior-tenant", "PATH": "/x"}
    cleanup = cli_runtime.apply_git_ssh(env, None)
    assert "GIT_SSH_COMMAND" not in env  # stripped — no bleed
    assert env["PATH"] == "/x"  # unrelated keys untouched
    cleanup()  # no-op, must not raise


def test_key_writes_ephemeral_0600_keyfile_and_sets_command():
    env = {}
    cleanup = cli_runtime.apply_git_ssh(env, _FAKE_KEY)
    cmd = env["GIT_SSH_COMMAND"]
    for opt in ("ssh -i ", "IdentitiesOnly=yes", "BatchMode=yes",
                "StrictHostKeyChecking=yes", "UserKnownHostsFile=/dev/null",
                "GlobalKnownHostsFile=/etc/ssh/ssh_known_hosts"):
        assert opt in cmd, opt
    keyfile = cmd.split("ssh -i ", 1)[1].split(" ", 1)[0]
    assert os.path.isfile(keyfile)
    assert stat.S_IMODE(os.stat(keyfile).st_mode) == 0o600  # owner-only
    with open(keyfile) as fh:
        content = fh.read()
    assert _FAKE_KEY in content
    assert content.endswith("\n")  # ssh needs a trailing newline
    keydir = os.path.dirname(keyfile)
    # NOT the persistent session dir — an ephemeral mkdtemp under tmp.
    assert "ghssh_" in keydir
    cleanup()
    assert not os.path.exists(keydir)  # the whole dir is removed


def test_key_overwrites_stale_command():
    env = {"GIT_SSH_COMMAND": "old"}
    cleanup = cli_runtime.apply_git_ssh(env, _FAKE_KEY)
    assert "ghssh_" in env["GIT_SSH_COMMAND"]  # the fresh per-turn keyfile path
    cleanup()


def test_setup_exception_cleans_up_keyfile_dir(monkeypatch):
    # Codex IMPORTANT: if anything after mkdtemp raises, the 0600 keyfile dir must
    # be removed (not leaked) before the exception propagates.
    created = {}
    real_mkdtemp = cli_runtime.tempfile.mkdtemp

    def spy_mkdtemp(*a, **k):
        d = real_mkdtemp(*a, **k)
        created["dir"] = d
        return d

    monkeypatch.setattr(cli_runtime.tempfile, "mkdtemp", spy_mkdtemp)
    # Force a failure AFTER mkdtemp: os.fdopen raises (propagates; chmod is
    # swallowed, and patching os.open/exists would break the assertion below).
    real_exists = os.path.exists

    def _boom(*a, **k):
        raise OSError("boom")

    monkeypatch.setattr(cli_runtime.os, "fdopen", _boom)
    env = {}
    raised = False
    try:
        cli_runtime.apply_git_ssh(env, _FAKE_KEY)
    except OSError:
        raised = True
    assert raised
    assert "GIT_SSH_COMMAND" not in env  # not set on failure
    assert not real_exists(created["dir"])  # dir cleaned up, no leak


def test_error_snippet_scrubs_keyfile_path():
    # Codex IMPORTANT: OpenSSH stderr can include `-i /tmp/ghssh_xxx/id`; the
    # user-facing snippet must scrub that ephemeral path.
    stderr = 'Load key "/tmp/ghssh_abc123/id": invalid format\nssh -i /tmp/ghssh_abc123/id failed'
    out = cli_runtime.safe_cli_error_snippet(stderr, "")
    assert "/tmp/ghssh_" not in out
    assert "<ssh-key>" in out
    # a normal error with no keyfile path is unchanged
    assert cli_runtime.safe_cli_error_snippet("fatal: repository not found", "") == "fatal: repository not found"
