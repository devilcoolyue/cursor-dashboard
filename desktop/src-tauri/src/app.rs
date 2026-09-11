use rand::{rngs::OsRng, RngCore};
use serde::{Deserialize, Serialize};
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
use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    Manager,
};

#[path = "connected.rs"]
mod connected;

fn nonce() -> String {
    let mut bytes = [0u8; 32];
    OsRng.fill_bytes(&mut bytes);
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}
#[derive(Clone, Deserialize, Serialize)]
struct Ready {
    port: u16,
    pid: u32,
    protocol: u32,
    frozen: bool,
    fixture: bool,
    imports_ms: u64,
    core_ms: u64,
    listener_ms: u64,
}
#[derive(Clone)]
struct Connection {
    token: String,
    ready: Ready,
    client: reqwest::blocking::Client,
}
impl Connection {
    fn request(&self, method: &str, path: &str, body: Option<Value>) -> Result<Value, String> {
        let method =
            reqwest::Method::from_bytes(method.as_bytes()).map_err(|_| "Invalid method")?;
        let mut request = self
            .client
            .request(
                method,
                format!("http://127.0.0.1:{}{path}", self.ready.port),
            )
            .bearer_auth(&self.token);
        if let Some(body) = body {
            if serde_json::to_vec(&body)
                .map_err(|_| "Invalid input")?
                .len()
                > 65536
            {
                return Err("Request is too large".into());
            }
            request = request.json(&body);
        }
        let response = request.send().map_err(|_| "Local backend is unavailable")?;
        let status = response.status().as_u16();
        let body: Value = if status == 204 {
            Value::Null
        } else {
            response.json().map_err(|_| "Invalid local response")?
        };
        Ok(json!({"status": status, "body": body}))
    }
}
struct Backend {
    child: Child,
    control: Option<ChildStdin>,
    connection: Option<Connection>,
    start_ms: u128,
}
impl Backend {
    fn start(_app: &tauri::AppHandle, directory: &PathBuf, fixture: bool) -> Result<Self, String> {
        let start = Instant::now();
        let suffix = if cfg!(windows) { ".exe" } else { "" };
        let executable = std::env::current_exe().map_err(|_| "Missing executable location")?;
        let parent = executable.parent().ok_or("Missing executable directory")?;
        let resources = if cfg!(target_os = "macos") {
            parent
                .parent()
                .ok_or("Missing bundle directory")?
                .join("Resources")
        } else {
            parent.to_path_buf()
        };
        let mut path = resources
            .join("runtime")
            .join(format!("cursor-local{suffix}"));
        if cfg!(debug_assertions) && !path.exists() {
            path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .join("runtime")
                .join(format!("cursor-local{suffix}"));
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
            command.creation_flags(0x08000000);
        }
        let mut child = command
            .spawn()
            .map_err(|_| "Cannot start the bundled local runtime")?;
        let control = child.stdin.take();
        let stdout = child.stdout.take().ok_or("Missing runtime channel")?;
        let mut backend = Self {
            child,
            control,
            connection: None,
            start_ms: 0,
        };
        let token = nonce();
        writeln!(
            backend.control.as_mut().ok_or("Missing runtime channel")?,
            "{}",
            json!({"token": token, "data_dir": directory, "fixture": fixture})
        )
        .map_err(|_| "Cannot initialize the local runtime")?;
        let (sender, receiver) = mpsc::channel();
        thread::spawn(move || {
            let mut line = String::new();
            let ready = BufReader::new(stdout)
                .take(4096)
                .read_line(&mut line)
                .map_err(|_| "Cannot read runtime readiness")
                .and_then(|_| {
                    serde_json::from_str::<Ready>(&line).map_err(|_| "Invalid runtime readiness")
                });
            let _ = sender.send(ready);
        });
        let ready = receiver
            .recv_timeout(Duration::from_secs(60))
            .map_err(|_| "Local startup timed out")??;
        if ready.protocol != 1 || ready.port == 0 || ready.pid == 0 || ready.fixture != fixture {
            return Err("Incompatible local runtime".into());
        }
        let client = reqwest::blocking::Client::builder()
            .no_proxy()
            .timeout(Duration::from_secs(180))
            .redirect(reqwest::redirect::Policy::none())
            .build()
            .map_err(|_| "Cannot create native client")?;
        backend.connection = Some(Connection {
            token,
            ready,
            client,
        });
        backend.start_ms = start.elapsed().as_millis();
        Ok(backend)
    }
}
impl Drop for Backend {
    fn drop(&mut self) {
        self.control.take(); // EOF also handles unexpected shell death.
        for _ in 0..1300 {
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
    error: Mutex<Option<String>>,
    starting: AtomicBool,
    background: AtomicBool,
    start: Instant,
    directory: PathBuf,
    report_path: Option<PathBuf>,
    reported: AtomicBool,
}
fn connection(app: &tauri::AppHandle) -> Result<Connection, String> {
    app.state::<Runtime>()
        .backend
        .lock()
        .map_err(|_| "Runtime lock failed")?
        .as_ref()
        .and_then(|backend| backend.connection.clone())
        .ok_or("Local backend is unavailable".into())
}
fn identifier(value: Option<String>) -> Result<String, String> {
    let value = value.ok_or("Missing identifier")?;
    let parsed = uuid::Uuid::parse_str(&value).map_err(|_| "Invalid identifier")?;
    if parsed.to_string() != value {
        return Err("Invalid identifier".into());
    }
    Ok(value)
}
#[derive(Deserialize)]
#[serde(rename_all = "snake_case")]
enum AccountOperation {
    Bootstrap,
    Me,
    List,
    Get,
    Add,
    Reauthorize,
    Edit,
    Delete,
    Refresh,
    Detail,
    Audit,
}
#[derive(Default, Deserialize)]
#[serde(deny_unknown_fields)]
struct Query {
    q: Option<String>,
    tag: Option<String>,
    limit: Option<u16>,
    offset: Option<u32>,
}
fn account_route(
    operation: AccountOperation,
    workspace: Option<String>,
    account: Option<String>,
    query: Query,
) -> Result<(&'static str, String), String> {
    use AccountOperation::*;
    match operation {
        Bootstrap => return Ok(("GET", "/api/v1/bootstrap".into())),
        Me => return Ok(("GET", "/api/v1/me".into())),
        _ => {}
    }
    let workspace = identifier(workspace)?;
    let base = format!("/api/v1/workspaces/{workspace}");
    let (method, path) = match operation {
        List => ("GET", format!("{base}/accounts")),
        Add => ("POST", format!("{base}/accounts")),
        Audit => ("GET", format!("{base}/audit")),
        other => {
            let id = identifier(account)?;
            let base = format!("{base}/accounts/{id}");
            match other {
                Get => ("GET", base),
                Edit => ("PATCH", base),
                Delete => ("DELETE", base),
                Reauthorize => ("POST", format!("{base}/authorization")),
                Refresh => ("POST", format!("{base}/refresh")),
                Detail => ("GET", format!("{base}/detail")),
                _ => return Err("Unknown operation".into()),
            }
        }
    };
    let mut url =
        reqwest::Url::parse(&format!("http://127.0.0.1{path}")).map_err(|_| "Invalid route")?;
    {
        let mut pairs = url.query_pairs_mut();
        if let Some(q) = query.q {
            if q.len() > 1024 {
                return Err("Query too long".into());
            }
            pairs.append_pair("q", &q);
        }
        if let Some(tag) = query.tag {
            if tag.len() > 512 {
                return Err("Tag too long".into());
            }
            pairs.append_pair("tag", &tag);
        }
        if let Some(limit) = query.limit {
            pairs.append_pair("limit", &limit.to_string());
        }
        if let Some(offset) = query.offset {
            pairs.append_pair("offset", &offset.to_string());
        }
    }
    Ok((
        method,
        format!(
            "{}{}",
            url.path(),
            url.query()
                .filter(|q| !q.is_empty())
                .map(|q| format!("?{q}"))
                .unwrap_or_default()
        ),
    ))
}
#[tauri::command(rename_all = "snake_case")]
async fn account_request(
    app: tauri::AppHandle,
    operation: AccountOperation,
    workspace: Option<String>,
    account: Option<String>,
    query: Option<Query>,
    body: Option<Value>,
    connection_id: Option<String>,
) -> Result<Value, String> {
    let (method, route) = account_route(operation, workspace, account, query.unwrap_or_default())?;
    tauri::async_runtime::spawn_blocking(move || connected::request(&app, connection_id, method, &route, body))
        .await
        .map_err(|_| "Native request failed")?
}
#[derive(Deserialize)]
#[serde(rename_all = "snake_case")]
enum NativeOperation {
    Status,
    Unlock,
    Detect,
    Switch,
    SwitchCommand,
    SwitchStatus,
    Backups,
    Restore,
    Background,
    Resume,
}
#[tauri::command]
async fn desktop_request(
    app: tauri::AppHandle,
    operation: NativeOperation,
    body: Option<Value>,
) -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(move || {
        use NativeOperation::*;
        let runtime = app.state::<Runtime>();
        if matches!(operation, Status) && runtime.starting.load(Ordering::SeqCst) { return Ok(json!({"status": 200, "body": {"phase": "starting", "background": false}})); }
        if matches!(operation, Status) && runtime.backend.lock().map_err(|_| "Runtime lock failed")?.is_none() {
            return Ok(json!({"status": 200, "body": {"phase": "unavailable", "error": "Local runtime stopped; restart Cursor Panel", "background": false}}));
        }
        let (method, path) = match operation {
            Status => ("GET", "/native/status"), Unlock => ("POST", "/native/unlock"), Detect => ("GET", "/native/cursor"),
            Switch => ("POST", "/native/switch"), SwitchStatus => ("GET", "/native/switch"), Backups => ("GET", "/native/backups"),
            SwitchCommand => ("POST", "/native/switch-command"),
            Restore => ("POST", "/native/restore"), Background => ("PUT", "/native/background"), Resume => ("POST", "/native/resume"),
        };
        let response = connection(&app)?.request(method, path, body)?;
        if response["status"] == 200 {
            if let Some(background) = response["body"]["background"].as_bool() { runtime.background.store(background, Ordering::SeqCst); }
        }
        Ok(response)
    }).await.map_err(|_| "Native request failed")?
}
#[derive(Deserialize)]
#[serde(rename_all = "snake_case")]
enum ArchiveOperation {
    Export,
    Import,
    Recover,
}
#[tauri::command]
async fn desktop_archive(
    app: tauri::AppHandle,
    operation: ArchiveOperation,
    workspace: Option<String>,
    password: String,
) -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(move || {
        if !(12..=256).contains(&password.chars().count()) {
            return Err("Invalid archive password length".into());
        }
        let dialog =
            rfd::FileDialog::new().add_filter("Cursor Panel encrypted archive", &["cursorarchive"]);
        let path = match operation {
            ArchiveOperation::Export => dialog
                .set_file_name("Cursor Panel.cursorarchive")
                .save_file(),
            _ => dialog.pick_file(),
        };
        let Some(path) = path else {
            return Ok(json!({"status": 200, "body": {"cancelled": true}}));
        };
        let (route, workspace) = match operation {
            ArchiveOperation::Export => ("/native/archive/export", Some(identifier(workspace)?)),
            ArchiveOperation::Import => ("/native/archive/import", Some(identifier(workspace)?)),
            ArchiveOperation::Recover => ("/native/recover", None),
        };
        connection(&app)?.request(
            "POST",
            route,
            Some(json!({"path": path, "password": password, "workspace_id": workspace})),
        )
    })
    .await
    .map_err(|_| "Native archive operation failed")?
}
#[tauri::command]
async fn desktop_open_backups(app: tauri::AppHandle) -> Result<(), String> {
    let path = app.state::<Runtime>().directory.join("cursor-backups");
    tauri::async_runtime::spawn_blocking(move || {
        let command = if cfg!(windows) {
            "explorer.exe"
        } else {
            "/usr/bin/open"
        };
        Command::new(command)
            .arg(path)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|_| "Cannot open backup folder")?;
        Ok(())
    })
    .await
    .map_err(|_| "Cannot open backup folder")?
}
#[tauri::command]
fn frontend_ready(app: tauri::AppHandle, rendered_accounts: usize) -> Result<(), String> {
    let runtime = app.state::<Runtime>();
    if let Some(path) = &runtime.report_path {
        if rendered_accounts != 2 {
            return Err("Fixture DOM is incomplete".into());
        }
        if !runtime.reported.swap(true, Ordering::SeqCst) {
            let guard = runtime.backend.lock().map_err(|_| "Runtime lock failed")?;
            let backend = guard.as_ref().ok_or("Runtime unavailable")?;
            let ready = &backend
                .connection
                .as_ref()
                .ok_or("Runtime unavailable")?
                .ready;
            let report = json!({"backend": ready, "backend_start_ms": backend.start_ms, "ui_ready_ms": runtime.start.elapsed().as_millis(),
                "shell_pid": std::process::id(), "target": env!("P0_TARGET"), "frontend": {"rendered_accounts": rendered_accounts}, "keyring": {"ok": true}});
            fs::write(
                path,
                serde_json::to_vec_pretty(&report).map_err(|_| "Cannot encode report")?,
            )
            .map_err(|_| "Cannot write fixture report")?;
            let handle = app.clone();
            thread::spawn(move || {
                thread::sleep(Duration::from_secs(8));
                handle.exit(0);
            });
        }
    }
    Ok(())
}
pub fn run() {
    let started = Instant::now();
    let args: Vec<_> = std::env::args_os().collect();
    let report = args
        .iter()
        .position(|arg| arg == "--desktop-smoke")
        .and_then(|index| args.get(index + 1))
        .map(PathBuf::from);
    let mut builder = tauri::Builder::default();
    // Verification owns an isolated temporary database and must not activate a user's running app.
    if report.is_none() {
        builder = builder.plugin(tauri_plugin_single_instance::init(|app, _, _| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
        }));
    }
    let application = builder.setup(move |app| {
            let directory = if let Some(report) = &report {
                report
                    .parent()
                    .ok_or("Missing fixture directory")?
                    .join("fixture-data")
            } else {
                app.path().app_data_dir()?
            };
            let fixture = report.is_some();
            app.manage(Runtime {
                backend: Mutex::new(None),
                error: Mutex::new(None),
                starting: AtomicBool::new(true),
                background: AtomicBool::new(false),
                start: started,
                directory: directory.clone(),
                report_path: report,
                reported: AtomicBool::new(false),
            });
            let show = MenuItem::with_id(app, "show", "打开 Cursor Panel", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "退出 Cursor Panel", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show, &quit])?;
            TrayIconBuilder::new()
                .icon(
                    app.default_window_icon()
                        .ok_or("Missing application icon")?
                        .clone(),
                )
                .tooltip("Cursor Panel")
                .menu(&menu)
                .on_menu_event(|app, event| {
                    if event.id.as_ref() == "quit" {
                        app.exit(0);
                    }
                    if event.id.as_ref() == "show" {
                        if let Some(window) = app.get_webview_window("main") {
                            let _ = window.show();
                            let _ = window.set_focus();
                        }
                    }
                })
                .build(app)?;
            let handle = app.handle().clone();
            thread::spawn(move || {
                let outcome = Backend::start(&handle, &directory, fixture);
                let runtime = handle.state::<Runtime>();
                match outcome {
                    Ok(backend) => {
                        if let Some(connection) = &backend.connection {
                            if let Ok(response) = connection.request("GET", "/native/status", None)
                            {
                                if let Some(report) = &runtime.report_path {
                                    let _ = fs::write(report.with_extension("startup.json"), serde_json::to_vec(&json!({"backend": connection.ready, "state": response})).unwrap());
                                }
                                runtime.background.store(
                                    response["body"]["background"].as_bool().unwrap_or(false),
                                    Ordering::SeqCst,
                                );
                            }
                        }
                        *runtime.backend.lock().unwrap() = Some(backend);
                    }
                    Err(error) => {
                        if let Some(report) = &runtime.report_path {
                            let _ = fs::write(report.with_extension("startup.json"), serde_json::to_vec(&json!({"error": error})).unwrap());
                        }
                        *runtime.error.lock().unwrap() = Some(error);
                    }
                }
                runtime.starting.store(false, Ordering::SeqCst);
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            account_request,
            desktop_request,
            desktop_archive,
            desktop_open_backups,
            connected::connection_request,
            connected::remote_manage,
            frontend_ready
        ])
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                let runtime = window.state::<Runtime>();
                if runtime.background.load(Ordering::SeqCst) {
                    api.prevent_close();
                    let _ = window.hide();
                } else {
                    window.app_handle().exit(0);
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("Cannot initialize Cursor Panel");
    application.run(|handle, event| {
        if matches!(event, tauri::RunEvent::Exit) {
            handle.state::<Runtime>().backend.lock().unwrap().take();
        }
    });
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn fixed_routes_reject_url_and_path_injection() {
        assert!(account_route(
            AccountOperation::Get,
            Some("http://example.test".into()),
            None,
            Query::default()
        )
        .is_err());
        let id = "00000000-0000-0000-0000-000000000001".to_string();
        assert!(account_route(
            AccountOperation::Get,
            Some(id.clone()),
            Some("../native/recover".into()),
            Query::default()
        )
        .is_err());
        let (_, path) = account_route(
            AccountOperation::List,
            Some(id),
            None,
            Query {
                q: Some("&admin=true#".into()),
                ..Query::default()
            },
        )
        .unwrap();
        assert!(path.ends_with("?q=%26admin%3Dtrue%23"));
    }
}
