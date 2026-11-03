//! Compiles the small C bridge to libx264 (src/x264_shim.c, which sees x264.h as it is, so that
//! nothing here depends on the layout of x264's structures) and links libx264: from X264_DIR
//! (include/ and lib/) when it is set, else from the system. X264_STATIC links it in.

use std::env;
use std::path::PathBuf;
use std::process::Command;

fn main() {
    let out = PathBuf::from(env::var("OUT_DIR").expect("OUT_DIR"));
    let target = env::var("TARGET").unwrap_or_default();
    let windows_gnu = target.contains("windows-gnu");
    let cc = env::var("CC").unwrap_or_else(|_| {
        if windows_gnu && cfg!(not(windows)) {
            "x86_64-w64-mingw32-gcc".into()
        } else {
            "cc".into()
        }
    });
    let ar = env::var("AR").unwrap_or_else(|_| {
        if windows_gnu && cfg!(not(windows)) {
            "x86_64-w64-mingw32-ar".into()
        } else {
            "ar".into()
        }
    });
    let x264_dir = env::var("X264_DIR").ok().map(PathBuf::from);
    let object = out.join("x264_shim.o");
    let mut compile = Command::new(&cc);
    compile
        .args(["-O2", "-std=c99", "-Wall", "-c", "src/x264_shim.c", "-o"])
        .arg(&object);
    if let Some(dir) = &x264_dir {
        compile.arg(format!("-I{}", dir.join("include").display()));
    }
    let done = compile.status().expect("the C compiler");
    assert!(done.success(), "src/x264_shim.c did not compile");
    let library = out.join("libx264_shim.a");
    let _ = std::fs::remove_file(&library);
    let done = Command::new(&ar)
        .arg("crs")
        .arg(&library)
        .arg(&object)
        .status()
        .expect("ar");
    assert!(done.success(), "ar did not make libx264_shim.a");
    println!("cargo:rustc-link-search=native={}", out.display());
    println!("cargo:rustc-link-lib=static=x264_shim");
    if let Some(dir) = &x264_dir {
        println!(
            "cargo:rustc-link-search=native={}",
            dir.join("lib").display()
        );
    }
    let kind = if env::var("X264_STATIC").is_ok() {
        "static"
    } else {
        "dylib"
    };
    println!("cargo:rustc-link-lib={kind}=x264");
    if kind == "static" && !windows_gnu && !target.contains("apple") {
        for library in ["m", "pthread", "dl"] {
            // (x264.pc's Libs.private)
            println!("cargo:rustc-link-lib=dylib={library}");
        }
    }
    println!("cargo:rerun-if-changed=src/x264_shim.c");
    println!("cargo:rerun-if-env-changed=X264_DIR");
    println!("cargo:rerun-if-env-changed=X264_STATIC");
    println!("cargo:rerun-if-env-changed=CC");
}
