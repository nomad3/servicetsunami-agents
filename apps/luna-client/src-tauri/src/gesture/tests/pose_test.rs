use crate::gesture::pose::classify;
use crate::gesture::types::*;

fn lm(x: f32, y: f32, z: f32) -> Landmark {
    Landmark { x, y, z }
}

fn open_palm_landmarks() -> [Landmark; 21] {
    // Stylized: fingers extended away from the wrist (landmark 0 at origin).
    // Indices follow MediaPipe Hands convention (21 points; tips at 4/8/12/16/20).
    let mut a = [lm(0.0, 0.0, 0.0); 21];
    // Thumb chain (1..=4)
    a[1] = lm(-0.05, 0.05, 0.0);
    a[2] = lm(-0.10, 0.10, 0.0);
    a[3] = lm(-0.13, 0.14, 0.0);
    a[4] = lm(-0.20, 0.20, 0.0);
    // Index (5..=8)
    a[5] = lm(0.02, 0.10, 0.0);
    a[6] = lm(0.02, 0.18, 0.0);
    a[7] = lm(0.02, 0.24, 0.0);
    a[8] = lm(0.02, 0.30, 0.0);
    // Middle (9..=12)
    a[9] = lm(0.04, 0.10, 0.0);
    a[10] = lm(0.04, 0.20, 0.0);
    a[11] = lm(0.04, 0.27, 0.0);
    a[12] = lm(0.04, 0.34, 0.0);
    // Ring (13..=16)
    a[13] = lm(0.06, 0.10, 0.0);
    a[14] = lm(0.06, 0.18, 0.0);
    a[15] = lm(0.06, 0.24, 0.0);
    a[16] = lm(0.06, 0.30, 0.0);
    // Pinky (17..=20)
    a[17] = lm(0.08, 0.10, 0.0);
    a[18] = lm(0.08, 0.16, 0.0);
    a[19] = lm(0.08, 0.20, 0.0);
    a[20] = lm(0.08, 0.26, 0.0);
    a
}

fn fist_landmarks() -> [Landmark; 21] {
    // All tips curled within ~0.06 of wrist; PIPs all further from wrist than tips.
    let mut a = [lm(0.0, 0.0, 0.0); 21];
    // Wrist at origin
    // PIPs at moderate distance (extended position would be further)
    a[3] = lm(0.0, 0.10, 0.0);
    a[6] = lm(0.0, 0.10, 0.0);
    a[10] = lm(0.0, 0.10, 0.0);
    a[14] = lm(0.0, 0.10, 0.0);
    a[18] = lm(0.0, 0.10, 0.0);
    // Tips closer to wrist than PIPs (curled fingers)
    a[4] = lm(0.0, 0.04, 0.0);
    a[8] = lm(0.0, 0.04, 0.0);
    a[12] = lm(0.0, 0.04, 0.0);
    a[16] = lm(0.0, 0.04, 0.0);
    a[20] = lm(0.0, 0.04, 0.0);
    a
}

#[test]
fn classifies_open_palm() {
    let frame = HandFrame {
        handedness: Hand::Right,
        landmarks: open_palm_landmarks(),
        confidence: 0.9,
    };
    let (pose, fingers) = classify(&frame);
    assert_eq!(pose, Pose::OpenPalm);
    assert!(fingers.thumb && fingers.index && fingers.middle && fingers.ring && fingers.pinky);
}

#[test]
fn classifies_fist() {
    let frame = HandFrame {
        handedness: Hand::Right,
        landmarks: fist_landmarks(),
        confidence: 0.9,
    };
    let (pose, _fingers) = classify(&frame);
    assert_eq!(pose, Pose::Fist);
}
