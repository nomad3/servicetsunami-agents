use tauri::Manager;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};

mod gesture;

lazy_static::lazy_static! {
    static ref CAPTURE_RUNNING: Arc<AtomicBool> = Arc::new(AtomicBool::new(false));
}

#[cfg(desktop)]
use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
};

#[tauri::command]
fn get_platform() -> String {
    std::env::consts::OS.to_string()
}

#[tauri::command]
fn get_arch() -> String {
    std::env::consts::ARCH.to_string()
}

/// Screenshot capture — desktop only (uses macOS screencapture binary).
/// On iOS returns an error; the frontend should use the native share sheet instead.
#[tauri::command]
async fn capture_screenshot() -> Result<String, String> {
    #[cfg(desktop)]
    {
        use std::process::Command;

        let timestamp = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_secs();
        let path = format!("/tmp/luna-screenshot-{}.png", timestamp);

        let output = Command::new("screencapture")
            .args(["-x", "-C", &path])
            .output()
            .map_err(|e| format!("Screenshot failed: {}", e))?;

        if !output.status.success() {
            return Err("Screenshot capture failed".to_string());
        }

        let bytes = std::fs::read(&path)
            .map_err(|e| format!("Failed to read screenshot: {}", e))?;
        let _ = std::fs::remove_file(&path);

        return Ok(base64_encode(&bytes));
    }

    #[cfg(mobile)]
    Err("Screenshot not available on mobile — use the system share sheet".to_string())
}

/// Haptic feedback trigger — mobile only, no-op on desktop.
#[tauri::command]
async fn haptic_feedback(style: String) -> Result<(), String> {
    log::info!("Haptic feedback: {}", style);
    // tauri-plugin-haptics exposes its own invoke commands (ImpactFeedback etc.)
    // This command lets the frontend check if it's on mobile before calling those.
    Ok(())
}

#[tauri::command]
async fn get_active_app() -> Result<serde_json::Value, String> {
    use std::process::Command;

    let app_output = Command::new("osascript")
        .args(["-e", "tell application \"System Events\" to get name of first application process whose frontmost is true"])
        .output()
        .map_err(|e| format!("Failed: {}", e))?;
    let app_name = String::from_utf8_lossy(&app_output.stdout).trim().to_string();

    let safe_name = app_name.replace('\\', "\\\\").replace('"', "\\\"");
    let title_output = Command::new("osascript")
        .args(["-e", &format!(
            "tell application \"System Events\" to get name of front window of application process \"{}\"",
            safe_name
        )])
        .output();

    let window_title = match title_output {
        Ok(o) if o.status.success() => String::from_utf8_lossy(&o.stdout).trim().to_string(),
        _ => String::new(),
    };

    Ok(serde_json::json!({
        "app": app_name,
        "title": window_title,
    }))
}

#[tauri::command]
async fn read_clipboard() -> Result<String, String> {
    use std::process::Command;
    let output = Command::new("pbpaste")
        .output()
        .map_err(|e| format!("Clipboard read failed: {}", e))?;
    Ok(String::from_utf8_lossy(&output.stdout).to_string())
}

#[tauri::command]
async fn toggle_spatial_hud(app: tauri::AppHandle) -> Result<(), String> {
    if let Some(window) = app.get_webview_window("spatial_hud") {
        if window.is_visible().unwrap_or(false) {
            let _ = window.hide();
            CAPTURE_RUNNING.store(false, Ordering::Relaxed);
        } else {
            let _ = window.show();
            let _ = window.set_focus();
        }
    }
    Ok(())
}

#[derive(Clone, serde::Serialize)]
struct SpatialFrame {
    width: u32,
    height: u32,
    timestamp: f64,
}

#[tauri::command]
async fn start_spatial_capture(_app: tauri::AppHandle) -> Result<(), String> {
    // Real `spatial-frame` events are now emitted by the gesture engine
    // (`gesture::supervisor::run_engine_loop`). This command is kept as a
    // no-op for FFI compatibility with the existing frontend HUD bootstrap.
    CAPTURE_RUNNING.store(true, Ordering::Relaxed);
    Ok(())
}

#[tauri::command]
async fn stop_spatial_capture() -> Result<(), String> {
    CAPTURE_RUNNING.store(false, Ordering::Relaxed);
    Ok(())
}

// ── Gesture engine commands ─────────────────────────────────────────────────

#[tauri::command]
async fn gesture_start() -> Result<(), String> {
    gesture::start_engine().await
}

#[tauri::command]
async fn gesture_stop() -> Result<(), String> {
    gesture::stop_engine().await
}

#[tauri::command]
async fn gesture_pause() -> Result<(), String> {
    gesture::pause_engine().await
}

#[tauri::command]
async fn gesture_resume() -> Result<(), String> {
    gesture::resume_engine().await
}

#[tauri::command]
async fn gesture_status() -> Result<gesture::EngineStatus, String> {
    Ok(gesture::engine_status().await)
}

#[tauri::command]
async fn gesture_list_cameras() -> Result<Vec<String>, String> {
    Ok(gesture::list_cameras().await)
}

#[tauri::command]
async fn gesture_set_camera_index(index: usize) -> Result<(), String> {
    gesture::set_camera_index(index).await
}

#[tauri::command]
async fn gesture_check_accessibility() -> Result<bool, String> {
    Ok(gesture::check_accessibility())
}

#[tauri::command]
async fn gesture_set_cursor_global(enabled: bool) -> Result<(), String> {
    gesture::set_global_mode(enabled);
    Ok(())
}

#[tauri::command]
async fn gesture_get_cursor_global() -> Result<bool, String> {
    Ok(gesture::global_mode())
}

#[derive(Clone, serde::Serialize, serde::Deserialize)]
struct ProjectionResult {
    id: String,
    x: f32,
    y: f32,
    z: f32,
}

#[tauri::command]
async fn project_embeddings(vectors: Vec<Vec<f32>>, ids: Vec<String>) -> Result<Vec<ProjectionResult>, String> {
    // Phase 1: deterministic scatter projection based on embedding values.
    // Full UMAP dimensionality reduction is a Phase 2 item — requires a
    // suitable Rust UMAP crate with a lib target (umap-rs has none).
    if vectors.is_empty() {
        return Ok(vec![]);
    }

    if vectors.len() != ids.len() {
        return Err("Vectors and IDs length mismatch".to_string());
    }

    let results = vectors.iter().zip(ids.iter()).map(|(v, id)| {
        // Use first three principal components as a cheap approximation.
        // Scale to [-100, 100] range for the Three.js scene.
        let x = v.get(0).copied().unwrap_or(0.0) * 100.0;
        let y = v.get(1).copied().unwrap_or(0.0) * 100.0;
        let z = v.get(2).copied().unwrap_or(0.0) * 100.0;
        ProjectionResult { id: id.clone(), x, y, z }
    }).collect();

    Ok(results)
}

/// Resolve the real tool/app from generic process names.
/// - Terminal/iTerm2: checks window title for running commands (claude, docker, npm, etc.)
/// - Electron: extracts real app name from window title
fn resolve_app_context(app_name: &str, window_title: &str) -> String {
    let lower_title = window_title.to_lowercase();

    // Terminal emulators: detect what's running inside
    if matches!(app_name, "Terminal" | "iTerm2" | "Alacritty" | "kitty" | "Warp" | "Hyper") {
        let tools = [
            ("claude", "Claude Code"),
            ("codex", "Codex CLI"),
            ("npm run", "npm"),
            ("pnpm", "pnpm"),
            ("cargo", "Cargo"),
            ("docker", "Docker CLI"),
            ("kubectl", "kubectl"),
            ("python", "Python"),
            ("node ", "Node.js"),
            ("vim", "Vim"),
            ("nvim", "Neovim"),
            ("ssh ", "SSH"),
            ("git ", "Git"),
            ("psql", "PostgreSQL CLI"),
        ];
        for (pattern, label) in tools {
            if lower_title.contains(pattern) {
                return format!("{} ({})", label, app_name);
            }
        }
        return app_name.to_string();
    }

    // Electron/Code editors: extract PROJECT name, not file name
    // Window titles look like: "project-name — filename.ext" or "project-name - filename"
    if matches!(app_name, "Electron" | "Code" | "Code - Insiders" | "Cursor") {
        // Extract the first segment before " — " or " - " (that's the project)
        let project = if let Some(pos) = window_title.find(" \u{2014} ") {
            // em dash (—) separator: "agentprovision-agents — file.md"
            window_title[..pos].trim()
        } else if let Some(pos) = window_title.find(" - ") {
            window_title[..pos].trim()
        } else {
            window_title.trim()
        };
        if !project.is_empty() {
            return project.to_string();
        }
    }

    // Chrome/Safari: extract just the domain or short title
    if matches!(app_name, "Google Chrome" | "Safari" | "Firefox" | "Arc") {
        if !window_title.is_empty() {
            // Truncate to just the meaningful part
            let short = if let Some(pos) = window_title.find(" - ") {
                &window_title[..pos]
            } else {
                truncate_str(&window_title, 40)
            };
            return format!("{} ({})", app_name, short.trim());
        }
    }

    app_name.to_string()
}

/// Get deeper subprocess context: what project/repo is the user working on,
/// what commands are running in their terminal sessions.
fn get_subprocess_context() -> serde_json::Value {
    use std::process::Command;

    // Get foreground terminal processes (children of Terminal/iTerm)
    // `ps` shows all processes with their command, we filter for interesting ones
    let ps_output = Command::new("sh")
        .args(["-c", "ps -eo pid,ppid,comm,args 2>/dev/null | grep -E 'claude|docker|cargo|npm|node|python|git|kubectl|uvicorn|vite' | grep -v grep | head -10"])
        .output();

    let mut processes = Vec::new();
    if let Ok(output) = ps_output {
        let text = String::from_utf8_lossy(&output.stdout);
        for line in text.lines() {
            let parts: Vec<&str> = line.split_whitespace().collect();
            if parts.len() >= 4 {
                let comm = parts[2];
                let args = parts[3..].join(" ");
                // Extract project context from args (look for paths)
                let project = extract_project_from_args(&args);
                processes.push(serde_json::json!({
                    "command": comm,
                    "args": truncate_str(&args, 120),
                    "project": project,
                }));
            }
        }
    }

    // Get the current git repo if we're in one (from the most recent terminal cwd)
    let git_output = Command::new("sh")
        .args(["-c", "lsof -c Terminal -c iTerm2 -a -d cwd 2>/dev/null | tail -1 | awk '{print $NF}'"])
        .output();

    let cwd = match git_output {
        Ok(o) => String::from_utf8_lossy(&o.stdout).trim().to_string(),
        _ => String::new(),
    };

    serde_json::json!({
        "active_processes": processes,
        "terminal_cwd": cwd,
    })
}

/// Extract project name from command args (looks for repo paths)
fn extract_project_from_args(args: &str) -> String {
    // Look for common project path patterns
    for part in args.split_whitespace() {
        if part.contains("/GitHub/") || part.contains("/Projects/") || part.contains("/src/") {
            // Extract the repo/project name from the path
            let segments: Vec<&str> = part.split('/').collect();
            for (i, seg) in segments.iter().enumerate() {
                if (*seg == "GitHub" || *seg == "Projects") && i + 1 < segments.len() {
                    return segments[i + 1].to_string();
                }
            }
        }
    }
    String::new()
}

fn truncate_str(s: &str, max: usize) -> &str {
    if s.len() <= max { s } else { &s[..max] }
}

fn base64_encode(data: &[u8]) -> String {
    const CHARS: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut result = String::with_capacity(data.len() * 4 / 3 + 4);
    for chunk in data.chunks(3) {
        let b0 = chunk[0] as u32;
        let b1 = chunk.get(1).copied().unwrap_or(0) as u32;
        let b2 = chunk.get(2).copied().unwrap_or(0) as u32;
        let n = (b0 << 16) | (b1 << 8) | b2;
        result.push(CHARS[((n >> 18) & 63) as usize] as char);
        result.push(CHARS[((n >> 12) & 63) as usize] as char);
        if chunk.len() > 1 {
            result.push(CHARS[((n >> 6) & 63) as usize] as char);
        } else {
            result.push('=');
        }
        if chunk.len() > 2 {
            result.push(CHARS[(n & 63) as usize] as char);
        } else {
            result.push('=');
        }
    }
    result
}

#[cfg(desktop)]
fn setup_tray(app: &tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    let open_item = MenuItem::with_id(app, "open", "Open Luna", true, None::<&str>)?;
    let quit_item = MenuItem::with_id(app, "quit", "Quit Luna", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&open_item, &quit_item])?;

    let _tray = TrayIconBuilder::new()
        .icon(app.default_window_icon().unwrap().clone())
        .tooltip("Luna — AI Assistant")
        .menu(&menu)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "open" => {
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.show();
                    let _ = window.set_focus();
                }
            }
            "quit" => {
                app.exit(0);
            }
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let tauri::tray::TrayIconEvent::Click { .. } = event {
                let app = tray.app_handle();
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.show();
                    let _ = window.set_focus();
                }
            }
        })
        .build(app)?;

    Ok(())
}

#[cfg(desktop)]
fn setup_global_shortcut(app: &tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut};

    let palette_shortcut = Shortcut::new(Some(Modifiers::SUPER | Modifiers::SHIFT), Code::Space);
    let hud_shortcut = Shortcut::new(Some(Modifiers::SUPER | Modifiers::SHIFT), Code::KeyL);
    let gesture_killswitch = Shortcut::new(Some(Modifiers::SUPER | Modifiers::SHIFT), Code::KeyG);

    app.global_shortcut().on_shortcut(palette_shortcut, move |app, _shortcut, event| {
        if event.state == tauri_plugin_global_shortcut::ShortcutState::Pressed {
            // Emit to frontend — React handles showing the command palette
            let _ = tauri::Emitter::emit(app, "toggle-palette", ());
            // Also ensure window is visible
            if let Some(window) = app.get_webview_window("main") {
                if !window.is_visible().unwrap_or(true) {
                    let _ = window.show();
                    let _ = window.set_focus();
                }
            }
        }
    })?;

    app.global_shortcut().on_shortcut(hud_shortcut, move |app, _shortcut, event| {
        if event.state == tauri_plugin_global_shortcut::ShortcutState::Pressed {
            if let Some(window) = app.get_webview_window("spatial_hud") {
                if window.is_visible().unwrap_or(false) {
                    let _ = window.hide();
                    CAPTURE_RUNNING.store(false, Ordering::Relaxed);
                } else {
                    let _ = window.show();
                    let _ = window.set_focus();
                }
            }
        }
    })?;

    // Cmd+Shift+G — gesture engine kill-switch (toggle pause/resume).
    app.global_shortcut().on_shortcut(gesture_killswitch, move |_app, _shortcut, event| {
        if event.state == tauri_plugin_global_shortcut::ShortcutState::Pressed {
            tauri::async_runtime::spawn(async move {
                let status = crate::gesture::engine_status().await;
                if status.state == "paused" {
                    let _ = crate::gesture::resume_engine().await;
                } else {
                    let _ = crate::gesture::pause_engine().await;
                }
            });
        }
    })?;

    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let mut builder = tauri::Builder::default()
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_opener::init());

    // Desktop-only plugins
    #[cfg(desktop)]
    {
        builder = builder
            .plugin(tauri_plugin_global_shortcut::Builder::new().build())
            .plugin(tauri_plugin_updater::Builder::new().build());
    }

    // Mobile-only plugins
    #[cfg(mobile)]
    {
        builder = builder.plugin(tauri_plugin_haptics::init());
    }

    builder
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }

            #[cfg(desktop)]
            {
                setup_tray(app)?;
                setup_global_shortcut(app)?;

                // Install the AppHandle synchronously so a fast auto-login
                // can call `gesture_start` immediately without racing the
                // setup spawn. install_app_handle is now a sync function
                // backed by a std::sync::Mutex.
                crate::gesture::install_app_handle(app.handle().clone());
                // Engine itself is NOT started here — the frontend calls
                // `gesture_start` after a successful login so we don't burn
                // camera + Apple Vision cycles on the login screen.

                // Auto-updater: check on startup + every 30 min
                let handle = app.handle().clone();
                std::thread::spawn(move || {
                    loop {
                        let h = handle.clone();
                        tauri::async_runtime::block_on(async move {
                            let updater = match tauri_plugin_updater::UpdaterExt::updater(&h) {
                                Ok(u) => u,
                                Err(e) => { log::debug!("Updater init failed: {}", e); return; }
                            };
                            match updater.check().await {
                                Ok(Some(update)) => {
                                    log::info!("Update available: {}", update.version);
                                    let _ = tauri::Emitter::emit(&h, "update-available", update.version.clone());
                                }
                                Ok(None) => log::info!("No update available"),
                                Err(e) => log::debug!("Update check failed: {}", e),
                            }
                        });
                        std::thread::sleep(std::time::Duration::from_secs(1800));
                    }
                });
            }

            // Clipboard watcher — emits 'clipboard-changed' when clipboard text changes
            // Uses AtomicBool so the thread can be signalled to stop on app exit.
            let clip_running = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(true));
            let clip_flag = clip_running.clone();
            let clip_handle = app.handle().clone();
            std::thread::spawn(move || {
                let mut last_content = String::new();
                while clip_flag.load(std::sync::atomic::Ordering::Relaxed) {
                    std::thread::sleep(std::time::Duration::from_secs(2));
                    if let Ok(output) = std::process::Command::new("pbpaste").output() {
                        let current = String::from_utf8_lossy(&output.stdout).to_string();
                        if current != last_content && !current.is_empty() {
                            last_content = current.clone();
                            let _ = tauri::Emitter::emit(&clip_handle, "clipboard-changed", &current);
                        }
                    }
                }
            });
            // Activity tracker — monitors app switches + window context for pattern detection
            // Captures: app name, window title, and for terminals/Electron apps, the
            // actual tool/project running inside (e.g., "claude" in Terminal, real app
            // name from Electron window titles).
            let activity_handle = app.handle().clone();
            let activity_running = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(true));
            let activity_flag = activity_running.clone();
            std::thread::spawn(move || {
                let mut last_context = String::new(); // "app:title" composite key
                let mut last_switch = std::time::Instant::now();
                while activity_flag.load(std::sync::atomic::Ordering::Relaxed) {
                    std::thread::sleep(std::time::Duration::from_secs(5));

                    // Get frontmost app
                    let app_name = match std::process::Command::new("osascript")
                        .args(["-e", "tell application \"System Events\" to get name of first application process whose frontmost is true"])
                        .output()
                    {
                        Ok(o) => String::from_utf8_lossy(&o.stdout).trim().to_string(),
                        Err(_) => continue,
                    };
                    if app_name.is_empty() { continue; }

                    // Get window title
                    let safe_name = app_name.replace('\\', "\\\\").replace('"', "\\\"");
                    let window_title = match std::process::Command::new("osascript")
                        .args(["-e", &format!(
                            "tell application \"System Events\" to get name of front window of application process \"{}\"",
                            safe_name
                        )])
                        .output()
                    {
                        Ok(o) if o.status.success() => String::from_utf8_lossy(&o.stdout).trim().to_string(),
                        _ => String::new(),
                    };

                    // Resolve the real tool for terminals and Electron apps
                    let resolved_app = resolve_app_context(&app_name, &window_title);

                    // Only emit on context change (app + title)
                    let context_key = format!("{}:{}", resolved_app, window_title);
                    if context_key != last_context {
                        let duration_secs = last_switch.elapsed().as_secs();
                        let timestamp = std::time::SystemTime::now()
                            .duration_since(std::time::UNIX_EPOCH)
                            .unwrap()
                            .as_secs();
                        // Get subprocess context for deeper insight
                        let subprocess = get_subprocess_context();

                        // Emit the main app switch event
                        let event = serde_json::json!({
                            "type": "app_switch",
                            "from_app": last_context.split(':').next().unwrap_or(""),
                            "to_app": resolved_app,
                            "window_title": window_title,
                            "subprocess": subprocess,
                            "duration_secs": duration_secs,
                            "timestamp": timestamp,
                        });
                        let _ = tauri::Emitter::emit(&activity_handle, "activity-event", &event);

                        // For editors: also emit events for detected CLI tools
                        // so the pattern detector sees "Claude Code", "Docker CLI", etc.
                        if let Some(procs) = subprocess.get("active_processes").and_then(|v| v.as_array()) {
                            for proc in procs {
                                let cmd = proc.get("command").and_then(|v| v.as_str()).unwrap_or("");
                                let project = proc.get("project").and_then(|v| v.as_str()).unwrap_or("");
                                let tool_name = match cmd {
                                    c if c.contains("claude") => "Claude Code",
                                    c if c.contains("codex") => "Codex CLI",
                                    c if c.contains("docker") => "Docker",
                                    c if c.contains("cargo") => "Cargo",
                                    c if c.contains("npm") || c.contains("node") => "Node.js",
                                    c if c.contains("python") || c.contains("uvicorn") => "Python",
                                    c if c.contains("kubectl") => "kubectl",
                                    c if c.contains("vite") => "Vite",
                                    _ => continue,
                                };
                                let tool_label = if project.is_empty() {
                                    tool_name.to_string()
                                } else {
                                    format!("{} ({})", tool_name, project)
                                };
                                let tool_event = serde_json::json!({
                                    "type": "app_switch",
                                    "from_app": &resolved_app,
                                    "to_app": tool_label,
                                    "window_title": proc.get("args").and_then(|v| v.as_str()).unwrap_or(""),
                                    "duration_secs": 0,
                                    "timestamp": timestamp,
                                });
                                let _ = tauri::Emitter::emit(&activity_handle, "activity-event", &tool_event);
                            }
                        }

                        last_context = context_key;
                        last_switch = std::time::Instant::now();
                    }
                }
            });

            // Stop clipboard watcher + activity tracker on app exit
            if let Some(window) = app.get_webview_window("main") {
                window.on_window_event(move |event| {
                    if let tauri::WindowEvent::Destroyed = event {
                        clip_running.store(false, std::sync::atomic::Ordering::Relaxed);
                        activity_running.store(false, std::sync::atomic::Ordering::Relaxed);
                    }
                });
            }

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            get_platform,
            get_arch,
            capture_screenshot,
            get_active_app,
            read_clipboard,
            haptic_feedback,
            toggle_spatial_hud,
            start_spatial_capture,
            stop_spatial_capture,
            project_embeddings,
            gesture_start,
            gesture_stop,
            gesture_pause,
            gesture_resume,
            gesture_status,
            gesture_list_cameras,
            gesture_set_camera_index,
            gesture_check_accessibility,
            gesture_set_cursor_global,
            gesture_get_cursor_global,
        ])
        .run(tauri::generate_context!())
        .expect("error while running Luna");
}

#[cfg(test)]
mod tests {
    //! Pure-logic unit tests for src-tauri/src/lib.rs.
    //!
    //! Only covers helpers that don't require a running tauri runtime: the
    //! `#[tauri::command]` async handlers that touch the AppHandle, system
    //! webcam, or external `osascript`/`pbpaste` processes are out of scope
    //! for default `cargo test` runs.
    use super::*;
    use pretty_assertions::assert_eq;

    // ── platform / arch ─────────────────────────────────────────────────
    #[test]
    fn get_platform_returns_non_empty() {
        let p = get_platform();
        assert!(!p.is_empty(), "platform string should not be empty");
        // OS family must be one of the standard Rust constants — guards
        // against accidental hard-coding.
        assert!(
            matches!(p.as_str(), "macos" | "linux" | "windows" | "ios" | "android" | "freebsd" | "netbsd" | "openbsd" | "dragonfly" | "solaris"),
            "unexpected platform: {p}",
        );
    }

    #[test]
    fn get_arch_returns_non_empty() {
        let a = get_arch();
        assert!(!a.is_empty(), "arch string should not be empty");
    }

    // ── truncate_str ────────────────────────────────────────────────────
    #[test]
    fn truncate_str_passes_short_through() {
        assert_eq!(truncate_str("hello", 10), "hello");
    }

    #[test]
    fn truncate_str_clips_long_to_max() {
        assert_eq!(truncate_str("abcdefghij", 4), "abcd");
    }

    #[test]
    fn truncate_str_handles_exact_length() {
        assert_eq!(truncate_str("abc", 3), "abc");
    }

    // ── base64_encode ───────────────────────────────────────────────────
    #[test]
    fn base64_encode_empty_input() {
        assert_eq!(base64_encode(&[]), "");
    }

    #[test]
    fn base64_encode_known_vectors() {
        // RFC 4648 test vectors.
        assert_eq!(base64_encode(b"f"), "Zg==");
        assert_eq!(base64_encode(b"fo"), "Zm8=");
        assert_eq!(base64_encode(b"foo"), "Zm9v");
        assert_eq!(base64_encode(b"foob"), "Zm9vYg==");
        assert_eq!(base64_encode(b"fooba"), "Zm9vYmE=");
        assert_eq!(base64_encode(b"foobar"), "Zm9vYmFy");
    }

    #[test]
    fn base64_encode_binary_bytes() {
        // 0x00 0xFF 0x10
        //  = 00000000 11111111 00010000
        //  = 000000 001111 111100 010000
        //  = 0 15 60 16
        //  = A  P  8  Q
        let bytes: [u8; 3] = [0x00, 0xFF, 0x10];
        let encoded = base64_encode(&bytes);
        assert_eq!(encoded, "AP8Q");
    }

    // ── extract_project_from_args ───────────────────────────────────────
    #[test]
    fn extract_project_from_github_path() {
        let got = extract_project_from_args("claude /Users/me/Documents/GitHub/agentprovision-agents/apps");
        assert_eq!(got, "agentprovision-agents");
    }

    #[test]
    fn extract_project_from_projects_path() {
        let got = extract_project_from_args("npm run dev /Users/me/Projects/luna-os");
        assert_eq!(got, "luna-os");
    }

    #[test]
    fn extract_project_returns_empty_for_no_match() {
        assert_eq!(extract_project_from_args("npm install"), "");
        assert_eq!(extract_project_from_args(""), "");
    }

    // ── resolve_app_context ─────────────────────────────────────────────
    #[test]
    fn resolve_terminal_with_known_tool() {
        let got = resolve_app_context("Terminal", "claude --some-flag");
        assert_eq!(got, "Claude Code (Terminal)");
    }

    #[test]
    fn resolve_terminal_with_docker() {
        let got = resolve_app_context("iTerm2", "docker ps -a");
        assert_eq!(got, "Docker CLI (iTerm2)");
    }

    #[test]
    fn resolve_terminal_with_unknown_tool_returns_app_name() {
        let got = resolve_app_context("Terminal", "ls -la");
        assert_eq!(got, "Terminal");
    }

    #[test]
    fn resolve_electron_extracts_project_with_em_dash() {
        // U+2014 em-dash is the canonical separator in VS Code titles.
        let got = resolve_app_context("Code", "agentprovision-agents \u{2014} CLAUDE.md");
        assert_eq!(got, "agentprovision-agents");
    }

    #[test]
    fn resolve_electron_extracts_project_with_hyphen() {
        let got = resolve_app_context("Cursor", "luna-os - lib.rs");
        assert_eq!(got, "luna-os");
    }

    #[test]
    fn resolve_chrome_includes_window_short_title() {
        let got = resolve_app_context("Google Chrome", "Test page - Chromium docs");
        assert!(got.starts_with("Google Chrome"));
        assert!(got.contains("Test page"));
    }

    #[test]
    fn resolve_unknown_app_returns_app_name() {
        let got = resolve_app_context("Slack", "general | acme");
        assert_eq!(got, "Slack");
    }

    // ── ProjectionResult / project_embeddings logic ─────────────────────
    //
    // `project_embeddings` is `async fn` but does no IO — we drive it with
    // `futures::executor::block_on` would require an extra dep, so use the
    // tokio-free trick of polling once via `pollster`-style hack: call the
    // future on a stub executor. The simpler path is to extract the logic
    // by hand, but that requires touching prod code — instead we reuse the
    // tauri-async runtime which is already pulled in by tauri itself.
    //
    // Tauri exposes `tauri::async_runtime::block_on` for this exact purpose.
    #[test]
    fn project_embeddings_empty_input_returns_empty_vec() {
        let result = tauri::async_runtime::block_on(project_embeddings(vec![], vec![]))
            .expect("empty input should not error");
        assert!(result.is_empty());
    }

    #[test]
    fn project_embeddings_length_mismatch_errors() {
        let result = tauri::async_runtime::block_on(project_embeddings(
            vec![vec![0.1, 0.2, 0.3]],
            vec!["a".into(), "b".into()],
        ));
        match result {
            Err(msg) => assert!(msg.contains("length mismatch"), "got: {msg}"),
            Ok(_) => panic!("expected length-mismatch error"),
        }
    }

    #[test]
    fn project_embeddings_scales_first_three_dims() {
        let result = tauri::async_runtime::block_on(project_embeddings(
            vec![vec![0.1, 0.2, 0.3, 0.4]],
            vec!["entity-1".into()],
        ))
        .expect("ok");
        assert_eq!(result.len(), 1);
        assert_eq!(result[0].id, "entity-1");
        // Scale factor is *100 in the implementation.
        assert!((result[0].x - 10.0).abs() < 1e-4);
        assert!((result[0].y - 20.0).abs() < 1e-4);
        assert!((result[0].z - 30.0).abs() < 1e-4);
    }

    #[test]
    fn project_embeddings_handles_short_vectors() {
        // Vectors with fewer than 3 dims should pad with 0 not panic.
        let result = tauri::async_runtime::block_on(project_embeddings(
            vec![vec![0.5]],
            vec!["x".into()],
        ))
        .expect("ok");
        assert_eq!(result[0].x, 50.0);
        assert_eq!(result[0].y, 0.0);
        assert_eq!(result[0].z, 0.0);
    }
}
