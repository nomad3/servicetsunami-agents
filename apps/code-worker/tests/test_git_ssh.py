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
