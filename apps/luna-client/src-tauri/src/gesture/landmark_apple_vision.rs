//! Apple Vision-backed landmark extractor for macOS.
//!
//! v1 ships with a pure-Rust placeholder that returns no landmarks. The actual
//! `VNDetectHumanHandPoseRequest` integration via Swift FFI is the next ticket
//! after the Phase 1 frame plumbing lands; the placeholder lets the rest of
//! the engine (camera, wake state, recognizer, Tauri events) be exercised
//! end-to-end while the Swift bridge work is in flight.
//!
//! When the FFI lands, the body of `extract` will call out to a Swift
//! `luna_extract_landmarks` symbol, parse the returned float buffer into
//! 21-landmark hands, and return them.

use crate::gesture::landmark::LandmarkExtractor;
use crate::gesture::types::*;

pub struct AppleVisionExtractor;

impl AppleVisionExtractor {
    pub fn new() -> Self {
        Self
    }
}

impl LandmarkExtractor for AppleVisionExtractor {
    fn extract(&self, _rgb: &[u8], _width: u32, _height: u32) -> Vec<HandFrame> {
        // TODO(luna-gestures-ffi): replace with extern "C" call to
        // `luna_extract_landmarks` (Swift) once the build.rs swift-bridge
        // wiring lands. Returning empty here lets the rest of the engine
        // run in a "no hands detected" state, which exercises the wake
        // state machine's idle path and the React UI's overlay.
        Vec::new()
    }
}
