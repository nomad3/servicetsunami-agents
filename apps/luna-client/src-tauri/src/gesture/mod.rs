//! Luna gesture engine — in-process Rust module.
//!
//! Owns webcam capture and hand-landmark recognition; emits GestureEvent
//! over Tauri events to the React frontend. Runs as a Tokio task spawned
//! from `setup` in `lib.rs`, supervised by `supervisor::run`.

pub mod types;
#[cfg(target_os = "macos")]
pub mod camera;
pub mod landmark;
#[cfg(target_os = "macos")]
pub mod landmark_apple_vision;
pub mod pose;
pub mod motion;
pub mod wake;
pub mod recognizer;
pub mod supervisor;

#[cfg(test)]
mod tests;

pub use supervisor::{
    engine_status, install_app_handle, list_cameras, pause_engine, resume_engine,
    set_camera_index, start_engine, stop_engine,
};
pub use types::{EngineStatus, GestureEvent, WakeState};
