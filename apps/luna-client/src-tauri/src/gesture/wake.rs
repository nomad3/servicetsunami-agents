//! Wake-gesture state machine. Defaults to Sleeping. An open-palm pose held
//! for 500 ms transitions to Armed. While Armed, 5 s of idle returns to
//! Sleeping unless a destructive-confirm window is in flight (in which case
//! the idle countdown is suspended).

use crate::gesture::types::{Pose, WakeState};

// Wake-arming accepts any "open-hand" classification — OpenPalm, Three, or
// Four — not just a strict 5-finger OpenPalm. The pose classifier in
// pose.rs requires every finger's tip-to-wrist distance to exceed the
// PIP-to-wrist distance, which means a relaxed pinky (very common on a
// real open hand) gets classified as Three (29 of 54 events in the live
// 2026-05-05 diagnostic — vs only 8 actual OpenPalm). Treating any of
// these three as a wake gesture matches user intent ("I raised an open
// hand") and recovers the wake path from the classifier's strictness.
fn is_wake_pose(pose: Pose) -> bool {
    matches!(pose, Pose::OpenPalm | Pose::Three | Pose::Four)
}

const ARM_HOLD_MS: i64 = 500;
const IDLE_TIMEOUT_MS: i64 = 5000;
const ARM_CONFIDENCE: f32 = 0.85;
// While Arming, a brief pose flicker (OpenPalm → Three → OpenPalm) used to
// drop the machine back to Sleeping immediately because the catch-all
// `Arming` arm slept unconditionally. Real cameras + the pose classifier
// flicker frame-to-frame as fingers settle, so the user's hand never
// stayed cleanly OpenPalm for 500ms. We now only abandon Arming when the
// hand DISAPPEARS or confidence collapses below this threshold — chosen
// at half ARM_CONFIDENCE so a "user lowered hand" event (confidence ~0)
// still exits, but a frame that classifies as Three at confidence 1.0
// keeps the hold counter ticking. Bug fix 2026-05-05.
const ARM_ABORT_CONFIDENCE: f32 = ARM_CONFIDENCE * 0.5;

#[derive(Debug, Clone, Copy)]
pub enum WakeInput {
    Pose { pose: Option<Pose>, confidence: f32 },
    Idle,
}

pub struct WakeMachine {
    state: WakeState,
    arming_started_at: Option<i64>,
    last_activity_ms: i64,
    confirm_pending: bool,
}

impl WakeMachine {
    pub fn new() -> Self {
        Self {
            state: WakeState::Sleeping,
            arming_started_at: None,
            last_activity_ms: 0,
            confirm_pending: false,
        }
    }

    pub fn state(&self) -> WakeState {
        self.state
    }

    pub fn set_confirm_pending(&mut self, v: bool) {
        self.confirm_pending = v;
        // Callers should follow with a Pose tick to refresh `last_activity_ms`
        // so the idle countdown re-baselines from clear-time. Tests rely on this.
    }

    pub fn force_sleep(&mut self) {
        self.state = WakeState::Sleeping;
        self.arming_started_at = None;
        self.confirm_pending = false;
    }

    pub fn tick(&mut self, input: WakeInput, now_ms: i64) {
        match (self.state, input) {
            (WakeState::Sleeping, WakeInput::Pose { pose: Some(p), confidence })
                if confidence >= ARM_CONFIDENCE && is_wake_pose(p) =>
            {
                self.state = WakeState::Arming;
                self.arming_started_at = Some(now_ms);
            }
            (WakeState::Arming, WakeInput::Pose { pose: Some(p), confidence })
                if confidence >= ARM_CONFIDENCE && is_wake_pose(p) =>
            {
                if let Some(start) = self.arming_started_at {
                    if now_ms - start >= ARM_HOLD_MS {
                        self.state = WakeState::Armed;
                        self.last_activity_ms = now_ms;
                        self.arming_started_at = None;
                    }
                }
            }
            // Hand still visible at decent confidence but classifier flickered
            // to a non-OpenPalm pose — keep counting toward the hold instead
            // of resetting to Sleeping. Real users can't hold an exact pose
            // for 500ms at 30fps without ANY misclassified frames; the old
            // catch-all aborted the hold every single time. Bug fix 2026-05-05.
            (
                WakeState::Arming,
                WakeInput::Pose { pose: Some(_), confidence },
            ) if confidence >= ARM_ABORT_CONFIDENCE => {
                // No-op: stay in Arming. The next OpenPalm frame will check
                // whether the hold time has elapsed.
            }
            // Hand disappeared OR confidence collapsed — real abort signal.
            (WakeState::Arming, WakeInput::Pose { .. }) => {
                self.state = WakeState::Sleeping;
                self.arming_started_at = None;
            }
            // Idle tick during Arming — same abort path so the wake gesture
            // doesn't stick around indefinitely without pose input.
            (WakeState::Arming, WakeInput::Idle) => {
                self.state = WakeState::Sleeping;
                self.arming_started_at = None;
            }
            // A frame *with* a hand is real activity — refresh the idle baseline.
            // A frame *without* a hand (pose: None) means the user lowered their
            // hands; we leave last_activity_ms alone so the 5s idle counter
            // continues to advance and the engine eventually disarms.
            (WakeState::Armed, WakeInput::Pose { pose: Some(_), .. }) => {
                self.last_activity_ms = now_ms;
            }
            (WakeState::Armed, WakeInput::Pose { pose: None, .. }) if !self.confirm_pending => {
                if now_ms - self.last_activity_ms >= IDLE_TIMEOUT_MS {
                    self.state = WakeState::Sleeping;
                }
            }
            (WakeState::Armed, WakeInput::Idle) if !self.confirm_pending => {
                if now_ms - self.last_activity_ms >= IDLE_TIMEOUT_MS {
                    self.state = WakeState::Sleeping;
                }
            }
            _ => {}
        }
    }
}

impl Default for WakeMachine {
    fn default() -> Self {
        Self::new()
    }
}
