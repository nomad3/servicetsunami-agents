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
    Five,
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
