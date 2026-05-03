//! Wake-gesture state machine. Defaults to Sleeping. An open-palm pose held
//! for 500 ms transitions to Armed. While Armed, 5 s of idle returns to
//! Sleeping unless a destructive-confirm window is in flight (in which case
//! the idle countdown is suspended).

use crate::gesture::types::{Pose, WakeState};

const ARM_HOLD_MS: i64 = 500;
const IDLE_TIMEOUT_MS: i64 = 5000;
const ARM_CONFIDENCE: f32 = 0.85;

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
            (WakeState::Sleeping, WakeInput::Pose { pose: Some(Pose::OpenPalm), confidence })
                if confidence >= ARM_CONFIDENCE =>
            {
                self.state = WakeState::Arming;
                self.arming_started_at = Some(now_ms);
            }
            (WakeState::Arming, WakeInput::Pose { pose: Some(Pose::OpenPalm), confidence })
                if confidence >= ARM_CONFIDENCE =>
            {
                if let Some(start) = self.arming_started_at {
                    if now_ms - start >= ARM_HOLD_MS {
                        self.state = WakeState::Armed;
                        self.last_activity_ms = now_ms;
                        self.arming_started_at = None;
                    }
                }
            }
            (WakeState::Arming, WakeInput::Pose { .. }) => {
                self.state = WakeState::Sleeping;
                self.arming_started_at = None;
            }
            (WakeState::Armed, WakeInput::Pose { .. }) => {
                self.last_activity_ms = now_ms;
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
