//! Engine supervisor — owns the engine's lifecycle, restart budget, and
//! Tauri event emission. Spawns one Tokio task that polls the camera for
//! frames, runs the landmark extractor, drives the wake state machine, and
//! emits gesture-event / wake-state-changed / engine-status events.

use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

use once_cell::sync::Lazy;
use tokio::sync::Mutex;
use tokio::task::JoinHandle;
use tokio::time::Duration;

use tauri::{AppHandle, Emitter};

#[cfg(target_os = "macos")]
use crate::gesture::camera::{self, CameraEvent};
#[cfg(target_os = "macos")]
use crate::gesture::landmark::LandmarkExtractor;
#[cfg(target_os = "macos")]
use crate::gesture::landmark_apple_vision::AppleVisionExtractor;
use crate::gesture::recognizer::Recognizer;
use crate::gesture::types::*;
use crate::gesture::wake::{WakeInput, WakeMachine};

static RUNNING: AtomicBool = AtomicBool::new(false);
static PAUSED: AtomicBool = AtomicBool::new(false);
static CAMERA_INDEX: AtomicUsize = AtomicUsize::new(0);

static HANDLE: Lazy<Mutex<Option<JoinHandle<()>>>> = Lazy::new(|| Mutex::new(None));
static APP_HANDLE: Lazy<Mutex<Option<AppHandle>>> = Lazy::new(|| Mutex::new(None));

const MAX_RESTARTS: usize = 3;

pub async fn install_app_handle(handle: AppHandle) {
    *APP_HANDLE.lock().await = Some(handle);
}

pub async fn list_cameras() -> Vec<String> {
    #[cfg(target_os = "macos")]
    {
        camera::list_devices().into_iter().map(|(_, name)| name).collect()
    }
    #[cfg(not(target_os = "macos"))]
    {
        Vec::new()
    }
}

pub async fn set_camera_index(i: usize) -> Result<(), String> {
    CAMERA_INDEX.store(i, Ordering::SeqCst);
    Ok(())
}

pub async fn engine_status() -> EngineStatus {
    let state = if PAUSED.load(Ordering::SeqCst) {
        "paused"
    } else if RUNNING.load(Ordering::SeqCst) {
        "running"
    } else {
        "stopped"
    };
    EngineStatus {
        state: state.into(),
        fps: 0.0,
        last_error: None,
    }
}

pub async fn pause_engine() -> Result<(), String> {
    PAUSED.store(true, Ordering::SeqCst);
    stop_engine().await
}

pub async fn resume_engine() -> Result<(), String> {
    PAUSED.store(false, Ordering::SeqCst);
    start_engine().await
}

pub async fn start_engine() -> Result<(), String> {
    if RUNNING.swap(true, Ordering::SeqCst) {
        return Ok(());
    }
    let app = APP_HANDLE
        .lock()
        .await
        .clone()
        .ok_or_else(|| "app handle not installed".to_string())?;

    let h = tokio::spawn(async move {
        let mut restarts = 0usize;
        while RUNNING.load(Ordering::SeqCst) && restarts <= MAX_RESTARTS {
            #[cfg(target_os = "macos")]
            let result = run_engine_loop(app.clone()).await;
            #[cfg(not(target_os = "macos"))]
            let result: Result<(), String> = Err("gesture engine: unsupported platform".into());

            if let Err(e) = result {
                let _ = app.emit(
                    "engine-status",
                    EngineStatus {
                        state: "error".into(),
                        fps: 0.0,
                        last_error: Some(e),
                    },
                );
                restarts += 1;
                tokio::time::sleep(Duration::from_millis(500)).await;
                continue;
            }
            break;
        }
        if restarts > MAX_RESTARTS {
            let _ = app.emit(
                "engine-status",
                EngineStatus {
                    state: "fatal".into(),
                    fps: 0.0,
                    last_error: Some("restart budget exhausted".into()),
                },
            );
            RUNNING.store(false, Ordering::SeqCst);
        }
    });

    *HANDLE.lock().await = Some(h);
    Ok(())
}

pub async fn stop_engine() -> Result<(), String> {
    RUNNING.store(false, Ordering::SeqCst);
    if let Some(h) = HANDLE.lock().await.take() {
        h.abort();
    }
    Ok(())
}

#[cfg(target_os = "macos")]
async fn run_engine_loop(app: AppHandle) -> Result<(), String> {
    let extractor = AppleVisionExtractor::new();
    let mut wake = WakeMachine::new();
    let mut recog = Recognizer::new();
    let mut last_state = WakeState::Sleeping;

    let mut stream = camera::start(CAMERA_INDEX.load(Ordering::SeqCst), 30)?;

    while RUNNING.load(Ordering::SeqCst) {
        let evt = match stream.rx.recv().await {
            Some(e) => e,
            None => break,
        };
        let now_ms = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_millis() as i64;
        match evt {
            CameraEvent::Frame(frame) => {
                // Fan out to the existing Spatial HUD consumer.
                let _ = app.emit(
                    "spatial-frame",
                    serde_json::json!({
                        "width": frame.width,
                        "height": frame.height,
                        "timestamp": frame.ts_ms as f64 / 1000.0,
                    }),
                );

                let hands = extractor.extract(&frame.rgb, frame.width, frame.height);
                let primary_pose = hands
                    .first()
                    .map(|h| crate::gesture::pose::classify(h).0);
                let primary_conf = hands.first().map(|h| h.confidence).unwrap_or(0.0);
                wake.tick(
                    WakeInput::Pose {
                        pose: primary_pose,
                        confidence: primary_conf,
                    },
                    now_ms,
                );
                wake.tick(WakeInput::Idle, now_ms);

                if last_state != wake.state() {
                    last_state = wake.state();
                    let _ = app.emit("wake-state-changed", &last_state);
                }

                if matches!(wake.state(), WakeState::Armed) {
                    let (event, _) = recog.ingest(hands, now_ms);
                    if let Some(ev) = event {
                        let _ = app.emit("gesture-event", &ev);
                    }
                }
            }
            CameraEvent::Disconnected => return Err("camera disconnected".into()),
            CameraEvent::Error(e) => return Err(e),
        }
    }
    Ok(())
}
