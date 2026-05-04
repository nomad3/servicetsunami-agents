//! Core types for the gesture engine. Shared across modules and serialized to
//! the React frontend via Tauri events.

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Pose {
    OpenPalm,
    Fist,
    Point,
    Peace,
    Three,
    Four,
    // Note: "five fingers extended" geometry maps to OpenPalm in pose::classify;
    // there is no separate `Five` variant. See pose.rs.
    ThumbUp,
    PinchPose,
    RotationPose,
    Custom,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Hand {
    Left,
    Right,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MotionKind {
    Swipe,
    Pinch,
    Rotate,
    Tap,
    /// Sweep-arm — large, slow, sustained horizontal palm motion (the
    /// "bring section in" / "section out" conducting gesture). Distinct
    /// from Swipe in that it requires open-palm pose, longer duration,
    /// and larger magnitude.
    Sweep,
    None,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Direction {
    Up,
    Down,
    Left,
    Right,
    In,
    Out,
    Cw,
    Ccw,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
pub struct FingersExtended {
    pub thumb: bool,
    pub index: bool,
    pub middle: bool,
    pub ring: bool,
    pub pinky: bool,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
pub struct Motion {
    pub kind: MotionKind,
    pub direction: Option<Direction>,
    pub magnitude: f32,
    pub velocity: f32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GestureEvent {
    pub id: String,
    pub ts: i64,
    pub pose: Pose,
    pub fingers_extended: FingersExtended,
    pub motion: Option<Motion>,
    pub hand: Hand,
    pub confidence: f32,
    /// Index-fingertip xy in normalized [0,1] image space when the pose is
    /// `Point`; None otherwise. Lets the React `LunaCursor` overlay actually
    /// track the fingertip instead of sitting at screen centre.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tip_xy: Option<(f32, f32)>,
    /// Two-handed pose info — present when both hands are visible. The
    /// React side reads this to detect "both hands rising" (crescendo),
    /// "both hands falling" (diminuendo), and two-handed framing.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub two_handed: Option<TwoHanded>,
}

/// Compact summary of the secondary hand when two are present, plus
/// whether their motion is mirrored (rising/falling/framing).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TwoHanded {
    pub other_pose: Pose,
    pub other_hand: Hand,
    /// Vertical motion of both palms over the last ~600ms. Positive =
    /// both palms rising in image-space y (which is screen-down due to
    /// y-flip in the Swift bridge → user is moving them up).
    pub coordinated_dy: f32,
    /// Horizontal spread between the two palms over the last ~600ms.
    /// Positive = palms moving apart (frame opening), negative = palms
    /// closing (frame closing).
    pub spread_dx: f32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum WakeState {
    Sleeping,
    Arming,
    Armed,
    Fatal,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EngineStatus {
    pub state: String,
    pub fps: f32,
    pub last_error: Option<String>,
}

#[derive(Debug, Clone, Copy)]
pub struct Landmark {
    pub x: f32,
    pub y: f32,
    pub z: f32,
}

#[derive(Debug, Clone)]
pub struct HandFrame {
    pub handedness: Hand,
    pub landmarks: [Landmark; 21],
    pub confidence: f32,
}
