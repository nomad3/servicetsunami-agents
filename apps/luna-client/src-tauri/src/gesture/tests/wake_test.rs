use crate::gesture::types::{Pose, WakeState};
use crate::gesture::wake::{WakeInput, WakeMachine};

#[test]
fn sleeps_initially() {
    let m = WakeMachine::new();
    assert_eq!(m.state(), WakeState::Sleeping);
}

#[test]
fn open_palm_500ms_arms() {
    let mut m = WakeMachine::new();
    m.tick(
        WakeInput::Pose {
            pose: Some(Pose::OpenPalm),
            confidence: 0.9,
        },
        0,
    );
    assert_eq!(m.state(), WakeState::Arming);
    m.tick(
        WakeInput::Pose {
            pose: Some(Pose::OpenPalm),
            confidence: 0.9,
        },
        600,
    );
    assert_eq!(m.state(), WakeState::Armed);
}

#[test]
fn pose_change_during_arming_returns_to_sleeping() {
    let mut m = WakeMachine::new();
    m.tick(
        WakeInput::Pose {
            pose: Some(Pose::OpenPalm),
            confidence: 0.9,
        },
        0,
    );
    m.tick(
        WakeInput::Pose {
            pose: Some(Pose::Fist),
            confidence: 0.9,
        },
        200,
    );
    assert_eq!(m.state(), WakeState::Sleeping);
}

#[test]
fn idle_5s_disarms() {
    let mut m = WakeMachine::new();
    m.tick(
        WakeInput::Pose {
            pose: Some(Pose::OpenPalm),
            confidence: 0.9,
        },
        0,
    );
    m.tick(
        WakeInput::Pose {
            pose: Some(Pose::OpenPalm),
            confidence: 0.9,
        },
        600,
    );
    assert_eq!(m.state(), WakeState::Armed);
    m.tick(WakeInput::Idle, 6000);
    assert_eq!(m.state(), WakeState::Sleeping);
}

#[test]
fn confirm_pending_freezes_idle_timer() {
    let mut m = WakeMachine::new();
    m.tick(
        WakeInput::Pose {
            pose: Some(Pose::OpenPalm),
            confidence: 0.9,
        },
        0,
    );
    m.tick(
        WakeInput::Pose {
            pose: Some(Pose::OpenPalm),
            confidence: 0.9,
        },
        600,
    );
    m.set_confirm_pending(true);
    m.tick(WakeInput::Idle, 7000);
    assert_eq!(m.state(), WakeState::Armed);
    m.set_confirm_pending(false);
    // Refresh wake's idea of "now" so the idle clock counts from clear-time.
    m.tick(
        WakeInput::Pose {
            pose: Some(Pose::OpenPalm),
            confidence: 0.9,
        },
        7000,
    );
    m.tick(WakeInput::Idle, 13000);
    assert_eq!(m.state(), WakeState::Sleeping);
}
