#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
mod cli;
mod credentials;
mod terminal;

use serde::{Deserialize, Serialize};
use std::{
    ffi::OsString,
    io::Write,
    path::{Path, PathBuf},
    process::{Command, Stdio},
    sync::Mutex,
    time::Duration,
};
use tauri::Manager;

#[derive(Default)]
struct Runtime {
    startup: Mutex<()>,
    info: Mutex<Option<Bootstrap>>,
    installation: Mutex<Option<Installation>>,
}
#[derive(Clone, Deserialize, Serialize)]
struct Bootstrap {
    port: u16,
    token: String,
    #[serde(default)]
    host_pid: u32,
}

/// Bundled programs, relative to the runtime folder; written by scripts/stage.mjs.
#[derive(Deserialize)]
struct Manifest {
    python: String,
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
            return Err("This installation is incomplete. Reinstall Grist.".into());
        };
        let data = app.path().app_data_dir().map_err(|e| e.to_string())?;
        let workspace = app
            .path()
            .video_dir()
            .or_else(|_| app.path().home_dir())
            .map_err(|e| e.to_string())?
            .join("Grist");
        Self::for_user(&resources, data, workspace)
    }

    fn for_user(resources: &Path, data: PathBuf, workspace: PathBuf) -> Result<Self, String> {
        let data = cli::user_path("GRIST_DATA_DIR", data)?;
        let workspace = cli::user_path("GRIST_WORKSPACE", workspace)?;
        if std::env::var_os("GRIST_DATA_DIR").is_none() {
            adopt_legacy_data(&data);
        }
        Self::new(resources, data, workspace)
    }

    /// The installation staged in `resources` (backend, engine, runtime), keeping user data in `data`.
    pub(crate) fn new(resources: &Path, data: PathBuf, workspace: PathBuf) -> Result<Self, String> {
        let runtime = resources.join("runtime");
        let manifest: Manifest = std::fs::read(runtime.join("manifest.json"))
            .ok()
            .and_then(|bytes| serde_json::from_slice(&bytes).ok())
            .ok_or("The bundled runtime is missing. Reinstall Grist, or run `npm run stage` when developing.")?;
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
            ("PYTHONNOUSERSITE".into(), "1".into()),
            ("PYTHONDONTWRITEBYTECODE".into(), "1".into()),
            ("PYTHONUTF8".into(), "1".into()),
        ];
        Ok(Self {
            python: tool(&manifest.python),
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

/// The app data folder of the builds named Book2Video Studio and Stuff 2 Video, beside today's.
const LEGACY_IDENTIFIER: &str = "com.book2video.studio";

/// Keep the library, project list and settings of an earlier build: its data folder becomes this
/// one the first time this build runs.
fn adopt_legacy_data(data: &Path) {
    let legacy = data.with_file_name(LEGACY_IDENTIFIER);
    if !data.exists() && legacy.is_dir() {
        let _ = std::fs::rename(&legacy, data);
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
        .map_err(|_| "Cannot find this project. Restart Grist.".to_string())?;
    let value: serde_json::Value = response
        .body_mut()
        .read_json()
        .map_err(|_| "The project service returned an invalid project.".to_string())?;
    PathBuf::from(value["path"].as_str().ok_or("Unknown project")?)
        .canonicalize()
        .map_err(|e| e.to_string())
}

impl Installation {
    fn ensure_service(&self, keys: &std::collections::BTreeMap<String, String>) -> Result<Bootstrap, String> {
        let mut command = self.command(&self.python);
        for name in credentials::PROVIDERS {
            command.env_remove(name);
        }
        let mut child = command
            .arg(self.backend_script("service.py"))
            .arg("--engine").arg(&self.engine)
            .arg("--data").arg(&self.data)
            .arg("--workspace").arg(&self.workspace)
            .env_remove("STUDIO_TOKEN")
            .env_remove("STUDIO_NATIVE_HOST")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|e| format!("Cannot start the project service: {e}"))?;
        if let Some(mut input) = child.stdin.take() {
            let _ = input.write_all(&serde_json::to_vec(keys).map_err(|e| e.to_string())?);
        }
        let output = child.wait_with_output().map_err(|e| e.to_string())?;
        if !output.status.success() {
            return Err(String::from_utf8_lossy(&output.stderr).trim().to_string());
        }
        serde_json::from_slice(&output.stdout).map_err(|e| format!("Invalid project service response: {e}"))
    }
}

fn start_service(app: &tauri::AppHandle) -> Result<Bootstrap, String> {
    // A locked store should not prevent the app from starting.
    let keys = credentials::load();
    let installation = Installation::locate(app)?;
    let mut info = match keys {
        Ok(keys) => {
            let info = installation.ensure_service(&keys)?;
            // Also update a service started by the CLI while the vault was locked.
            credentials::apply(&info, &keys)?;
            info
        }
        Err(_) => {
            credentials::defer_apply();
            installation.ensure_service(&Default::default())?
        }
    };
    info.host_pid = std::process::id();
    let runtime = app.state::<Runtime>();
    *runtime.installation.lock().unwrap() = Some(installation);
    *runtime.info.lock().unwrap() = Some(info.clone());
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
                #[cfg(target_os = "macos")]
                submenu.append(&MenuItem::with_id(app, INSTALL_CLI_MENU_ID, "Install Command-Line Tool…", true, None::<&str>)?)?;
                submenu.remove(&predefined)?;
                submenu.append(&MenuItem::with_id(app, QUIT_MENU_ID, "Quit Grist", true, Some("CmdOrCtrl+Q"))?)?;
            }
        }
    }
    Ok(menu)
}

const QUIT_MENU_ID: &str = "studio-quit";
#[cfg(target_os = "macos")]
const INSTALL_CLI_MENU_ID: &str = "studio-install-cli";

fn main() {
    if let Some(status) = cli::dispatch() {
        std::process::exit(status);
    }
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .menu(app_menu)
        .on_menu_event(|app, event| {
            #[cfg(target_os = "macos")]
            if event.id() == INSTALL_CLI_MENU_ID {
                cli::install_from_menu(app.clone());
                return;
            }
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
            terminal::terminal_start,
            terminal::terminal_write,
            terminal::terminal_resize,
            terminal::terminal_stop
        ])
        .build(tauri::generate_context!())
        .expect("Cannot build Grist")
        .run(|app, event| {
            if let tauri::RunEvent::Exit = event {
                app.state::<terminal::Terminals>().stop_all();
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

#[cfg(test)]
mod legacy_tests {
    use super::{adopt_legacy_data, LEGACY_IDENTIFIER};
    use std::{env, fs};

    #[test]
    fn earlier_data_folder_is_adopted_once() {
        let root = env::temp_dir().join(format!("studio-legacy-{}", uuid::Uuid::new_v4()));
        let (legacy, data) = (root.join(LEGACY_IDENTIFIER), root.join("app.grist.desktop"));
        fs::create_dir_all(legacy.join("library")).unwrap();
        fs::write(legacy.join("projects.json"), "{}").unwrap();
        adopt_legacy_data(&data);
        assert!(data.join("library").is_dir() && data.join("projects.json").is_file());
        assert!(!legacy.exists());
        // Data this build already keeps is never replaced.
        fs::create_dir_all(&legacy).unwrap();
        adopt_legacy_data(&data);
        assert!(legacy.is_dir() && data.join("projects.json").is_file());
        fs::remove_dir_all(root).unwrap();
    }
}
