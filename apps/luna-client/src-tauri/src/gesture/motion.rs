//! Motion analysis — sliding window of palm-center positions to detect
//! swipes, pinches (thumb-tip ↔ index-tip distance derivative), rotations
//! (palm-axis angular velocity), and taps (pinch open→close→open within
//! 200ms). All four signal types share the same 30-frame ring buffer.

use std::collections::VecDeque;

use crate::gesture::types::*;

const WINDOW: usize = 30;
const SWIPE_MIN_MAGNITUDE: f32 = 0.20;
const SWIPE_MAX_DURATION_MS: i64 = 350;

const PINCH_DELTA_THRESHOLD: f32 = 0.10;
const PINCH_MAX_DURATION_MS: i64 = 600;

const TAP_MAX_DURATION_MS: i64 = 200;
const TAP_PINCH_THRESHOLD: f32 = 0.06;

const ROTATE_DELTA_RADIANS: f32 = 0.6; // ~34°
const ROTATE_MAX_DURATION_MS: i64 = 600;

// Sweep-arm: bigger and slower than a swipe. Captures the conductor's
// "bring this section in" or "out" motion — open palm moves laterally a
// large distance (>0.5 normalized) over 0.4–1.2s.
const SWEEP_MIN_MAGNITUDE: f32 = 0.50;
const SWEEP_MIN_DURATION_MS: i64 = 400;
const SWEEP_MAX_DURATION_MS: i64 = 1200;

#[derive(Clone, Copy)]
struct Sample {
    palm: Landmark,        // landmark 9 — middle MCP, palm-center proxy
    thumb_tip: Landmark,   // landmark 4
    index_tip: Landmark,   // landmark 8
    pinky_mcp: Landmark,   // landmark 17 — used with wrist for palm axis
    wrist: Landmark,       // landmark 0
    ts: i64,
}

pub struct MotionAnalyzer {
    samples: VecDeque<Sample>,
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
        let lm = &frame.landmarks;
        self.samples.push_back(Sample {
            palm: lm[9],
            thumb_tip: lm[4],
            index_tip: lm[8],
            pinky_mcp: lm[17],
            wrist: lm[0],
            ts: ts_ms,
        });
    }

    pub fn classify(&self) -> Option<Motion> {
        if self.samples.len() < 5 {
            return None;
        }

        // Try the more-specific motions first (tap is a pinch open→close→open
        // within 200ms; pinch is a sustained distance change; rotate is palm
        // angle velocity; sweep is a large slow lateral palm move). Swipe is
        // the fallback for the small/fast lateral case.
        if let Some(m) = self.classify_tap() { return Some(m); }
        if let Some(m) = self.classify_pinch() { return Some(m); }
        if let Some(m) = self.classify_rotate() { return Some(m); }
        if let Some(m) = self.classify_sweep() { return Some(m); }
        if let Some(m) = self.classify_swipe() { return Some(m); }

        Some(Motion {
            kind: MotionKind::None,
            direction: None,
            magnitude: 0.0,
            velocity: 0.0,
        })
    }

    fn classify_swipe(&self) -> Option<Motion> {
        let start = self.samples.front()?;
        let end = self.samples.back()?;
        let dx = end.palm.x - start.palm.x;
        let dy = end.palm.y - start.palm.y;
        let mag = (dx * dx + dy * dy).sqrt();
        let dur = end.ts - start.ts;
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
        None
    }

    fn classify_pinch(&self) -> Option<Motion> {
        let start = self.samples.front()?;
        let end = self.samples.back()?;
        let dur = end.ts - start.ts;
        if dur <= 0 || dur > PINCH_MAX_DURATION_MS {
            return None;
        }
        let d_start = pinch_distance(start);
        let d_end = pinch_distance(end);
        let delta = d_end - d_start;
        if delta.abs() < PINCH_DELTA_THRESHOLD {
            return None;
        }
        let direction = if delta < 0.0 { Direction::In } else { Direction::Out };
        Some(Motion {
            kind: MotionKind::Pinch,
            direction: Some(direction),
            magnitude: delta.abs().min(1.0),
            velocity: delta.abs() / (dur as f32 / 1000.0),
        })
    }

    fn classify_tap(&self) -> Option<Motion> {
        // A tap is a tight open→close→open sequence: thumb–index distance
        // dips below TAP_PINCH_THRESHOLD then recovers, all within 200ms.
        // Look for it in the last ~6 samples (≈200ms at 30fps).
        let n = self.samples.len();
        if n < 4 { return None; }
        let tail: Vec<&Sample> = self.samples.iter().rev().take(8).collect();
        let dur = tail.first()?.ts - tail.last()?.ts;
        if dur <= 0 || dur > TAP_MAX_DURATION_MS {
            return None;
        }
        let mut min_dist = f32::INFINITY;
        let mut max_dist_after = 0.0f32;
        let mut min_idx = 0usize;
        for (i, s) in tail.iter().rev().enumerate() {
            let d = pinch_distance(s);
            if d < min_dist {
                min_dist = d;
                min_idx = i;
            }
        }
        for s in tail.iter().rev().skip(min_idx + 1) {
            let d = pinch_distance(s);
            if d > max_dist_after { max_dist_after = d; }
        }
        if min_dist < TAP_PINCH_THRESHOLD && max_dist_after > TAP_PINCH_THRESHOLD * 2.0 {
            return Some(Motion {
                kind: MotionKind::Tap,
                direction: None,
                magnitude: 1.0,
                velocity: 0.0,
            });
        }
        None
    }

    fn classify_sweep(&self) -> Option<Motion> {
        let start = self.samples.front()?;
        let end = self.samples.back()?;
        let dx = end.palm.x - start.palm.x;
        let dy = end.palm.y - start.palm.y;
        let mag = (dx * dx + dy * dy).sqrt();
        let dur = end.ts - start.ts;
        if mag < SWEEP_MIN_MAGNITUDE { return None; }
        if dur < SWEEP_MIN_DURATION_MS || dur > SWEEP_MAX_DURATION_MS { return None; }
        // Sweep is dominantly horizontal — reject if the vertical component
        // is bigger.
        if dy.abs() > dx.abs() { return None; }
        let direction = if dx > 0.0 { Direction::Right } else { Direction::Left };
        Some(Motion {
            kind: MotionKind::Sweep,
            direction: Some(direction),
            magnitude: mag.min(1.0),
            velocity: mag / (dur as f32 / 1000.0),
        })
    }

    fn classify_rotate(&self) -> Option<Motion> {
        let start = self.samples.front()?;
        let end = self.samples.back()?;
        let dur = end.ts - start.ts;
        if dur <= 0 || dur > ROTATE_MAX_DURATION_MS {
            return None;
        }
        let angle_start = palm_angle(start);
        let angle_end = palm_angle(end);
        let mut delta = angle_end - angle_start;
        // Normalize to (-π, π]
        while delta > std::f32::consts::PI { delta -= 2.0 * std::f32::consts::PI; }
        while delta <= -std::f32::consts::PI { delta += 2.0 * std::f32::consts::PI; }
        if delta.abs() < ROTATE_DELTA_RADIANS {
            return None;
        }
        let direction = if delta > 0.0 { Direction::Cw } else { Direction::Ccw };
        Some(Motion {
            kind: MotionKind::Rotate,
            direction: Some(direction),
            magnitude: (delta.abs() / std::f32::consts::PI).min(1.0),
            velocity: delta.abs() / (dur as f32 / 1000.0),
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

fn pinch_distance(s: &Sample) -> f32 {
    let dx = s.thumb_tip.x - s.index_tip.x;
    let dy = s.thumb_tip.y - s.index_tip.y;
    let dz = s.thumb_tip.z - s.index_tip.z;
    (dx * dx + dy * dy + dz * dz).sqrt()
}

fn palm_angle(s: &Sample) -> f32 {
    // Angle of the wrist→pinky-MCP vector — proxy for palm rotation around
    // the wrist axis, normalized to [-π, π].
    let dx = s.pinky_mcp.x - s.wrist.x;
    let dy = s.pinky_mcp.y - s.wrist.y;
    dy.atan2(dx)
}
