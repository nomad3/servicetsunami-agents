use std::process::Command;

fn main() {
    tauri_build::build();

    #[cfg(target_os = "macos")]
    compile_swift_landmarker();
}

#[cfg(target_os = "macos")]
fn compile_swift_landmarker() {
    let manifest_dir = std::env::var("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR");
    let out_dir = std::env::var("OUT_DIR").expect("OUT_DIR");
    let swift_src = format!("{}/swift/HandLandmarker.swift", manifest_dir);
    let lib_path = format!("{}/libluna_hand_landmarker.a", out_dir);

    println!("cargo:rerun-if-changed={}", swift_src);
    println!("cargo:rerun-if-changed=build.rs");

    // Build a static library from the Swift source. -parse-as-library is
    // required because the file uses @_cdecl rather than top-level statements.
    let target = std::env::var("TARGET").unwrap_or_else(|_| "arm64-apple-macosx11.0".into());
    let swift_target = if target.contains("aarch64") {
        "arm64-apple-macos11"
    } else {
        "x86_64-apple-macos11"
    };

    let status = Command::new("swiftc")
        .args([
            "-emit-library",
            "-static",
            "-o",
            &lib_path,
            "-target",
            swift_target,
            "-parse-as-library",
            &swift_src,
        ])
        .status()
        .expect("swiftc not found in PATH; the macOS CI runner must have Xcode installed");

    if !status.success() {
        panic!("swiftc failed to compile HandLandmarker.swift");
    }

    println!("cargo:rustc-link-search=native={}", out_dir);
    println!("cargo:rustc-link-lib=static=luna_hand_landmarker");
    println!("cargo:rustc-link-lib=framework=Vision");
    println!("cargo:rustc-link-lib=framework=CoreImage");
    println!("cargo:rustc-link-lib=framework=CoreGraphics");
    // Required so the Swift static lib can resolve its standard-library symbols
    // when linked into a Rust crate that doesn't otherwise know about them.
    println!("cargo:rustc-link-arg=-Xlinker");
    println!("cargo:rustc-link-arg=-no_warn_duplicate_libraries");
    let xcode_dev_dir = Command::new("xcode-select")
        .arg("-p")
        .output()
        .ok()
        .and_then(|o| String::from_utf8(o.stdout).ok())
        .map(|s| s.trim().to_string())
        .unwrap_or_else(|| "/Applications/Xcode.app/Contents/Developer".to_string());
    let swift_lib_dir = format!(
        "{}/Toolchains/XcodeDefault.xctoolchain/usr/lib/swift/macosx",
        xcode_dev_dir
    );
    println!("cargo:rustc-link-search=native={}", swift_lib_dir);
}
