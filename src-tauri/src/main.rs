#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
mod claude_code;
mod credentials;
mod terminal;

use serde::{Deserialize, Serialize};
use std::{
    ffi::OsString,
    fs::File,
    io::{BufRead, BufReader, Read, Write},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::Mutex,
    time::{Duration, Instant},
};
use tauri::Manager;

#[derive(Default)]
struct Runtime {
    startup: Mutex<()>,
    backend: Mutex<Option<Child>>,
    info: Mutex<Option<Bootstrap>>,
    installation: Mutex<Option<Installation>>,
}
#[derive(Clone, Serialize)]
struct Bootstrap {
    port: u16,
    token: String,
}

/// Bundled programs, relative to the runtime folder; written by scripts/stage.mjs.
#[derive(Deserialize)]
struct Manifest {
    python: String,
    claude: String,
    motion_modules: String,
    chrome: String,
    ffmpeg: String,
    ffprobe: String,
    path: Vec<String>,
}

/// Everything the app ships and the folders it keeps user data in. Every child
/// process gets the same environment, so production never depends on what is
/// installed on the computer.
#[derive(Clone)]
pub(crate) struct Installation {
    python: PathBuf,
    claude: PathBuf,
    engine: PathBuf,
    backend: PathBuf,
    data: PathBuf,
    library: PathBuf,
    workspace: PathBuf,
    environment: Vec<(String, OsString)>,
}

impl Installation {
    fn locate(app: &tauri::AppHandle) -> Result<Self, String> {
        let bundled = app.path().resource_dir().map_err(|e| e.to_string())?;
        // During development the staged parts are used in place (`npm run stage`).
        let resources = if bundled.join("runtime/manifest.json").is_file() {
            bundled
        } else if cfg!(debug_assertions) {
            PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../build")
        } else {
            return Err("This installation is incomplete. Reinstall Book2Video Studio.".into());
        };
        let data = app.path().app_data_dir().map_err(|e| e.to_string())?;
        let workspace = app
            .path()
            .video_dir()
            .or_else(|_| app.path().home_dir())
            .map_err(|e| e.to_string())?
            .join("Book2Video Studio");
        Self::new(&resources, data, workspace)
    }

    /// The installation staged in `resources` (backend, engine, runtime), keeping user data in `data`.
    pub(crate) fn new(resources: &Path, data: PathBuf, workspace: PathBuf) -> Result<Self, String> {
        let runtime = resources.join("runtime");
        let manifest: Manifest = std::fs::read(runtime.join("manifest.json"))
            .ok()
            .and_then(|bytes| serde_json::from_slice(&bytes).ok())
            .ok_or("The bundled runtime is missing. Reinstall Book2Video Studio, or run `npm run stage` when developing.")?;
        let source_backend = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../backend");
        let backend = if cfg!(debug_assertions) && source_backend.is_dir() {
            source_backend
        } else {
            resources.join("backend")
        };
        let library = data.join("library");
        let tool = |relative: &str| runtime.join(relative);
        // Bundled programs come first; the system PATH remains for the agent's own shell use.
        let mut paths: Vec<PathBuf> = manifest.path.iter().map(|p| runtime.join(p)).collect();
        paths.extend(std::env::split_paths(
            &std::env::var_os("PATH").unwrap_or_default(),
        ));
        if !cfg!(windows) {
            for system in ["/usr/bin", "/bin", "/usr/sbin", "/sbin"] {
                if !paths.iter().any(|p| p == Path::new(system)) {
                    paths.push(system.into());
                }
            }
        }
        let chrome = tool(&manifest.chrome);
        let ffmpeg = tool(&manifest.ffmpeg);
        let claude = tool(&manifest.claude);
        let environment = vec![
            (
                "PATH".into(),
                std::env::join_paths(paths).map_err(|e| e.to_string())?,
            ),
            ("AIGC_VIDEO_LIBRARY_DIR".into(), library.clone().into()),
            ("AIGC_MOTION_MODULES".into(), tool(&manifest.motion_modules).into()),
            ("AIGC_BROWSER_PATH".into(), chrome.clone().into()),
            ("AIGC_FFMPEG_PATH".into(), ffmpeg.clone().into()),
            ("AIGC_FFPROBE_PATH".into(), tool(&manifest.ffprobe).into()),
            ("PRODUCER_HEADLESS_SHELL_PATH".into(), chrome.into()),
            ("IMAGEIO_FFMPEG_EXE".into(), ffmpeg.into()),
            ("STUDIO_CLAUDE".into(), claude.clone().into()),
            ("PYTHONNOUSERSITE".into(), "1".into()),
            ("PYTHONDONTWRITEBYTECODE".into(), "1".into()),
            ("PYTHONUTF8".into(), "1".into()),
        ];
        Ok(Self {
            python: tool(&manifest.python),
            claude,
            engine: resources.join("engine"),
            backend,
            data,
            library,
            workspace,
            environment,
        })
    }

    pub(crate) fn backend_script(&self, name: &str) -> PathBuf {
        self.backend.join(name)
    }

    /// The bundled environment for a child process; a user's Python settings must not leak in.
    pub(crate) fn environment(&self) -> impl Iterator<Item = (&str, &OsString)> {
        self.environment.iter().map(|(k, v)| (k.as_str(), v))
    }
    pub(crate) const CLEARED: [&'static str; 3] = ["PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"];

    pub(crate) fn command(&self, program: &Path) -> Command {
        let mut command = Command::new(program);
        command.envs(self.environment());
        for name in Self::CLEARED {
            command.env_remove(name);
        }
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000);
        }
        command
    }
}

/// Run blocking work (process start-up, keychain, file copies) off the main
/// thread so the window stays responsive, for example during a system prompt.
pub(crate) async fn off_main_thread<T: Send + 'static>(
    work: impl FnOnce() -> Result<T, String> + Send + 'static,
) -> Result<T, String> {
    tauri::async_runtime::spawn_blocking(work)
        .await
        .map_err(|e| e.to_string())?
}

pub(crate) fn installation(app: &tauri::AppHandle) -> Result<Installation, String> {
    app.state::<Runtime>()
        .installation
        .lock()
        .unwrap()
        .clone()
        .ok_or_else(|| "Start the project service first".into())
}

/// Ask the local project service where a project lives. Paths supplied by the
/// WebView or a launch manifest are only trusted after this lookup.
pub(crate) fn project_root(info: &Bootstrap, project: &str) -> Result<PathBuf, String> {
    // Only loopback is contacted; environment proxies must not see the token.
    let agent: ureq::Agent = ureq::Agent::config_builder()
        .proxy(None)
        .timeout_global(Some(Duration::from_secs(5)))
        .build()
        .into();
    let mut response = agent
        .get(format!("http://127.0.0.1:{}/api/project-path", info.port))
        .query("project", project)
        .header("Authorization", format!("Bearer {}", info.token))
        .call()
        .map_err(|_| "Cannot find this project. Restart Book2Video Studio.".to_string())?;
    let value: serde_json::Value = response
        .body_mut()
        .read_json()
        .map_err(|_| "The project service returned an invalid project.".to_string())?;
    PathBuf::from(value["path"].as_str().ok_or("Unknown project")?)
        .canonicalize()
        .map_err(|e| e.to_string())
}

fn stop_service(mut child: Child) {
    if let Some(mut input) = child.stdin.take() {
        let _ = input.write_all(b"shutdown\n");
    }
    let deadline = Instant::now() + Duration::from_secs(7);
    while Instant::now() < deadline {
        if child.try_wait().ok().flatten().is_some() {
            return;
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    let _ = child.kill();
    let _ = child.wait();
}

/// Copy a service stream into its log so a full pipe can never stall the service.
fn drain(stream: impl Read + Send + 'static, mut log: File) {
    std::thread::spawn(move || {
        let _ = std::io::copy(&mut BufReader::new(stream), &mut log);
    });
}

fn start_service(app: &tauri::AppHandle) -> Result<Bootstrap, String> {
    // A credential store that cannot be read (locked, or access denied) must not keep the app
    // from starting: Settings → API keys reports it, and the keys follow once they can be read.
    let keys = credentials::load().unwrap_or_else(|_| {
        credentials::defer_apply();
        Default::default()
    });
    let runtime = app.state::<Runtime>();
    *runtime.info.lock().unwrap() = None;
    if let Some(process) = runtime.backend.lock().unwrap().take() {
        stop_service(process);
    }
    let installation = Installation::locate(app)?;
    let logs = installation.data.join("logs");
    std::fs::create_dir_all(&logs).map_err(|e| e.to_string())?;
    let log = File::create(logs.join("service.log")).map_err(|e| e.to_string())?;
    let token = uuid::Uuid::new_v4().to_string() + &uuid::Uuid::new_v4().to_string();
    let mut command = installation.command(&installation.python);
    command
        .arg("-u")
        .arg(installation.backend_script("server.py"))
        .arg("--engine")
        .arg(&installation.engine)
        .arg("--data")
        .arg(&installation.data)
        .arg("--workspace")
        .arg(&installation.workspace)
        .current_dir(&installation.data)
        .env("STUDIO_TOKEN", &token)
        .env("STUDIO_NATIVE_HOST", "1")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut child = command
        .spawn()
        .map_err(|e| format!("Cannot start the project service: {e}"))?;
    let mut stdout = BufReader::new(child.stdout.take().ok_or("No service output")?);
    let mut line = String::new();
    stdout.read_line(&mut line).map_err(|e| e.to_string())?;
    let handshake: serde_json::Value = match serde_json::from_str(&line) {
        Ok(value) => value,
        Err(_) => {
            // Stop it first: a service that is still running would never close its error stream.
            let _ = child.kill();
            let _ = child.wait();
            let mut error = String::new();
            if let Some(stderr) = child.stderr.take() {
                let _ = BufReader::new(stderr).read_to_string(&mut error);
            }
            let _ = std::fs::write(logs.join("service.log"), &error);
            return Err(format!(
                "The project service could not start: {}",
                error.trim()
            ));
        }
    };
    drain(stdout, log.try_clone().map_err(|e| e.to_string())?);
    drain(child.stderr.take().ok_or("No service errors stream")?, log);
    let info = Bootstrap {
        port: handshake["port"].as_u64().ok_or("No backend port")? as u16,
        token,
    };
    if let Err(error) = credentials::apply(&info, &keys) {
        stop_service(child);
        return Err(error);
    }
    *runtime.installation.lock().unwrap() = Some(installation);
    *runtime.info.lock().unwrap() = Some(info.clone());
    *runtime.backend.lock().unwrap() = Some(child);
    Ok(info)
}

#[tauri::command]
async fn bootstrap(app: tauri::AppHandle) -> Result<Bootstrap, String> {
    off_main_thread(move || {
        let runtime = app.state::<Runtime>();
        let _startup = runtime.startup.lock().unwrap();
        if let Some(info) = runtime.info.lock().unwrap().clone() {
            return Ok(info);
        }
        start_service(&app)
    })
    .await
}

fn library_source_folder(library: &Path, path: &str) -> Result<PathBuf, String> {
    let library = library.canonicalize().map_err(|e| e.to_string())?;
    let source = PathBuf::from(path)
        .canonicalize()
        .map_err(|e| e.to_string())?;
    if !source.starts_with(library.join("skills")) && !source.starts_with(library.join("prompts")) {
        return Err("Choose a source file inside the skill and template library.".into());
    }
    if source.is_dir() {
        Ok(source)
    } else {
        source
            .parent()
            .map(|p| p.to_path_buf())
            .ok_or("No source folder".into())
    }
}

fn reveal(folder: &Path) -> Result<(), String> {
    let program = if cfg!(target_os = "macos") {
        "open"
    } else if cfg!(windows) {
        "explorer.exe"
    } else {
        "xdg-open"
    };
    Command::new(program)
        .arg(folder)
        .spawn()
        .map(|_| ())
        .map_err(|e| e.to_string())
}

/// Show a file selected in its folder (Finder and Explorer select it; elsewhere the folder opens).
fn reveal_file(file: &Path) -> Result<(), String> {
    let mut command = if cfg!(target_os = "macos") {
        let mut c = Command::new("open");
        c.arg("-R").arg(file);
        c
    } else if cfg!(windows) {
        let mut c = Command::new("explorer.exe");
        c.arg(format!("/select,{}", file.display()));
        c
    } else {
        let mut c = Command::new("xdg-open");
        c.arg(file.parent().ok_or("No folder")?);
        c
    };
    command.spawn().map(|_| ()).map_err(|e| e.to_string())
}

#[tauri::command]
fn open_library_folder(app: tauri::AppHandle, path: String) -> Result<(), String> {
    reveal(&library_source_folder(&installation(&app)?.library, &path)?)
}

/// Show the default projects folder or the editable library in Finder or Explorer.
#[tauri::command]
fn open_folder(app: tauri::AppHandle, folder: String) -> Result<(), String> {
    let installation = installation(&app)?;
    let target = match folder.as_str() {
        "workspace" => installation.workspace,
        "library" => installation.library,
        _ => return Err("Unknown folder".into()),
    };
    std::fs::create_dir_all(&target).map_err(|e| e.to_string())?;
    reveal(&target)
}

/// Show the project folder, a folder inside it (such as a segment's motion work), or one of its
/// files selected, in Finder or Explorer. An empty path is the project folder itself.
#[tauri::command]
async fn reveal_project_path(
    app: tauri::AppHandle,
    project: String,
    path: String,
) -> Result<(), String> {
    off_main_thread(move || {
        let info = app
            .state::<Runtime>()
            .info
            .lock()
            .unwrap()
            .clone()
            .ok_or("Start the project service first")?;
        let root = project_root(&info, &project)?;
        let target = root.join(&path).canonicalize().map_err(|e| e.to_string())?;
        if !target.starts_with(&root) || target.components().any(|c| c.as_os_str() == ".studio") {
            return Err("Choose a file or folder inside this project".into());
        }
        if target.is_file() {
            reveal_file(&target)
        } else {
            reveal(&target)
        }
    })
    .await
}

/// The standard menu, with Quit (⌘Q) closing the window instead of ending the app at once: macOS's own
/// Quit exits without asking the window, so unsaved edits and running work would get no question.
fn app_menu(app: &tauri::AppHandle) -> tauri::Result<tauri::menu::Menu<tauri::Wry>> {
    use tauri::menu::{Menu, MenuItem, MenuItemKind};
    let menu = Menu::default(app)?;
    for item in menu.items()? {
        let MenuItemKind::Submenu(submenu) = item else { continue };
        for entry in submenu.items()? {
            let MenuItemKind::Predefined(predefined) = entry else { continue };
            if predefined.text()?.starts_with("Quit") {
                submenu.remove(&predefined)?;
                submenu.append(&MenuItem::with_id(app, QUIT_MENU_ID, "Quit Book2Video Studio", true, Some("CmdOrCtrl+Q"))?)?;
            }
        }
    }
    Ok(menu)
}

const QUIT_MENU_ID: &str = "studio-quit";

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .menu(app_menu)
        .on_menu_event(|app, event| {
            if event.id() == QUIT_MENU_ID {
                // The window asks about unsaved edits and running work, then closes; the app ends with it.
                match app.get_webview_window("main") {
                    Some(window) => { let _ = window.close(); }
                    None => app.exit(0),
                }
            }
        })
        .manage(Runtime::default())
        .manage(terminal::Terminals::default())
        .invoke_handler(tauri::generate_handler![
            bootstrap,
            open_library_folder,
            open_folder,
            reveal_project_path,
            credentials::credential_status,
            credentials::credential_save,
            claude_code::claude_code_status,
            claude_code::claude_code_set_mode,
            terminal::terminal_start,
            terminal::terminal_write,
            terminal::terminal_resize,
            terminal::terminal_stop
        ])
        .build(tauri::generate_context!())
        .expect("Cannot build Book2Video Studio")
        .run(|app, event| {
            if let tauri::RunEvent::Exit = event {
                app.state::<terminal::Terminals>().stop_all();
                if let Some(child) = app.state::<Runtime>().backend.lock().unwrap().take() {
                    stop_service(child);
                }
            }
        });
}

#[cfg(test)]
mod library_tests {
    use super::library_source_folder;
    use std::{env, fs};

    #[test]
    fn source_folder_is_confined_to_skills_and_prompts() {
        let root = env::temp_dir().join(format!("studio-library-{}", uuid::Uuid::new_v4()));
        fs::create_dir_all(root.join("skills/step1/test")).unwrap();
        fs::create_dir_all(root.join("prompts")).unwrap();
        let file = root.join("skills/step1/test/SKILL.md");
        fs::write(&file, "Test").unwrap();
        let unrelated = root.join("unrelated.txt");
        fs::write(&unrelated, "Preserve").unwrap();
        assert_eq!(
            library_source_folder(&root, file.to_str().unwrap()).unwrap(),
            file.parent().unwrap().canonicalize().unwrap()
        );
        assert_eq!(
            library_source_folder(&root, root.join("prompts").to_str().unwrap()).unwrap(),
            root.join("prompts").canonicalize().unwrap()
        );
        assert!(library_source_folder(&root, unrelated.to_str().unwrap()).is_err());
        #[cfg(unix)]
        {
            let link = root.join("skills/outside.md");
            std::os::unix::fs::symlink(&unrelated, &link).unwrap();
            assert!(library_source_folder(&root, link.to_str().unwrap()).is_err());
        }
        fs::remove_dir_all(root).unwrap();
    }
}
