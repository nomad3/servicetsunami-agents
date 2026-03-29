use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    Manager,
};

#[tauri::command]
fn get_platform() -> String {
    std::env::consts::OS.to_string()
}

#[tauri::command]
fn get_arch() -> String {
    std::env::consts::ARCH.to_string()
}

#[tauri::command]
async fn capture_screenshot() -> Result<String, String> {
    use std::process::Command;

    let timestamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs();
    let path = format!("/tmp/luna-screenshot-{}.png", timestamp);

    // macOS screencapture command
    let output = Command::new("screencapture")
        .args(["-x", "-C", &path]) // -x: no sound, -C: capture cursor
        .output()
        .map_err(|e| format!("Screenshot failed: {}", e))?;

    if !output.status.success() {
        return Err("Screenshot capture failed".to_string());
    }

    // Read file and base64 encode
    let bytes = std::fs::read(&path)
        .map_err(|e| format!("Failed to read screenshot: {}", e))?;
    let _ = std::fs::remove_file(&path); // cleanup

    let encoded = base64_encode(&bytes);
    Ok(encoded)
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

fn setup_global_shortcut(app: &tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut};

    let shortcut = Shortcut::new(Some(Modifiers::SUPER | Modifiers::SHIFT), Code::Space);

    app.global_shortcut().on_shortcut(shortcut, move |app, _shortcut, event| {
        if event.state == tauri_plugin_global_shortcut::ShortcutState::Pressed {
            if let Some(window) = app.get_webview_window("main") {
                if window.is_visible().unwrap_or(false) {
                    let _ = window.hide();
                } else {
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
    tauri::Builder::default()
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }

            // System tray (desktop only)
            #[cfg(desktop)]
            {
                setup_tray(app)?;
                setup_global_shortcut(app)?;
            }

            // Check for updates on startup + every 30 minutes
            let handle = app.handle().clone();
            std::thread::spawn(move || {
                loop {
                    let h = handle.clone();
                    tauri::async_runtime::block_on(async move {
                        match tauri_plugin_updater::UpdaterExt::updater(&h).check().await {
                            Ok(Some(update)) => {
                                log::info!("Update available: {}", update.version);
                                // Emit event to frontend so it can show a banner
                                let _ = tauri::Emitter::emit(&h, "update-available", update.version.clone());
                            }
                            Ok(None) => {
                                log::info!("No update available");
                            }
                            Err(e) => {
                                log::debug!("Update check failed: {}", e);
                            }
                        }
                    });
                    std::thread::sleep(std::time::Duration::from_secs(1800)); // 30 min
                }
            });

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![get_platform, get_arch, capture_screenshot])
        .run(tauri::generate_context!())
        .expect("error while running Luna");
}
