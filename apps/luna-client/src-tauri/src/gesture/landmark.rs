//! Landmark extraction trait. The actual implementation is platform-specific
//! (Apple Vision on macOS via Swift FFI). A null extractor is provided so the
//! engine can compile and run on non-macOS targets without the full pipeline.

use crate::gesture::types::*;

pub trait LandmarkExtractor: Send + Sync {
    /// Extract up to two hand frames from an RGB888 buffer. Returns an empty
    /// vector if no hands are detected.
    fn extract(&self, rgb: &[u8], width: u32, height: u32) -> Vec<HandFrame>;
}

/// Fallback extractor used on non-macOS targets and when the real extractor
/// fails to initialize. Always returns no hands, so the engine effectively
/// runs in a "no hands ever detected" mode.
pub struct NullExtractor;

impl LandmarkExtractor for NullExtractor {
    fn extract(&self, _rgb: &[u8], _width: u32, _height: u32) -> Vec<HandFrame> {
        Vec::new()
    }
}
