# F2 Keychain — wontfix decision (2026-05-25)

**Decision:** Abandon F2 (macOS login-keychain secret storage). Keep `$HOME` 0600 files. Refocus Sub-project A effort on F7b + F7c (JWT kid cutover + Ed25519), which close real takeover-chain risk.

**Decided by:** Simon, 2026-05-25 evening, after PR #724 verify deploy still showed `[fallback]` despite the `set-keychain-settings` auto-lock fix.

---

## Root cause F2 cannot work as designed

The runner is a launchd-spawned subprocess running as user `nomade`. Its security context (Mach security session) is **distinct** from the operator's GUI security session, even though both run as the same user.

Keychain unlock state is per-security-session. When loginwindow unlocks the login keychain at GUI login, that unlock applies to the GUI session's Mach context. Processes in other security sessions (including launchd-spawned daemons/agents) see the keychain as locked **even if it's "unlocked" from the operator's interactive shell**.

Diagnostic v3 probe (PR #723) running in the runner's actual context confirmed:

```
=== keychain lock state ===
security: SecKeychainCopySettings ...login.keychain-db:
  User interaction is not allowed.

=== probe each entry (no -w, just attrs) ===
all 4: rc=0  ✅ ACL fix from #721 works — entries visible

=== probe each entry WITH -w ===
all 4: rc=36 ❌ errSecInteractionNotAllowed
```

After PR #724 disabled auto-lock via `security set-keychain-settings`, a fresh deploy still saw `[fallback]` for all 4 secrets. `set-keychain-settings` prevents auto-locking after unlock; it does not unlock a session-view that started locked.

To make the runner read the keychain, we would need to store the operator's login password somewhere readable by the runner — defeating the encryption-at-rest purpose entirely (chicken-and-egg).

## Alternatives considered + rejected

| Option | Why rejected |
|---|---|
| Dedicated empty-password keychain | Zero crypto value beyond file perms (key is well-known empty string) |
| System keychain | Requires sudo, root-owned secrets, breaks per-user runner model |
| Login-password-in-file unlock | Defeats the entire encryption-at-rest premise |
| Auto-login + always-unlocked | Mac would auto-login at boot, large security regression for a marginal gain |

## Security comparison (what we'd actually gain if F2 worked)

| Threat | `$HOME` files (0600) | Keychain (had it worked) |
|---|---|---|
| Code execution as `nomade` | ✗ reads files | ✗ reads via `security -w` |
| Code execution as other user | ✓ blocked | ✓ blocked |
| Disk theft (powered off Mac) | ✗ readable † | ✓ encrypted by login password |
| Time Machine / backup capture | ✗ included | ✓ excluded by default |

† Assumes FileVault is off. If FileVault is on (default on modern macOS, almost certainly on the runner Mac), the `$HOME` files are *also* encrypted at rest behind the FileVault key, collapsing this row to "both encrypted." The keychain would still add a second layer of encryption, but the marginal gain shrinks substantially.

The realistic gain was **disk-theft + backup-hygiene**, not the takeover-chain risk. Sub-project A's actual takeover-chain hardening lives in F7b + F7c (JWT key rotation + Ed25519).

## Why F7b/F7c are the right pivot (Luna's framing)

F7b's per-domain JWT secrets (`JWT_USER_SECRET` / `JWT_AGENT_TOKEN_SECRET` / `JWT_OAUTH_STATE_SECRET`) are not just a key-rotation hygiene win — they're a **coordination-layer trust boundary**. The platform's agents act on behalf of three distinct identities (humans, agents, OAuth-state). If those identities share signing material, a leak in any one collapses the trust model for all three.

F7c (Ed25519) takes that further: asymmetric signing means the api can verify tokens without holding mint capability for every domain. That separation is what makes "agent acts on behalf of user" auditable at the cryptographic layer instead of just the application layer.

F2 was infrastructure hygiene; F7b/F7c are the actual coordination-layer security work.

## Sub-project A status after this decision

| Leg | Status |
|---|---|
| F1 (runner exfil hardening) | shipped earlier |
| F2 (keychain) | **wontfix** — this decision |
| F3 (audit logging) | shipped earlier |
| F4–F6 | shipped earlier |
| F7a (JWT mint hardening) | shipped earlier |
| **F7b (JWT kid cutover)** | **next** |
| **F7c (Ed25519 migration + cleanup commit)** | **after F7b** |

## PR arc that landed on this decision

- PR #712 — original F2 PR3 ship (keychain entries created, dual-source loader)
- PR #718 — diagnostic v1 (bash pipe exit-code bug)
- PR #719 — diagnostic v2 (captured `security` exit code, ACL inspection)
- PR #720 — first ACL fix attempt (`-A` with `-U`, didn't work)
- PR #721 — real ACL fix (delete-then-add, ACL verified open)
- PR #723 — diagnostic v3 (pinpointed errSecInteractionNotAllowed under runner context)
- PR #724 — auto-lock disabled (didn't help — session isolation, not auto-lock)
- PR #725 (this PR) — full revert + decision documented

## Lesson preserved

macOS keychain unlock is per-security-session, NOT per-user. Any future "let a launchd-spawned process read the keychain" plan must solve the cross-session unlock problem at design time. Don't relearn this through 7 PRs.
