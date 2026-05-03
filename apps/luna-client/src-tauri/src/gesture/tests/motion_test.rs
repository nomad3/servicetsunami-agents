use crate::gesture::motion::MotionAnalyzer;
use crate::gesture::types::*;

fn lm(x: f32, y: f32) -> Landmark {
    Landmark { x, y, z: 0.0 }
}

fn frame_at(palm_x: f32, palm_y: f32) -> HandFrame {
    let mut a = [lm(0.0, 0.0); 21];
    a[9] = lm(palm_x, palm_y);
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
        a.push(&frame_at(i as f32 * 0.05, 0.5), 1_700_000_000_000 + i * 30);
    }
    let m = a.classify().expect("motion should classify");
    assert_eq!(m.kind, MotionKind::Swipe);
    assert_eq!(m.direction, Some(Direction::Right));
}

#[test]
fn idle_palm_returns_none_kind() {
    let mut a = MotionAnalyzer::new();
    for i in 0..10 {
        a.push(&frame_at(0.5, 0.5), 1_700_000_000_000 + i * 30);
    }
    let m = a.classify().unwrap();
    assert_eq!(m.kind, MotionKind::None);
}

#[test]
fn empty_buffer_returns_none() {
    let a = MotionAnalyzer::new();
    assert!(a.classify().is_none());
}
