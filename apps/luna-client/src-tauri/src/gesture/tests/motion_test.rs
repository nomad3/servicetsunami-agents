use crate::gesture::motion::MotionAnalyzer;
use crate::gesture::types::*;

fn lm(x: f32, y: f32) -> Landmark {
    Landmark { x, y, z: 0.0 }
}

fn frame_with_palm_at(palm_x: f32, palm_y: f32) -> HandFrame {
    let mut a = [lm(0.0, 0.0); 21];
    a[9] = lm(palm_x, palm_y);
    // Thumb tip / index tip placeholder positions (not pinching)
    a[4] = lm(0.0, 0.0);
    a[8] = lm(0.5, 0.0);
    // Pinky MCP / wrist for palm-angle (default unrotated)
    a[17] = lm(0.1, 0.0);
    a[0] = lm(0.0, 0.0);
    HandFrame {
        handedness: Hand::Right,
        landmarks: a,
        confidence: 0.9,
    }
}

fn frame_with_pinch(thumb_to_index: f32, palm_x: f32, ts_palm_y: f32) -> HandFrame {
    let mut a = [lm(0.0, 0.0); 21];
    a[9] = lm(palm_x, ts_palm_y);
    a[4] = lm(0.5 - thumb_to_index / 2.0, 0.5);
    a[8] = lm(0.5 + thumb_to_index / 2.0, 0.5);
    a[17] = lm(0.1, 0.0);
    a[0] = lm(0.0, 0.0);
    HandFrame {
        handedness: Hand::Right,
        landmarks: a,
        confidence: 0.9,
    }
}

fn frame_with_palm_angle(angle_rad: f32) -> HandFrame {
    let mut a = [lm(0.0, 0.0); 21];
    a[0] = lm(0.0, 0.0); // wrist
    a[17] = lm(angle_rad.cos() * 0.1, angle_rad.sin() * 0.1);
    a[9] = lm(0.5, 0.5);
    // Hold pinch open so tap detector doesn't trigger
    a[4] = lm(0.0, 0.0);
    a[8] = lm(0.4, 0.0);
    HandFrame {
        handedness: Hand::Right,
        landmarks: a,
        confidence: 0.9,
    }
}

#[test]
fn detects_swipe_right() {
    let mut a = MotionAnalyzer::new();
    for i in 0..10 {
        a.push(&frame_with_palm_at(i as f32 * 0.05, 0.5), 1_700_000_000_000 + i * 30);
    }
    let m = a.classify().expect("motion should classify");
    assert_eq!(m.kind, MotionKind::Swipe);
    assert_eq!(m.direction, Some(Direction::Right));
}

#[test]
fn idle_palm_returns_none_kind() {
    let mut a = MotionAnalyzer::new();
    for i in 0..10 {
        a.push(&frame_with_palm_at(0.5, 0.5), 1_700_000_000_000 + i * 30);
    }
    let m = a.classify().unwrap();
    assert_eq!(m.kind, MotionKind::None);
}

#[test]
fn empty_buffer_returns_none() {
    let a = MotionAnalyzer::new();
    assert!(a.classify().is_none());
}

#[test]
fn detects_pinch_in() {
    let mut a = MotionAnalyzer::new();
    // Start with thumb+index spread 0.30 apart, close to 0.05 over 10 frames.
    for i in 0..10 {
        let d = 0.30 - (i as f32) * 0.025;
        a.push(&frame_with_pinch(d, 0.5, 0.5), 1_700_000_000_000 + i * 30);
    }
    let m = a.classify().expect("pinch must classify");
    assert_eq!(m.kind, MotionKind::Pinch);
    assert_eq!(m.direction, Some(Direction::In));
}

#[test]
fn detects_rotate_cw() {
    let mut a = MotionAnalyzer::new();
    // Sweep palm angle from 0 to ~60° (1.05 rad) over 10 frames.
    for i in 0..10 {
        let angle = (i as f32) * 0.12;
        a.push(&frame_with_palm_angle(angle), 1_700_000_000_000 + i * 30);
    }
    let m = a.classify().expect("rotate must classify");
    assert_eq!(m.kind, MotionKind::Rotate);
    assert_eq!(m.direction, Some(Direction::Cw));
}

#[test]
fn detects_sweep_left() {
    let mut a = MotionAnalyzer::new();
    // Large slow lateral motion — palm sweeps from x=0.85 to x=0.15 over 600ms.
    for i in 0..20 {
        let x = 0.85 - (i as f32) * 0.035;
        a.push(&frame_with_palm_at(x, 0.5), 1_700_000_000_000 + i * 30);
    }
    let m = a.classify().expect("sweep must classify");
    assert_eq!(m.kind, MotionKind::Sweep);
    assert_eq!(m.direction, Some(Direction::Left));
}

#[test]
fn small_fast_motion_is_swipe_not_sweep() {
    let mut a = MotionAnalyzer::new();
    // Magnitude 0.30, duration ~270ms — too small for sweep, fits swipe.
    for i in 0..10 {
        a.push(&frame_with_palm_at(i as f32 * 0.033, 0.5), 1_700_000_000_000 + i * 30);
    }
    let m = a.classify().unwrap();
    assert_eq!(m.kind, MotionKind::Swipe);
}

#[test]
fn detects_tap() {
    let mut a = MotionAnalyzer::new();
    // Open → close → open in ~150ms.
    for i in 0..3 {
        a.push(&frame_with_pinch(0.30, 0.5, 0.5), 1_700_000_000_000 + i * 25);
    }
    a.push(&frame_with_pinch(0.04, 0.5, 0.5), 1_700_000_000_075);
    a.push(&frame_with_pinch(0.04, 0.5, 0.5), 1_700_000_000_100);
    a.push(&frame_with_pinch(0.30, 0.5, 0.5), 1_700_000_000_150);
    let m = a.classify().expect("tap must classify");
    assert_eq!(m.kind, MotionKind::Tap);
}
