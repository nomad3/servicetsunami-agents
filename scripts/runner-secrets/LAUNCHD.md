# Runner launchd plist — `LimitLoadToSessionType: Aqua`

The self-hosted GitHub Actions runner on the runner Mac is registered
as a launchd agent (typically at `~/Library/LaunchAgents/com.github.actions-runner.<repo>.plist`).
By default it can be loaded by either an Aqua session (user logged in,
GUI up) **or** a Background session (no GUI, before login).

After the F2 / PR3 Keychain migration, deploys read secrets from the
login keychain via `security find-generic-password -w`. The login
keychain is **locked** until Simon logs in to the GUI. Therefore the
runner must NOT auto-start in a Background session — it would try to
read a locked keychain and fail.

Add this one key to the plist:

```xml
<key>LimitLoadToSessionType</key>
<array>
  <string>Aqua</string>
</array>
```

The runner now only starts post-login. Worst case after reboot: deploys
queue until Simon logs in. This is acceptable per the spec (§5 PR3
rollback section).

---

## Locate the plist

```bash
ls ~/Library/LaunchAgents/ | grep -i runner
```

Expected: a file named like `actions.runner.<owner>-<repo>.<host>.plist`
or `com.github.actions-runner.<...>.plist`. There may be more than one
if multiple runners are registered.

---

## Patch the plist

For each runner plist found above:

```bash
PLIST="$HOME/Library/LaunchAgents/<the-plist-file>.plist"

# Unload first (cannot edit a loaded plist on modern macOS).
launchctl bootout gui/$(id -u) "$PLIST" 2>/dev/null || true

# Insert the key. If LimitLoadToSessionType already exists, this
# replaces it; otherwise it adds it at the top level.
/usr/libexec/PlistBuddy -c "Delete :LimitLoadToSessionType" "$PLIST" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :LimitLoadToSessionType array" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :LimitLoadToSessionType:0 string Aqua" "$PLIST"

# Reload.
launchctl bootstrap gui/$(id -u) "$PLIST"

# Verify.
/usr/libexec/PlistBuddy -c "Print :LimitLoadToSessionType" "$PLIST"
# Expected output:
#   Array {
#       Aqua
#   }
```

---

## Verification

After patching, confirm the runner is running:

```bash
launchctl list | grep -i runner
# Should show a PID for the runner agent, status code 0.
```

And confirm the constraint applies after a reboot:

1. Reboot the Mac.
2. Do NOT log in to the GUI.
3. From a remote SSH session (or post-login check after a delay):
   `launchctl list | grep -i runner` should show NO PID until login.
4. Log in to the GUI.
5. `launchctl list | grep -i runner` now shows a PID — runner started
   post-login, login keychain unlocked, ready to read Keychain entries.

If step 3 still shows a PID, the patch did not apply — re-run the
patch sequence and re-verify.

---

## Rollback

If the runner refuses to start after the patch (rare; usually a
malformed plist):

```bash
PLIST="$HOME/Library/LaunchAgents/<the-plist-file>.plist"
launchctl bootout gui/$(id -u) "$PLIST" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Delete :LimitLoadToSessionType" "$PLIST"
launchctl bootstrap gui/$(id -u) "$PLIST"
```

This restores the default (loads in any session). The runner will
start in a Background session again but Keychain reads will fail
until Simon logs in. Acceptable as a stopgap; re-apply the patch
after fixing whatever caused the malformed-plist issue.
