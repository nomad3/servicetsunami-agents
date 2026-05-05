//! Camera capture via nokhwa (AVFoundation on macOS). Single owner of the
//! webcam handle; frames are fanned out to the landmark extractor and to the
//! Spatial HUD's existing `spatial-frame` consumer.

use tokio::sync::mpsc;
use tokio::task;

use nokhwa::pixel_format::RgbFormat;
use nokhwa::utils::{ApiBackend, CameraIndex, RequestedFormat, RequestedFormatType};
use nokhwa::Camera;

// Manual Debug impl so the enclosing `CameraEvent::Frame(Frame)` (which
// derives Debug) compiles, without dumping the entire RGB buffer in logs.
#[derive(Clone)]
pub struct Frame {
    pub width: u32,
    pub height: u32,
    pub rgb: Vec<u8>,
    pub ts_ms: i64,
}

impl std::fmt::Debug for Frame {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Frame")
            .field("width", &self.width)
            .field("height", &self.height)
            .field("rgb_len", &self.rgb.len())
            .field("ts_ms", &self.ts_ms)
            .finish()
    }
}

#[derive(Debug, Clone)]
pub enum CameraEvent {
    Frame(Frame),
    Disconnected,
    Error(String),
}

pub struct CameraStream {
    pub rx: mpsc::Receiver<CameraEvent>,
    pub stop_tx: mpsc::Sender<()>,
}

pub fn list_devices() -> Vec<(usize, String)> {
    nokhwa::query(ApiBackend::AVFoundation)
        .unwrap_or_default()
        .into_iter()
        .enumerate()
        .map(|(i, info)| (i, info.human_name()))
        .collect()
}

pub fn start(index: usize, fps_target: u32) -> Result<CameraStream, String> {
    let (tx, rx) = mpsc::channel::<CameraEvent>(8);
    let (stop_tx, mut stop_rx) = mpsc::channel::<()>(1);

    task::spawn_blocking(move || {
        let format =
            RequestedFormat::new::<RgbFormat>(RequestedFormatType::AbsoluteHighestFrameRate);
        let mut camera = match Camera::new(CameraIndex::Index(index as u32), format) {
            Ok(c) => c,
            Err(e) => {
                let _ = tx.blocking_send(CameraEvent::Error(format!("camera init: {e}")));
                return;
            }
        };
        if let Err(e) = camera.open_stream() {
            let _ = tx.blocking_send(CameraEvent::Error(format!("open_stream: {e}")));
            return;
        }
        let frame_dur = std::time::Duration::from_millis((1000 / fps_target.max(1)) as u64);
        loop {
            if stop_rx.try_recv().is_ok() {
                break;
            }
            match camera.frame() {
                Ok(buf) => {
                    let img = match buf.decode_image::<RgbFormat>() {
                        Ok(i) => i,
                        Err(e) => {
                            let _ = tx.blocking_send(CameraEvent::Error(format!("decode: {e}")));
                            continue;
                        }
                    };
                    let now_ms = std::time::SystemTime::now()
                        .duration_since(std::time::UNIX_EPOCH)
                        .unwrap_or_default()
                        .as_millis() as i64;
                    let frame = Frame {
                        width: img.width(),
                        height: img.height(),
                        rgb: img.into_raw(),
                        ts_ms: now_ms,
                    };
                    if tx.blocking_send(CameraEvent::Frame(frame)).is_err() {
                        break;
                    }
                }
                Err(e) => {
                    let _ = tx.blocking_send(CameraEvent::Error(format!("frame: {e}")));
                }
            }
            std::thread::sleep(frame_dur);
        }
        let _ = camera.stop_stream();
    });

    Ok(CameraStream { rx, stop_tx })
}
