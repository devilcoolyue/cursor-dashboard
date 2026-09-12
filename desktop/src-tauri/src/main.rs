#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
#[cfg(not(feature = "p0-probe"))]
mod app;
#[cfg(feature = "p0-probe")]
mod probe;
#[cfg(not(feature = "p0-probe"))]
mod updates;
fn main() {
    #[cfg(not(feature = "p0-probe"))]
    app::run();
    #[cfg(feature = "p0-probe")]
    probe::run();
}
