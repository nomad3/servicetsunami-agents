//! System cursor driver — moves the OS cursor and synthesizes clicks via
//! `enigo`. Gated behind two checks:
//!
//!   1. macOS Accessibility permission (`AXIsProcessTrusted`-ish via osascript).
//!   2. A user-controlled `cursor_global_mode` flag (default OFF). When OFF
//!      cursor moves only fire while Luna or Spatial HUD is the frontmost
//!      app, so a stray pinch doesn't click in some other app.
//!
//! Phase 3 wires this into the recognizer's `point` pose path. Display size
//! is hardcoded to 1920×1080 in v1; a follow-up will read `CGDisplayBounds`.

use std::sync::atomic::{AtomicBool, Ordering};

use once_cell::sync::Lazy;
use tokio::sync::Mutex;

#[cfg(target_os = "macos")]
use enigo::{Button, Coordinate, Direction, Enigo, Mouse, Settings};

static GLOBAL_MODE: AtomicBool = AtomicBool::new(false);
static ACCESSIBILITY_OK: AtomicBool = AtomicBool::new(false);

#[cfg(target_os = "macos")]
static ENIGO: Lazy<Mutex<Option<Enigo>>> = Lazy::new(|| Mutex::new(None));

pub fn set_global_mode(v: bool) {
    GLOBAL_MODE.store(v, Ordering::SeqCst);
}

pub fn global_mode() -> bool {
    GLOBAL_MODE.load(Ordering::SeqCst)
}

pub fn accessibility_ok() -> bool {
    ACCESSIBILITY_OK.load(Ordering::SeqCst)
}

#[cfg(target_os = "macos")]
pub fn check_accessibility() -> bool {
    use std::process::Command;
    // A cheap read-only Apple Events probe — if we have Accessibility, this
    // returns the frontmost process name. If not, osascript exits non-zero.
    let ok = Command::new("osascript")
        .args([
            "-e",
            "tell application \"System Events\" to get name of first application process whose frontmost is true",
        ])
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false);
    ACCESSIBILITY_OK.store(ok, Ordering::SeqCst);
    ok
}

#[cfg(not(target_os = "macos"))]
pub fn check_accessibility() -> bool {
    false
}

#[cfg(target_os = "macos")]
fn frontmost_is_luna() -> bool {
    use std::process::Command;
    let out = Command::new("osascript")
        .args([
            "-e",
            "tell application \"System Events\" to get name of first application process whose frontmost is true",
        ])
        .output();
    match out {
        Ok(o) if o.status.success() => {
            let s = String::from_utf8_lossy(&o.stdout).trim().to_string();
            s == "Luna" || s == "luna"
        }
        _ => false,
    }
}

#[cfg(not(target_os = "macos"))]
fn frontmost_is_luna() -> bool {
    false
}

/// Move the system cursor to absolute coordinates `(x, y)` in [0, 1] image
/// space. No-op if Accessibility is denied or if global_mode is OFF and
/// Luna isn't frontmost.
#[cfg(target_os = "macos")]
pub async fn move_abs(x: f32, y: f32) {
    if !ACCESSIBILITY_OK.load(Ordering::SeqCst) { return; }
    if !GLOBAL_MODE.load(Ordering::SeqCst) && !frontmost_is_luna() { return; }

    // Display size is hardcoded for v1; follow-up will read CGDisplayBounds.
    let px = (x.clamp(0.0, 1.0) * 1920.0) as i32;
    let py = (y.clamp(0.0, 1.0) * 1080.0) as i32;

    let mut guard = ENIGO.lock().await;
    if guard.is_none() {
        *guard = Enigo::new(&Settings::default()).ok();
    }
    if let Some(e) = guard.as_mut() {
        let _ = e.move_mouse(px, py, Coordinate::Abs);
    }
}

#[cfg(target_os = "macos")]
pub async fn click() {
    if !ACCESSIBILITY_OK.load(Ordering::SeqCst) { return; }
    if !GLOBAL_MODE.load(Ordering::SeqCst) && !frontmost_is_luna() { return; }

    let mut guard = ENIGO.lock().await;
    if guard.is_none() {
        *guard = Enigo::new(&Settings::default()).ok();
    }
    if let Some(e) = guard.as_mut() {
        let _ = e.button(Button::Left, Direction::Click);
    }
}

#[cfg(not(target_os = "macos"))]
pub async fn move_abs(_x: f32, _y: f32) {}

#[cfg(not(target_os = "macos"))]
pub async fn click() {}
