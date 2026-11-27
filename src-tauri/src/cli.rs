//! Use the installed app's executable and runtime from a terminal as well as Finder.
use std::{
    env,
    ffi::{OsStr, OsString},
    path::{Path, PathBuf},
};

use crate::{credentials, Installation};

pub(super) fn user_path(variable: &str, default: PathBuf) -> Result<PathBuf, String> {
    match env::var_os(variable) {
        None => Ok(default),
        Some(value) if Path::new(&value).is_absolute() => Ok(value.into()),
        Some(_) => Err(format!("{variable} must be an absolute path.")),
    }
}

fn locate(executable: &Path) -> Result<Installation, String> {
    let home = dirs::home_dir().ok_or("Cannot find the user home folder.")?;
    let resources = if cfg!(target_os = "macos")
        && executable
            .parent()
            .and_then(Path::parent)
            .is_some_and(|p| p.join("Resources/runtime/manifest.json").is_file())
    {
        executable
            .parent()
            .and_then(Path::parent)
            .unwrap()
            .join("Resources")
    } else if cfg!(debug_assertions) {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../build")
    } else {
        executable
            .parent()
            .ok_or("Cannot locate the Grist installation.")?
            .to_owned()
    };
    let data = dirs::data_dir()
        .ok_or("Cannot find the user data folder.")?
        .join("app.grist.desktop");
    let workspace = dirs::video_dir().unwrap_or(home).join("Grist");
    Installation::for_user(&resources, data, workspace)
}

fn run(mode: &OsStr, args: impl Iterator<Item = OsString>) -> Result<i32, String> {
    let executable = env::current_exe()
        .and_then(|p| p.canonicalize())
        .map_err(|e| e.to_string())?;
    let installation = locate(&executable)?;
    if mode == "__grist_service" {
        let mut keys = credentials::load().unwrap_or_default();
        for name in credentials::PROVIDERS {
            if let Ok(value) = env::var(name) {
                if !value.trim().is_empty() {
                    keys.insert(name.to_string(), value);
                }
            }
        }
        let info = installation.ensure_service(&keys)?;
        println!(
            "{}",
            serde_json::to_string(&info).map_err(|e| e.to_string())?
        );
        return Ok(0);
    }
    let status = installation
        .command(&installation.python)
        .arg(installation.backend_script("cli.py"))
        .args(args)
        .env("GRIST_HOST_EXECUTABLE", executable)
        .env("GRIST_RESOLVED_DATA_DIR", installation.data)
        .status()
        .map_err(|e| format!("Cannot launch the bundled CLI: {e}"))?;
    Ok(status.code().unwrap_or(1))
}

pub(super) fn dispatch() -> Option<i32> {
    let mut args = env::args_os().skip(1);
    let mode = args.next()?;
    if mode != "cli" && mode != "__grist_service" {
        return None;
    }
    Some(match run(&mode, args) {
        Ok(code) => code,
        Err(error) => {
            eprintln!("Grist: {error}");
            1
        }
    })
}

#[cfg(target_os = "macos")]
pub(super) fn install_from_menu(app: tauri::AppHandle) {
    use tauri_plugin_dialog::DialogExt;

    tauri::async_runtime::spawn(async move {
        let result = crate::off_main_thread(|| {
            let host = env::current_exe().map_err(|e| e.to_string())?;
            let output = std::process::Command::new(host)
                .args(["cli", "install"])
                .output()
                .map_err(|e| e.to_string())?;
            if !output.status.success() {
                let error = String::from_utf8_lossy(&output.stderr);
                let message = serde_json::from_str::<serde_json::Value>(&error)
                    .ok()
                    .and_then(|value| value["error"].as_str().map(str::to_owned));
                return Err(message.unwrap_or_else(|| error.trim().to_string()));
            }
            let value: serde_json::Value =
                serde_json::from_slice(&output.stdout).map_err(|e| e.to_string())?;
            let path = value["command"]
                .as_str()
                .ok_or("Cannot locate the installed command.")?;
            let mut message = format!("Installed grist at {path}.");
            if let Some(folder) = value["add_to_path"].as_str() {
                message.push_str(&format!(" Add {folder} to your PATH to use grist by name."));
            }
            Ok(message)
        })
        .await;
        app.dialog()
            .message(result.unwrap_or_else(|error| error))
            .title("Grist CLI")
            .show(|_| {});
    });
}
