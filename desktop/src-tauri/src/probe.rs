use rand::{rngs::OsRng, RngCore};
use serde::Deserialize;
use serde_json::{json, Value};
use std::{
    fs,
    io::{BufRead, BufReader, Read, Write},
    path::PathBuf,
    process::{Child, ChildStdin, Command, Stdio},
    sync::{
        atomic::{AtomicBool, Ordering},
        mpsc, Mutex,
    },
    thread,
    time::{Duration, Instant},
};
use tauri::Manager;

fn nonce() -> String {
    let mut bytes = [0u8; 32];
    OsRng.fill_bytes(&mut bytes);
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

#[derive(Deserialize)]
struct Ready {
    port: u16,
    pid: u32,
    protocol: u32,
}

struct Backend {
    child: Child,
    control: Option<ChildStdin>,
    ready: Option<Ready>,
    token: String,
    start_ms: u128,
}

impl Backend {
    fn start() -> Result<Self, String> {
        let start = Instant::now();
        let suffix = if cfg!(windows) { ".exe" } else { "" };
        let executable = std::env::current_exe().map_err(|e| e.to_string())?;
        let mut path = executable
            .parent()
            .ok_or("Missing executable directory")?
            .join(format!("p0-backend{suffix}"));
        if cfg!(debug_assertions) && !path.exists() {
            path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .join("binaries")
                .join(format!("p0-backend-{}{suffix}", env!("P0_TARGET")));
        }
        let mut command = Command::new(path);
        command
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .env_remove("PYTHONHOME")
            .env_remove("PYTHONPATH");
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000); // CREATE_NO_WINDOW, keep the private pipes.
        }
        let mut child = command
            .spawn()
            .map_err(|e| format!("Cannot start bundled backend: {e}"))?;
        let control = child.stdin.take();
        let stdout = child.stdout.take().ok_or("Missing backend stdout")?;
        let mut backend = Self {
            child,
            control,
            ready: None,
            token: nonce(),
            start_ms: 0,
        };
        writeln!(
            backend.control.as_mut().ok_or("Missing backend stdin")?,
            "{}",
            json!({"token": backend.token})
        )
        .map_err(|e| e.to_string())?;
        let (sender, receiver) = mpsc::channel();
        thread::spawn(move || {
            let mut line = String::new();
            let result = BufReader::new(stdout)
                .take(4096)
                .read_line(&mut line)
                .map_err(|_| "Cannot read backend readiness".to_string())
                .and_then(|_| {
                    serde_json::from_str::<Ready>(&line)
                        .map_err(|_| "Invalid backend readiness".to_string())
                });
            let _ = sender.send(result);
        });
        let ready = receiver
            .recv_timeout(Duration::from_secs(30))
            .map_err(|_| "Backend startup timed out".to_string())??;
        if ready.protocol != 1 || ready.port == 0 || ready.pid == 0 {
            return Err("Unsupported backend handshake".into());
        }
        backend.ready = Some(ready);
        backend.start_ms = start.elapsed().as_millis();
        Ok(backend)
    }

    fn get(&self, route: &str) -> Result<Value, String> {
        let ready = self.ready.as_ref().ok_or("Backend is not ready")?;
        let client = reqwest::blocking::Client::builder()
            .no_proxy()
            .timeout(Duration::from_secs(5))
            .redirect(reqwest::redirect::Policy::none())
            .build()
            .map_err(|e| e.to_string())?;
        client
            .get(format!("http://127.0.0.1:{}{route}", ready.port))
            .bearer_auth(&self.token)
            .send()
            .map_err(|_| "Backend unavailable".to_string())?
            .error_for_status()
            .map_err(|_| "Backend rejected request".to_string())?
            .json()
            .map_err(|_| "Invalid backend response".to_string())
    }
}

impl Drop for Backend {
    fn drop(&mut self) {
        // EOF is a graceful shutdown signal, including when the shell is killed unexpectedly.
        self.control.take();
        for _ in 0..60 {
            if matches!(self.child.try_wait(), Ok(Some(_))) {
                return;
            }
            thread::sleep(Duration::from_millis(50));
        }
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

struct Runtime {
    backend: Mutex<Option<Backend>>,
    startup_error: Option<String>,
    start: Instant,
    report_path: Option<PathBuf>,
    reported: AtomicBool,
    last_probe: Mutex<Option<Value>>,
}

fn keyring_probe() -> Value {
    // Only a random, synthetic item owned by this probe. Never enumerate user credentials.
    let outcome = (|| -> Result<(), String> {
        let entry = keyring::Entry::new("dev.cursor-panel.p0.probe", &nonce())
            .map_err(|e| e.to_string())?;
        let expected = nonce();
        entry.set_password(&expected).map_err(|e| e.to_string())?;
        let result = entry.get_password();
        let cleanup = entry.delete_credential();
        if result.map_err(|e| e.to_string())? != expected {
            return Err("Roundtrip mismatch".into());
        }
        cleanup.map_err(|e| e.to_string())?;
        Ok(())
    })();
    match outcome {
        Ok(()) => json!({"ok": true, "detail": "synthetic item set/read/deleted"}),
        Err(error) => json!({"ok": false, "detail": error}),
    }
}

#[tauri::command]
async fn probe(app: tauri::AppHandle) -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let runtime = app.state::<Runtime>();
        if let Some(error) = &runtime.startup_error {
            return Err(error.clone());
        }
        let guard = runtime.backend.lock().map_err(|_| "Backend lock failed")?;
        let backend = guard.as_ref().ok_or("Backend stopped")?;
        let health = backend.get("/api/v1/health")?;
        let demo = backend.get("/api/v1/demo")?;
        let result = json!({
            "backend": health, "accounts": demo["accounts"], "fixture": demo["fixture"],
            "keyring": keyring_probe(), "backend_start_ms": backend.start_ms,
        });
        *runtime.last_probe.lock().map_err(|_| "Probe lock failed")? = Some(result.clone());
        Ok(result)
    })
    .await
    .map_err(|e| e.to_string())?
}

#[tauri::command]
fn frontend_ready(
    app: tauri::AppHandle,
    rendered_accounts: usize,
    title: String,
) -> Result<(), String> {
    let runtime = app.state::<Runtime>();
    if let Some(path) = &runtime.report_path {
        if !runtime.reported.swap(true, Ordering::SeqCst) {
            let mut result = runtime
                .last_probe
                .lock()
                .map_err(|_| "Probe lock failed")?
                .clone()
                .ok_or("Probe must complete first")?;
            result["frontend"] = json!({"rendered_accounts": rendered_accounts, "title": title});
            result["ui_ready_ms"] = json!(runtime.start.elapsed().as_millis());
            result["shell_pid"] = json!(std::process::id());
            result["target"] = json!(env!("P0_TARGET"));
            fs::write(
                path,
                serde_json::to_vec_pretty(&result).map_err(|e| e.to_string())?,
            )
            .map_err(|e| e.to_string())?;
            let handle = app.clone();
            thread::spawn(move || {
                // Leave a measurable idle window for the external smoke runner.
                thread::sleep(Duration::from_secs(8));
                handle.exit(0);
            });
        }
    }
    Ok(())
}

pub fn run() {
    let start = Instant::now();
    let args: Vec<_> = std::env::args_os().collect();
    let report_path = args
        .iter()
        .position(|arg| arg == "--p0-smoke")
        .and_then(|index| args.get(index + 1))
        .map(PathBuf::from);
    let (backend, startup_error) = match Backend::start() {
        Ok(backend) => (Some(backend), None),
        Err(error) => (None, Some(error)),
    };
    let application = tauri::Builder::default()
        .manage(Runtime {
            backend: Mutex::new(backend),
            startup_error,
            start,
            report_path,
            reported: AtomicBool::new(false),
            last_probe: Mutex::new(None),
        })
        .invoke_handler(tauri::generate_handler![probe, frontend_ready])
        .on_window_event(|window, event| {
            if matches!(event, tauri::WindowEvent::CloseRequested { .. }) {
                window.app_handle().exit(0);
            }
        })
        .build(tauri::generate_context!())
        .expect("Cannot initialize P0 desktop");
    application.run(|handle, event| {
        if matches!(event, tauri::RunEvent::Exit) {
            handle.state::<Runtime>().backend.lock().unwrap().take();
        }
    });
}
