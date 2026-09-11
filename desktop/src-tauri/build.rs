fn main() {
    println!(
        "cargo:rustc-env=P0_TARGET={}",
        std::env::var("TARGET").unwrap()
    );
    tauri_build::build()
}
