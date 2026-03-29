use tauri::Manager;

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

    let shortcut = Shortcut::new(Some(Modifiers::SUPER | Modifiers::SHIFT), Code::Space);

    app.global_shortcut().on_shortcut(shortcut, move |app, _shortcut, event| {
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
            // Activity tracker — monitors app switches for workflow pattern detection
            let activity_handle = app.handle().clone();
            let activity_running = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(true));
            let activity_flag = activity_running.clone();
            std::thread::spawn(move || {
                let mut last_app = String::new();
                let mut last_switch = std::time::Instant::now();
                while activity_flag.load(std::sync::atomic::Ordering::Relaxed) {
                    std::thread::sleep(std::time::Duration::from_secs(5));
                    if let Ok(output) = std::process::Command::new("osascript")
                        .args(["-e", "tell application \"System Events\" to get name of first application process whose frontmost is true"])
                        .output()
                    {
                        let current = String::from_utf8_lossy(&output.stdout).trim().to_string();
                        if !current.is_empty() && current != last_app {
                            let duration_secs = last_switch.elapsed().as_secs();
                            let timestamp = std::time::SystemTime::now()
                                .duration_since(std::time::UNIX_EPOCH)
                                .unwrap()
                                .as_secs();
                            let event = serde_json::json!({
                                "type": "app_switch",
                                "from_app": last_app,
                                "to_app": current,
                                "duration_secs": duration_secs,
                                "timestamp": timestamp,
                            });
                            let _ = tauri::Emitter::emit(&activity_handle, "activity-event", &event);
                            last_app = current;
                            last_switch = std::time::Instant::now();
                        }
                    }
                }
            });

            // Stop clipboard watcher + activity tracker on app exit
            app.on_window_event(move |_window, event| {
                if let tauri::WindowEvent::Destroyed = event {
                    clip_running.store(false, std::sync::atomic::Ordering::Relaxed);
                    activity_running.store(false, std::sync::atomic::Ordering::Relaxed);
                }
            });

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            get_platform,
            get_arch,
            capture_screenshot,
            get_active_app,
            read_clipboard,
            haptic_feedback,
        ])
        .run(tauri::generate_context!())
        .expect("error while running Luna");
}
