//! Combines pose + motion detection into emitted GestureEvents. Debounces
//! emission to one event per 80 ms so a single gesture doesn't fire its
//! bound action multiple times.

use ulid::Ulid;

use crate::gesture::motion::MotionAnalyzer;
use crate::gesture::pose::classify;
use crate::gesture::types::*;

const DEBOUNCE_MS: i64 = 80;

pub struct Recognizer {
    motion: MotionAnalyzer,
    last_emit_ms: i64,
}

impl Recognizer {
    pub fn new() -> Self {
        Self {
            motion: MotionAnalyzer::new(),
            last_emit_ms: 0,
        }
    }

    /// Ingest a frame. Returns a GestureEvent if one should be emitted now,
    /// plus the classified pose (for the wake state machine).
    pub fn ingest(
        &mut self,
        hands: Vec<HandFrame>,
        now_ms: i64,
    ) -> (Option<GestureEvent>, Option<Pose>) {
        let primary = match hands.first() {
            Some(h) => h.clone(),
            None => return (None, None),
        };
        let (pose, fingers) = classify(&primary);
        self.motion.push(&primary, now_ms);

        if now_ms - self.last_emit_ms < DEBOUNCE_MS {
            return (None, Some(pose));
        }

        let motion = self.motion.classify();
        let event = GestureEvent {
            id: Ulid::new().to_string(),
            ts: now_ms,
            pose,
            fingers_extended: fingers,
            motion,
            hand: primary.handedness,
            confidence: primary.confidence,
        };
        self.last_emit_ms = now_ms;
        // Clear the motion buffer after emitting a successful swipe so the
        // same swipe doesn't keep firing as the buffer scrolls. Without this
        // a single 3-finger swipe up would trigger nav_hud ~12 times.
        if let Some(m) = motion {
            if matches!(m.kind, MotionKind::Swipe | MotionKind::Pinch | MotionKind::Tap) {
                self.motion.clear();
            }
        }
        (Some(event), Some(pose))
    }
}

impl Default for Recognizer {
    fn default() -> Self {
        Self::new()
    }
}
