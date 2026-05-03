//! Geometric pose classification from 21-landmark hand frames.
//!
//! For each finger, "extended" is decided by comparing the tip-to-wrist
//! distance to the PIP-joint-to-wrist distance. The combination of which
//! fingers are extended maps to the named poses (open palm, fist, peace,
//! three, four, five, point, thumbs-up).

use crate::gesture::types::*;

const TIP_INDICES: [usize; 5] = [4, 8, 12, 16, 20];
const PIP_INDICES: [usize; 5] = [3, 6, 10, 14, 18];
const WRIST: usize = 0;

fn dist(a: Landmark, b: Landmark) -> f32 {
    let dx = a.x - b.x;
    let dy = a.y - b.y;
    let dz = a.z - b.z;
    (dx * dx + dy * dy + dz * dz).sqrt()
}

fn finger_extended(lm: &[Landmark; 21], tip_idx: usize, pip_idx: usize) -> bool {
    dist(lm[tip_idx], lm[WRIST]) > dist(lm[pip_idx], lm[WRIST])
}

pub fn classify(frame: &HandFrame) -> (Pose, FingersExtended) {
    let lm = &frame.landmarks;
    let extended: [bool; 5] =
        std::array::from_fn(|i| finger_extended(lm, TIP_INDICES[i], PIP_INDICES[i]));
    let fingers = FingersExtended {
        thumb: extended[0],
        index: extended[1],
        middle: extended[2],
        ring: extended[3],
        pinky: extended[4],
    };
    let count_non_thumb = extended[1..].iter().filter(|b| **b).count();
    // OpenPalm and Five are the same canonical pose for our grammar — both
    // mean thumb + 4 fingers extended. We map the geometry to OpenPalm.
    let pose = match (extended[0], count_non_thumb, extended[1], extended[2], extended[3], extended[4]) {
        (_, 0, _, _, _, _) => {
            if extended[0] { Pose::ThumbUp } else { Pose::Fist }
        }
        (true, 4, _, _, _, _) => Pose::OpenPalm,
        (false, 4, _, _, _, _) => Pose::Four,
        (_, 1, true, false, false, false) => Pose::Point,
        (_, 2, true, true, false, false) => Pose::Peace,
        (_, 3, true, true, true, false) => Pose::Three,
        _ => Pose::Custom,
    };
    (pose, fingers)
}
