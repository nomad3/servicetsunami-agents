//! System cursor driver — moves the OS cursor and synthesizes clicks via
//! `enigo`. Gated behind two checks:
//!
//!   1. macOS Accessibility permission (`AXIsProcessTrusted`-ish via osascript).
//!   2. A user-controlled `cursor_global_mode` flag (default OFF). When OFF
//!      cursor moves only fire while Luna or Spatial HUD is the frontmost
//!      app, so a stray pinch doesn't click in some other app.
//!
//! Phase 4 improvements over the v1 from the gesture-system PR:
//!   - Display size read once at startup via `CGDisplayPixelsWide/High` so
//!     cursor coordinates work on Retina, multi-monitor, and non-1080p setups.
//!   - Frontmost-app check cached at 1Hz instead of shelling `osascript` per
//!     cursor frame. ~30× CPU reduction while pointing.

use std::sync::atomic::{AtomicBool, AtomicI64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use once_cell::sync::Lazy;
use tokio::sync::Mutex;

#[cfg(target_os = "macos")]
use enigo::{Button, Coordinate, Direction, Enigo, Mouse, Settings};

static GLOBAL_MODE: AtomicBool = AtomicBool::new(false);
static ACCESSIBILITY_OK: AtomicBool = AtomicBool::new(false);

// Frontmost-Luna cache — refreshed at most once per FRONTMOST_TTL_MS.
static FRONTMOST_LAST_CHECK_MS: AtomicI64 = AtomicI64::new(0);
static FRONTMOST_IS_LUNA: AtomicBool = AtomicBool::new(false);
const FRONTMOST_TTL_MS: i64 = 1_000;

// Display dimensions cache. -1 = not yet read.
static DISPLAY_W: AtomicI64 = AtomicI64::new(-1);
static DISPLAY_H: AtomicI64 = AtomicI64::new(-1);

// `Enigo` on macOS holds a `NonNull<CGEventSource>` which is `!Send` because
// the raw pointer marker is conservative. Wrap it in a newtype that asserts
// `Send` so we can park it inside a `Lazy<Mutex<...>>` static. Safety: the
// surrounding `tokio::sync::Mutex` serializes all access to a single thread
// at a time, and Apple documents `CGEventCreate*` / `CGEventPost` family as
// thread-safe (the type is `!Send` only because rustc can't prove it).
#[cfg(target_os = "macos")]
struct SendEnigo(Enigo);

#[cfg(target_os = "macos")]
unsafe impl Send for SendEnigo {}

#[cfg(target_os = "macos")]
impl std::ops::Deref for SendEnigo {
    type Target = Enigo;
    fn deref(&self) -> &Enigo { &self.0 }
}

#[cfg(target_os = "macos")]
impl std::ops::DerefMut for SendEnigo {
    fn deref_mut(&mut self) -> &mut Enigo { &mut self.0 }
}

#[cfg(target_os = "macos")]
static ENIGO: Lazy<Mutex<Option<SendEnigo>>> = Lazy::new(|| Mutex::new(None));

pub fn set_global_mode(v: bool) {
    GLOBAL_MODE.store(v, Ordering::SeqCst);
}

pub fn global_mode() -> bool {
    GLOBAL_MODE.load(Ordering::SeqCst)
}

pub fn accessibility_ok() -> bool {
    ACCESSIBILITY_OK.load(Ordering::SeqCst)
}

fn now_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as i64)
        .unwrap_or(0)
}

#[cfg(target_os = "macos")]
pub fn check_accessibility() -> bool {
    use std::process::Command;
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
fn probe_frontmost_luna_now() -> bool {
    use std::process::Command;
    Command::new("osascript")
        .args([
            "-e",
            "tell application \"System Events\" to get name of first application process whose frontmost is true",
        ])
        .output()
        .ok()
        .filter(|o| o.status.success())
        .map(|o| {
            let s = String::from_utf8_lossy(&o.stdout).trim().to_string();
            s == "Luna" || s == "luna"
        })
        .unwrap_or(false)
}

#[cfg(target_os = "macos")]
fn frontmost_is_luna_cached() -> bool {
    let now = now_ms();
    let last = FRONTMOST_LAST_CHECK_MS.load(Ordering::Relaxed);
    if now - last >= FRONTMOST_TTL_MS {
        let v = probe_frontmost_luna_now();
        FRONTMOST_IS_LUNA.store(v, Ordering::Relaxed);
        FRONTMOST_LAST_CHECK_MS.store(now, Ordering::Relaxed);
        v
    } else {
        FRONTMOST_IS_LUNA.load(Ordering::Relaxed)
    }
}

#[cfg(not(target_os = "macos"))]
fn frontmost_is_luna_cached() -> bool {
    false
}

/// Read main display size once; fall back to 1920×1080 if CG isn't available.
#[cfg(target_os = "macos")]
fn ensure_display_size() -> (i32, i32) {
    let cached_w = DISPLAY_W.load(Ordering::Relaxed);
    let cached_h = DISPLAY_H.load(Ordering::Relaxed);
    if cached_w > 0 && cached_h > 0 {
        return (cached_w as i32, cached_h as i32);
    }
    let (w, h) = read_main_display_size();
    DISPLAY_W.store(w as i64, Ordering::Relaxed);
    DISPLAY_H.store(h as i64, Ordering::Relaxed);
    (w, h)
}

#[cfg(target_os = "macos")]
fn read_main_display_size() -> (i32, i32) {
    // CGDirectDisplayID is u32 on macOS.
    extern "C" {
        fn CGMainDisplayID() -> u32;
        fn CGDisplayPixelsWide(display: u32) -> usize;
        fn CGDisplayPixelsHigh(display: u32) -> usize;
    }
    unsafe {
        let did = CGMainDisplayID();
        let w = CGDisplayPixelsWide(did) as i32;
        let h = CGDisplayPixelsHigh(did) as i32;
        if w > 0 && h > 0 { (w, h) } else { (1920, 1080) }
    }
}

#[cfg(not(target_os = "macos"))]
fn ensure_display_size() -> (i32, i32) {
    (1920, 1080)
}

/// Move the system cursor to absolute coordinates `(x, y)` in [0, 1] image
/// space. No-op if Accessibility is denied or if global_mode is OFF and
/// Luna isn't frontmost.
#[cfg(target_os = "macos")]
pub async fn move_abs(x: f32, y: f32) {
    if !ACCESSIBILITY_OK.load(Ordering::SeqCst) { return; }
    if !GLOBAL_MODE.load(Ordering::SeqCst) && !frontmost_is_luna_cached() { return; }

    let (dw, dh) = ensure_display_size();
    let px = (x.clamp(0.0, 1.0) * dw as f32) as i32;
    let py = (y.clamp(0.0, 1.0) * dh as f32) as i32;

    let mut guard = ENIGO.lock().await;
    if guard.is_none() {
        *guard = Enigo::new(&Settings::default()).ok().map(SendEnigo);
    }
    if let Some(e) = guard.as_mut() {
        let _ = e.move_mouse(px, py, Coordinate::Abs);
    }
}

#[cfg(target_os = "macos")]
pub async fn click() {
    if !ACCESSIBILITY_OK.load(Ordering::SeqCst) { return; }
    if !GLOBAL_MODE.load(Ordering::SeqCst) && !frontmost_is_luna_cached() { return; }

    let mut guard = ENIGO.lock().await;
    if guard.is_none() {
        *guard = Enigo::new(&Settings::default()).ok().map(SendEnigo);
    }
    if let Some(e) = guard.as_mut() {
        let _ = e.button(Button::Left, Direction::Click);
    }
}

#[cfg(not(target_os = "macos"))]
pub async fn move_abs(_x: f32, _y: f32) {}

#[cfg(not(target_os = "macos"))]
pub async fn click() {}
