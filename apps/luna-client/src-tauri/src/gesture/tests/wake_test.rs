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
fn pose_flicker_during_arming_does_not_reset() {
    // Real cameras + the pose classifier flicker frame-to-frame as the
    // user's fingers settle into position. The old behaviour reset the
    // wake machine to Sleeping the moment classification went OpenPalm →
    // Three / Fist / ThumbUp, which made the 500ms wake hold practically
    // unreachable on real hardware (live diagnostic 2026-05-05 showed
    // wake never reached Armed despite confident hand detection). The
    // fix: tolerate non-OpenPalm poses while the hand is still visible
    // at decent confidence, and only abort Arming when the hand
    // disappears or confidence collapses.
    let mut m = WakeMachine::new();
    m.tick(
        WakeInput::Pose { pose: Some(Pose::OpenPalm), confidence: 0.9 },
        0,
    );
    assert_eq!(m.state(), WakeState::Arming);
    // Pose classifier flicker — same hand, briefly classified as Fist.
    m.tick(
        WakeInput::Pose { pose: Some(Pose::Fist), confidence: 0.9 },
        200,
    );
    assert_eq!(m.state(), WakeState::Arming, "flicker should not abort hold");
    // Recover to OpenPalm and finish the 500ms hold.
    m.tick(
        WakeInput::Pose { pose: Some(Pose::OpenPalm), confidence: 0.95 },
        600,
    );
    assert_eq!(m.state(), WakeState::Armed, "hold should complete after flicker");
}

#[test]
fn arming_aborts_when_hand_disappears() {
    // Flicker tolerance must not turn into hang-on-forever — when the
    // user actually lowers their hand (pose: None), we DO want to abort
    // back to Sleeping immediately.
    let mut m = WakeMachine::new();
    m.tick(
        WakeInput::Pose { pose: Some(Pose::OpenPalm), confidence: 0.9 },
        0,
    );
    assert_eq!(m.state(), WakeState::Arming);
    m.tick(WakeInput::Pose { pose: None, confidence: 0.0 }, 200);
    assert_eq!(m.state(), WakeState::Sleeping);
}

#[test]
fn arming_aborts_on_confidence_collapse() {
    // A confident OpenPalm followed by a frame where confidence collapses
    // (e.g. hand moved out of frame, partial occlusion) should also abort.
    let mut m = WakeMachine::new();
    m.tick(
        WakeInput::Pose { pose: Some(Pose::OpenPalm), confidence: 0.9 },
        0,
    );
    m.tick(
        WakeInput::Pose { pose: Some(Pose::Three), confidence: 0.2 },
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
fn empty_frames_after_arming_disarm() {
    // Issue #1 from holistic review: previously, every Pose frame (including
    // pose: None) refreshed last_activity_ms, so the engine never disarmed
    // while the camera kept feeding empty frames. This test guards against
    // that regression.
    let mut m = WakeMachine::new();
    m.tick(
        WakeInput::Pose { pose: Some(Pose::OpenPalm), confidence: 0.9 },
        0,
    );
    m.tick(
        WakeInput::Pose { pose: Some(Pose::OpenPalm), confidence: 0.9 },
        600,
    );
    assert_eq!(m.state(), WakeState::Armed);

    // Stream of "no hands detected" frames. Activity should NOT refresh.
    for ts in (700..6000).step_by(33) {
        m.tick(WakeInput::Pose { pose: None, confidence: 0.0 }, ts);
    }
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
