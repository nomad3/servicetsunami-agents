//! Apple Vision-backed landmark extractor for macOS. Calls into Swift via
//! a C-callable symbol declared in `swift/HandLandmarker.swift` and built
//! by `build.rs` into a static library linked at compile time.

use crate::gesture::landmark::LandmarkExtractor;
use crate::gesture::types::*;

extern "C" {
    fn luna_extract_landmarks(
        rgb_bytes: *const u8,
        width: i32,
        height: i32,
        out_buf: *mut f32,
        out_confidence: *mut f32,
        out_handedness_left: *mut u8,
    ) -> i32;
}

pub struct AppleVisionExtractor;

impl AppleVisionExtractor {
    pub fn new() -> Self {
        Self
    }
}

impl Default for AppleVisionExtractor {
    fn default() -> Self {
        Self::new()
    }
}

impl LandmarkExtractor for AppleVisionExtractor {
    fn extract(&self, rgb: &[u8], width: u32, height: u32) -> Vec<HandFrame> {
        if rgb.len() < (width as usize) * (height as usize) * 3 {
            return Vec::new();
        }
        let mut buf = [0f32; 126]; // 2 hands × 21 × 3
        let mut conf = [0f32; 2];
        let mut left = [0u8; 2];
        let n = unsafe {
            luna_extract_landmarks(
                rgb.as_ptr(),
                width as i32,
                height as i32,
                buf.as_mut_ptr(),
                conf.as_mut_ptr(),
                left.as_mut_ptr(),
            )
        };
        let n = n.max(0) as usize;
        let n = n.min(2);
        (0..n)
            .map(|h| {
                let mut lm = [Landmark { x: 0.0, y: 0.0, z: 0.0 }; 21];
                for i in 0..21 {
                    let base = (h * 21 + i) * 3;
                    lm[i] = Landmark {
                        x: buf[base],
                        y: buf[base + 1],
                        z: buf[base + 2],
                    };
                }
                HandFrame {
                    handedness: if left[h] == 1 { Hand::Left } else { Hand::Right },
                    landmarks: lm,
                    confidence: conf[h],
                }
            })
            .collect()
    }
}
