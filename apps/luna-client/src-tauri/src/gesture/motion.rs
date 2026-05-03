//! Motion analysis — sliding window of palm-center positions to detect
//! swipes (with direction). Pinch / rotate / tap are stubbed in v1; they
//! are extended in Phase 3 once Apple Vision returns reliable thumb-tip /
//! index-tip deltas.

use std::collections::VecDeque;

use crate::gesture::types::*;

const WINDOW: usize = 30;
const SWIPE_MIN_MAGNITUDE: f32 = 0.20;
const SWIPE_MAX_DURATION_MS: i64 = 350;

pub struct MotionAnalyzer {
    samples: VecDeque<(Landmark, i64)>,
}

impl MotionAnalyzer {
    pub fn new() -> Self {
        Self {
            samples: VecDeque::with_capacity(WINDOW),
        }
    }

    pub fn push(&mut self, frame: &HandFrame, ts_ms: i64) {
        if self.samples.len() == WINDOW {
            self.samples.pop_front();
        }
        // Landmark 9 is the middle-finger MCP — closest stable proxy for the palm center.
        self.samples.push_back((frame.landmarks[9], ts_ms));
    }

    pub fn classify(&self) -> Option<Motion> {
        if self.samples.len() < 5 {
            return None;
        }
        let (start, t0) = *self.samples.front()?;
        let (end, t1) = *self.samples.back()?;
        let dx = end.x - start.x;
        let dy = end.y - start.y;
        let mag = (dx * dx + dy * dy).sqrt();
        let dur = t1 - t0;
        if mag >= SWIPE_MIN_MAGNITUDE && dur > 0 && dur <= SWIPE_MAX_DURATION_MS {
            let dir = if dx.abs() > dy.abs() {
                if dx > 0.0 { Direction::Right } else { Direction::Left }
            } else if dy > 0.0 {
                Direction::Down
            } else {
                Direction::Up
            };
            return Some(Motion {
                kind: MotionKind::Swipe,
                direction: Some(dir),
                magnitude: mag.min(1.0),
                velocity: mag / (dur as f32 / 1000.0),
            });
        }
        Some(Motion {
            kind: MotionKind::None,
            direction: None,
            magnitude: 0.0,
            velocity: 0.0,
        })
    }

    pub fn clear(&mut self) {
        self.samples.clear();
    }
}

impl Default for MotionAnalyzer {
    fn default() -> Self {
        Self::new()
    }
}
