use std::process::Command;

fn main() {
    tauri_build::build();

    #[cfg(target_os = "macos")]
    {
        compile_swift_landmarker();
        // CoreGraphics is required by gesture/cursor.rs (CGMainDisplayID,
        // CGDisplayPixelsWide/High). It's already linked by the swift dylib's
        // dependencies, but make it explicit so removing the swift step in
        // the future doesn't silently break the cursor.
        println!("cargo:rustc-link-lib=framework=CoreGraphics");
    }
}

#[cfg(target_os = "macos")]
fn compile_swift_landmarker() {
    let manifest_dir = std::env::var("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR");
    let out_dir = std::env::var("OUT_DIR").expect("OUT_DIR");
    let swift_src = format!("{}/swift/HandLandmarker.swift", manifest_dir);
    let lib_path = format!("{}/libluna_hand_landmarker.dylib", out_dir);

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
