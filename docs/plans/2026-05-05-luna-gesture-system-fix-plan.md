# Luna Gesture System — Fix Plan (post-launch hot patch)

**Date:** 2026-05-05
**Owner:** triggered by saguilera1608 live test on 0.1.61
**Status:** in-progress

## Background

The gesture system shipped via the 2026-05-03 plan and PRs #276 / #279 / #283.
Live diagnostic with the new logging from PRs #292 / #293 (Luna 0.1.61) showed
the **engine works** — Apple Vision is detecting hands at 21–22 fps with
confidence 1.0 — but **no gesture ever triggers an action**.

Symptoms reproduced from `~/Library/Logs/com.agentprovision.luna/Luna.log`:

- 45+ emitted `gesture-event` payloads in a 60s window
- Pose detection works: `Three: 29, OpenPalm: 8, Fist: 6, ThumbUp: 4, Custom: 4, Four: 2, Peace: 1`
- **Every event has `motion: { kind: None, direction: None, magnitude: 0.0, velocity: 0.0 }`**
- Wake state transitions only ever: `Sleeping → Arming → Sleeping` (twice)
- Wake never reaches `Armed` for any sustained period the user can see in the
  spatial HUD ("SLEEPING" badge stays up the whole time)

## Root causes

### Bug 1 — Wake state machine drops Arming on any non-OpenPalm pose

`apps/luna-client/src-tauri/src/gesture/wake.rs` lines 70–73:

```rust
(WakeState::Arming, WakeInput::Pose { .. }) => {
    self.state = WakeState::Sleeping;
    self.arming_started_at = None;
}
```

The catch-all at the end of the `Arming` arm sleeps on **any** pose other than
a confident `OpenPalm`. A single frame that classifies as `Three` or `ThumbUp`
during the 500ms hold window resets the machine to `Sleeping`. The user's
real hand naturally flickers between poses as fingers settle, so Arming never
completes the hold.

### Bug 2 — Motion classifier compares the entire ring buffer (~1s) against sub-second duration thresholds

`apps/luna-client/src-tauri/src/gesture/motion.rs`:

- Window size: 30 samples × 30 fps ≈ **1000 ms** of palm history once full.
- `classify_swipe`: `samples.front() → samples.back()`, requires `dur ≤ 350 ms`.
- `classify_pinch`: same shape, requires `dur ≤ 600 ms`.
- `classify_rotate`: same shape, requires `dur ≤ 600 ms`.

Once the buffer fills (after ~1 s of capture), the front-to-back duration is
always ~1000 ms, which always exceeds 350 / 600 / 600 ms. So `swipe`, `pinch`,
and `rotate` **can never classify** under steady-state operation. Only `tap`
(uses an explicit 8-sample tail window) and `sweep` (1200 ms ceiling) can fire.

The user's log confirms zero swipe / pinch / rotate / sweep / tap events
across ~60 s of waving. Every event is `kind: None`.

### Bug 3 — Wake state armed transitions don't get emitted to the React HUD before the next idle

This is a UX consequence of Bug 1, not an independent bug. When Arming →
Armed happens (in the rare case the user holds OpenPalm cleanly for 500 ms),
the next non-OpenPalm frame immediately drops back to `Sleeping` because
the `Armed` arm at line 78 doesn't actually exit Arming — wait, that's
correct. The actual issue is Bug 1: Arming never completes because of pose
flicker.

Bug 3 is a duplicate of Bug 1; the sustained-Armed state is achievable once
Bug 1 is fixed.

## Fix plan

### Fix 1 — Wake state should tolerate pose flicker during Arming

File: `apps/luna-client/src-tauri/src/gesture/wake.rs`

Replace the catch-all `Arming → Sleeping` arm with one that sleeps **only**
when the hand goes away (`pose: None`) or confidence collapses below
`ARM_CONFIDENCE / 2`. A frame that classifies as `Three` while the user is
still showing five fingers is noise from the pose classifier, not the user
giving up — so we should keep counting toward the hold.

Updated transitions (Arming arm only):

| Input | New behaviour |
|---|---|
| `Pose { Some(OpenPalm), confidence ≥ 0.85 }` | If `now - arming_started_at ≥ 500ms` → `Armed`. Otherwise stay `Arming`. (existing) |
| `Pose { Some(other), confidence ≥ 0.85 }` | Stay `Arming`. **(new — was: drop to Sleeping)** |
| `Pose { None, .. }` or `Pose { _, conf < 0.42 }` | Drop to `Sleeping`. Reset `arming_started_at`. **(new — was: implicit catch-all)** |
| `WakeInput::Idle` | No change. |

Rationale: the user's intent during Arming is signalled by **a hand being
visible**. Pose classifier flicker between OpenPalm / Three / ThumbUp is
an artifact of finger-extension thresholds, not user intent. The hand
disappearing or confidence collapsing means the user dropped their hand —
THAT is the right exit signal.

### Fix 2 — Motion classifier should use a windowed comparison sized to the constraint

File: `apps/luna-client/src-tauri/src/gesture/motion.rs`

Replace `samples.front() → samples.back()` with `samples_within(duration_ms)
→ samples.back()`. For each classifier, walk backwards from `back()` until
the duration constraint is hit, and use that walked-to sample as `start`.

Concrete change for `classify_swipe` (≤350 ms):

```rust
fn classify_swipe(&self) -> Option<Motion> {
    let end = self.samples.back()?;
    // Walk backwards to find the oldest sample within SWIPE_MAX_DURATION_MS.
    let start = self
        .samples
        .iter()
        .rev()
        .take_while(|s| end.ts - s.ts <= SWIPE_MAX_DURATION_MS)
        .last()?;
    let dx = end.palm.x - start.palm.x;
    let dy = end.palm.y - start.palm.y;
    let mag = (dx * dx + dy * dy).sqrt();
    let dur = end.ts - start.ts;
    if mag >= SWIPE_MIN_MAGNITUDE && dur > 0 {
        // dur is now guaranteed ≤ SWIPE_MAX_DURATION_MS by construction
        ...
    }
    None
}
```

Same shape for `classify_pinch` and `classify_rotate`. `classify_tap` already
uses a tail window (correct). `classify_sweep` has a ≥ minimum-duration
constraint that needs the OLDEST sample within the window — opposite of
the swipe case — so for sweep we walk back to find the OLDEST sample where
`end.ts - s.ts ≤ SWEEP_MAX_DURATION_MS` AND `end.ts - s.ts ≥ SWEEP_MIN_DURATION_MS`,
which the existing front-to-back compare actually matches when the buffer
duration falls in [400, 1200] ms. Sweep is OK as-is, but I'll add the
same windowed pattern for consistency.

### Fix 3 — Add tests for both fixes

`apps/luna-client/src-tauri/src/gesture/`:

- `wake.rs` — extend the existing test module with:
  - `arming_persists_through_pose_flicker` — drive `Arming` then feed
    `Three` for a frame, then `OpenPalm` again, expect state still `Arming`
    until 500 ms elapse, then `Armed`.
  - `arming_drops_to_sleeping_on_no_hand` — feed `Pose { None, .. }` while
    Arming, expect drop to `Sleeping`.
- `motion.rs` — extend with:
  - `swipe_classifies_with_full_30_sample_buffer` — fill the ring with 1 s
    of samples that include a 200 ms swipe-magnitude burst at the tail,
    expect `Swipe` motion classification.
  - `pinch_classifies_with_full_30_sample_buffer` — same shape.
  - `rotate_classifies_with_full_30_sample_buffer` — same shape.

The existing tests likely pass because they fill the buffer with exactly
the swipe sequence and never exceed the duration cap. The bug only
manifests at steady-state when the buffer holds older history. The new
tests add older history before the gesture to reproduce the bug, then
assert the fix.

## Out of scope for this PR

- **No changes to the action layer.** Once events fire with real motion,
  the binding match on the React side is already correct (verified earlier
  via `bindingMatches`).
- **No new gestures.** This is a hot fix for the existing default bindings.
- **No tuning of confidence / magnitude thresholds.** They're conservative
  in spec and match the user's report of clean Vision detection at
  confidence=1.0.

## Done items log

- 2026-05-05: Root-cause via live diagnostic logs from PR #293 → confirmed
  three failure modes (wake-flicker, motion full-buffer compare, no events
  reaching React).
- 2026-05-05: Fix plan written.
- _(in flight)_ Fix 1 + Fix 2 + tests, single PR.
