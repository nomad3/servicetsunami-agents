//! Engine supervisor — owns the engine's lifecycle, restart budget, and
//! Tauri event emission. Spawns one Tokio task that polls the camera for
//! frames, runs the landmark extractor, drives the wake state machine, and
//! emits gesture-event / wake-state-changed / engine-status events.

use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::Mutex as StdMutex;

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
// AppHandle storage uses a *sync* std::sync::Mutex so `install_app_handle`
// can be called from the Tauri setup closure without spawning, eliminating
// the race where `gesture_start` (called from React on auto-login) would
// see an empty handle.
static APP_HANDLE: Lazy<StdMutex<Option<AppHandle>>> = Lazy::new(|| StdMutex::new(None));

const MAX_RESTARTS: usize = 3;

pub fn install_app_handle(handle: AppHandle) {
    if let Ok(mut guard) = APP_HANDLE.lock() {
        *guard = Some(handle);
    }
}

fn app_handle() -> Option<AppHandle> {
    APP_HANDLE.lock().ok().and_then(|g| g.clone())
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
        log::info!("gesture: start_engine called but already running — no-op");
        return Ok(());
    }
    log::info!("gesture: start_engine called, spawning supervisor");
    let app = app_handle().ok_or_else(|| {
        log::error!("gesture: app handle not installed at start_engine");
        "app handle not installed".to_string()
    })?;

    // Probe Accessibility once at startup so cursor/click bindings work
    // immediately when the user has already granted the permission. Skip
    // probing in tests (which we detect via cfg).
    #[cfg(target_os = "macos")]
    {
        let ax_ok = crate::gesture::cursor::check_accessibility();
        log::info!("gesture: accessibility check at startup = {}", ax_ok);
    }

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

    let camera_index = CAMERA_INDEX.load(Ordering::SeqCst);
    log::info!("gesture: opening camera index={} target_fps=30", camera_index);
    let mut stream = camera::start(camera_index, 30).map_err(|e| {
        log::error!("gesture: camera::start failed: {}", e);
        e
    })?;
    log::info!("gesture: camera stream opened, entering frame loop");

    // Throttled hand-count log: emit one Info line per second summarizing
    // detection rate so we can tell at a glance whether Vision is finding
    // hands at all without spamming the log per-frame.
    let mut frame_count: u64 = 0;
    let mut hands_seen_total: u64 = 0;
    let mut last_hand_log_ms: i64 = 0;
    const HAND_LOG_INTERVAL_MS: i64 = 1000;

    while RUNNING.load(Ordering::SeqCst) {
        let evt = match stream.rx.recv().await {
            Some(e) => e,
            None => {
                log::warn!("gesture: camera channel closed (stream.rx.recv -> None)");
                break;
            }
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

                frame_count += 1;
                let hands = extractor.extract(&frame.rgb, frame.width, frame.height);
                hands_seen_total += hands.len() as u64;
                let primary_pose = hands
                    .first()
                    .map(|h| crate::gesture::pose::classify(h).0);
                let primary_conf = hands.first().map(|h| h.confidence).unwrap_or(0.0);

                // Heartbeat once per second so we can tell from the log
                // whether the engine is alive AND whether Vision is
                // detecting any hands. Now also includes the classified
                // pose + wake state so we can diagnose why wake isn't
                // promoting (e.g. classifier returns Custom even when
                // hand looks like an open palm).
                if now_ms - last_hand_log_ms >= HAND_LOG_INTERVAL_MS {
                    log::info!(
                        "gesture: heartbeat frames={} hands_in_last_window={} hands_now={} confidence={:.3} frame_size={}x{} pose={:?} wake_state={:?}",
                        frame_count,
                        hands_seen_total,
                        hands.len(),
                        primary_conf,
                        frame.width,
                        frame.height,
                        primary_pose,
                        wake.state(),
                    );
                    last_hand_log_ms = now_ms;
                    hands_seen_total = 0;
                }
                // Emit landmarks for the React-side hand portrait
                // visualizer. Payload is small (~600 bytes per frame for
                // 1 hand of 21 landmarks × 3 floats), and we only emit
                // when at least one hand is detected so we don't spam
                // an idle stream of empties. The React side overlays
                // these on its own getUserMedia <video> at GPU speed —
                // no JPEG round-trip over Tauri IPC.
                if !hands.is_empty() {
                    let payload: Vec<serde_json::Value> = hands
                        .iter()
                        .map(|h| {
                            serde_json::json!({
                                "landmarks": h.landmarks
                                    .iter()
                                    .map(|p| serde_json::json!([p.x, p.y, p.z]))
                                    .collect::<Vec<_>>(),
                                "handedness": format!("{:?}", h.handedness),
                                "confidence": h.confidence,
                                "pose": primary_pose.map(|p| format!("{:?}", p)),
                            })
                        })
                        .collect();
                    let _ = app.emit(
                        "hand-landmarks",
                        serde_json::json!({
                            "ts_ms": now_ms,
                            "frame_w": frame.width,
                            "frame_h": frame.height,
                            "hands": payload,
                            "wake_state": format!("{:?}", wake.state()),
                        }),
                    );
                }

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
                    log::info!("gesture: wake-state -> {:?}", &last_state);
                    let _ = app.emit("wake-state-changed", &last_state);
                }

                if matches!(wake.state(), WakeState::Armed) {
                    let (event, _) = recog.ingest(hands, now_ms);
                    if let Some(ev) = event {
                        log::info!("gesture: emit gesture-event {:?}", &ev);
                        let _ = app.emit("gesture-event", &ev);
                    }
                }
            }
            CameraEvent::Disconnected => {
                log::error!("gesture: camera disconnected");
                return Err("camera disconnected".into());
            }
            CameraEvent::Error(e) => {
                log::error!("gesture: camera error: {}", e);
                return Err(e);
            }
        }
    }
    Ok(())
}
