use std::process::Command;

fn main() {
    // IMPORTANT: compile the Swift dylib BEFORE `tauri_build::build()`.
    // tauri_build validates `bundle.macOS.frameworks` paths at config-load
    // time and aborts the whole build with "Library not found" if any
    // referenced file is missing. The dylib is produced inside this
    // build.rs (compile_swift_landmarker), so it must exist before the
    // tauri config validator runs.
    #[cfg(target_os = "macos")]
    {
        compile_swift_landmarker();
        // CoreGraphics is required by gesture/cursor.rs (CGMainDisplayID,
        // CGDisplayPixelsWide/High). It's already linked by the swift dylib's
        // dependencies, but make it explicit so removing the swift step in
        // the future doesn't silently break the cursor.
        println!("cargo:rustc-link-lib=framework=CoreGraphics");
    }

    tauri_build::build();

    // Propagate the updater pubkey state from tauri.conf.json into a rustc
    // env var so install_update can fail fast when signing isn't configured.
    propagate_updater_pubkey();
}

fn propagate_updater_pubkey() {
    let conf_path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("tauri.conf.json");
    println!("cargo:rerun-if-changed={}", conf_path.display());
    let pubkey = std::fs::read_to_string(&conf_path)
        .ok()
        .and_then(|s| serde_json::from_str::<serde_json::Value>(&s).ok())
        .and_then(|v| v.pointer("/plugins/updater/pubkey").and_then(|p| p.as_str()).map(|s| s.to_string()))
        .unwrap_or_default();
    // The pubkey can contain quotes / multi-line minisign content; only
    // its emptiness affects install_update behavior, so emit the trimmed
    // length-bearing value.
    println!("cargo:rustc-env=LUNA_UPDATER_PUBKEY={}", pubkey);
}

#[cfg(target_os = "macos")]
fn compile_swift_landmarker() {
    let manifest_dir = std::env::var("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR");
    let out_dir = std::env::var("OUT_DIR").expect("OUT_DIR");
    let swift_src = format!("{}/swift/HandLandmarker.swift", manifest_dir);
    let lib_path = format!("{}/libluna_hand_landmarker.dylib", out_dir);
    // Stable copy for the Tauri bundler — OUT_DIR is a per-build hash
    // path that tauri.conf.json's `bundle.macOS.frameworks` can't predict,
    // so we mirror the dylib into `target/lib/` and reference that there.
    // Without this, Luna.app ships without the dylib and crashes at
    // launch with `Library not loaded: @rpath/libluna_hand_landmarker.dylib`
    // (incident 2026-05-04, v0.1.57).
    let stable_lib_dir = format!("{}/target/lib", manifest_dir);
    let stable_lib_path = format!("{}/libluna_hand_landmarker.dylib", stable_lib_dir);

    println!("cargo:rerun-if-changed={}", swift_src);
    println!("cargo:rerun-if-changed=build.rs");

    let target = std::env::var("TARGET").unwrap_or_else(|_| "arm64-apple-macosx11.0".into());
    let swift_target = if target.contains("aarch64") {
        "arm64-apple-macos11"
    } else {
        "x86_64-apple-macos11"
    };

    // Emit a *dynamic* library, not a static archive. A dynamic Swift dylib
    // pulls in its own runtime references (libswiftCore, libswiftFoundation,
    // libswift_Concurrency, etc.) at load time via @rpath, which is exactly
    // what we want; a static `.a` would force us to enumerate every Swift
    // runtime lib explicitly and is brittle across Xcode versions.
    let status = Command::new("swiftc")
        .args([
            "-emit-library",
            "-o",
            &lib_path,
            "-target",
            swift_target,
            "-parse-as-library",
            "-Xlinker",
            "-install_name",
            "-Xlinker",
            "@rpath/libluna_hand_landmarker.dylib",
            &swift_src,
        ])
        .status()
        .expect("swiftc not found in PATH; the macOS CI runner must have Xcode installed");

    if !status.success() {
        panic!("swiftc failed to compile HandLandmarker.swift");
    }

    // Mirror the freshly-built dylib to the stable location so the Tauri
    // bundler can find it via tauri.conf.json's `bundle.macOS.frameworks`.
    std::fs::create_dir_all(&stable_lib_dir)
        .unwrap_or_else(|e| panic!("create_dir_all {stable_lib_dir}: {e}"));
    std::fs::copy(&lib_path, &stable_lib_path)
        .unwrap_or_else(|e| panic!("copy {lib_path} -> {stable_lib_path}: {e}"));

    println!("cargo:rustc-link-search=native={}", out_dir);
    println!("cargo:rustc-link-lib=dylib=luna_hand_landmarker");
    println!("cargo:rustc-link-lib=framework=Vision");
    println!("cargo:rustc-link-lib=framework=CoreImage");
    println!("cargo:rustc-link-lib=framework=CoreGraphics");

    // Add an rpath pointing at the Swift runtime so the dylib resolves at
    // load time without requiring DYLD_LIBRARY_PATH at run time.
    println!("cargo:rustc-link-arg=-Wl,-rpath,/usr/lib/swift");
    println!("cargo:rustc-link-arg=-Wl,-rpath,@executable_path/../Frameworks");
}
