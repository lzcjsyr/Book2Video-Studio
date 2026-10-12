//! Claude Code runs in one of two ways. Built in: the copy shipped with Studio,
//! connected to the language-model provider and API key chosen in Settings.
//! Subscription: the copy installed on this computer, signed in with the user's
//! own Claude account, so their Claude plan covers the sessions.
use serde::{Deserialize, Serialize};
use std::{
    path::{Path, PathBuf},
    process::{Command, Output, Stdio},
    time::{Duration, Instant},
};
use tauri::Manager;

const FILE: &str = "claude-code.json";
/// What the built-in mode sets to reach a provider. A subscription session must not
/// inherit any of it, or Claude Code would bill that endpoint instead of the login.
pub const PROVIDER_ENV: [&str; 10] = [
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_SMALL_FAST_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "CLAUDE_CODE_SUBAGENT_MODEL",
    "CLAUDE_CODE_EFFORT_LEVEL",
];

#[derive(Clone, Copy, Debug, Default, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Mode {
    #[default]
    ApiKey,
    Subscription,
}

#[derive(Default, Serialize, Deserialize)]
struct Saved {
    mode: Mode,
    /// The installed Claude Code found by the last check.
    #[serde(default)]
    path: Option<PathBuf>,
}

#[derive(Default, Serialize)]
pub struct Local {
    path: Option<String>,
    version: Option<String>,
    logged_in: bool,
    auth_method: Option<String>,
    subscription: Option<String>,
    email: Option<String>,
    error: Option<String>,
}

#[derive(Serialize)]
pub struct Status {
    mode: Mode,
    local: Option<Local>,
}

fn file(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    Ok(app
        .path()
        .app_data_dir()
        .map_err(|e| e.to_string())?
        .join(FILE))
}

fn load(app: &tauri::AppHandle) -> Saved {
    file(app)
        .ok()
        .and_then(|path| std::fs::read(path).ok())
        .and_then(|bytes| serde_json::from_slice(&bytes).ok())
        .unwrap_or_default()
}

fn store(app: &tauri::AppHandle, saved: &Saved) -> Result<(), String> {
    let path = file(app)?;
    std::fs::create_dir_all(path.parent().unwrap()).map_err(|e| e.to_string())?;
    let temporary = path.with_extension("json.tmp");
    std::fs::write(&temporary, serde_json::to_vec_pretty(saved).unwrap())
        .map_err(|e| e.to_string())?;
    std::fs::rename(temporary, path).map_err(|e| e.to_string())
}

pub fn mode(app: &tauri::AppHandle) -> Mode {
    load(app).mode
}

/// Run a short command, giving up after `limit` (a login shell or a stalled CLI must not hang the app).
fn run(mut command: Command, limit: Duration) -> Result<Output, String> {
    command
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    let mut child = command.spawn().map_err(|e| e.to_string())?;
    let started = Instant::now();
    while child.try_wait().map_err(|e| e.to_string())?.is_none() {
        if started.elapsed() > limit {
            let _ = child.kill();
            let _ = child.wait();
            return Err("did not answer in time".into());
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    child.wait_with_output().map_err(|e| e.to_string())
}

/// Where the user's own Claude Code is. Apps opened from the Finder do not get the
/// shell's PATH, so ask the login shell first, then look in the installers' folders.
fn find(bundled: &Path) -> Option<PathBuf> {
    let home =
        std::env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" }).map(PathBuf::from)?;
    let mut candidates = Vec::new();
    #[cfg(not(windows))]
    {
        let shell = std::env::var("SHELL").unwrap_or_else(|_| "/bin/zsh".into());
        let mut lookup = Command::new(shell);
        lookup.args(["-ilc", "command -v claude"]);
        if let Ok(output) = run(lookup, Duration::from_secs(10)) {
            let text = String::from_utf8_lossy(&output.stdout).into_owned();
            candidates.extend(
                text.lines()
                    .rev()
                    .map(str::trim)
                    .filter(|l| l.starts_with('/'))
                    .map(PathBuf::from),
            );
        }
        for relative in [
            ".local/bin/claude",
            ".claude/local/claude",
            ".npm-global/bin/claude",
            ".bun/bin/claude",
            ".volta/bin/claude",
        ] {
            candidates.push(home.join(relative));
        }
        candidates.extend(["/opt/homebrew/bin/claude", "/usr/local/bin/claude"].map(PathBuf::from));
    }
    #[cfg(windows)]
    {
        if let Ok(output) = run(
            {
                let mut c = Command::new("where");
                c.arg("claude");
                c
            },
            Duration::from_secs(10),
        ) {
            candidates.extend(
                String::from_utf8_lossy(&output.stdout)
                    .lines()
                    .map(|l| PathBuf::from(l.trim())),
            );
        }
        candidates.push(home.join(".local/bin/claude.exe"));
        if let Some(appdata) = std::env::var_os("APPDATA") {
            candidates.push(PathBuf::from(appdata).join("npm/claude.cmd"));
        }
    }
    let bundled = bundled.canonicalize().ok();
    candidates
        .into_iter()
        .find(|path| path.is_file() && path.canonicalize().ok() != bundled)
}

/// The command for the installed Claude Code, with Studio's tools on PATH but no provider settings.
fn local_command(program: &Path, installation: Option<&crate::Installation>) -> Command {
    let mut command = match installation {
        Some(installation) => installation.command(program),
        None => Command::new(program),
    };
    for name in PROVIDER_ENV {
        command.env_remove(name);
    }
    command
}

fn inspect(program: &Path, installation: Option<&crate::Installation>) -> Local {
    let mut local = Local {
        path: Some(program.display().to_string()),
        ..Default::default()
    };
    let mut version = local_command(program, installation);
    version.arg("--version");
    match run(version, Duration::from_secs(20)) {
        Ok(output) if output.status.success() => {
            local.version = String::from_utf8_lossy(&output.stdout)
                .lines()
                .next()
                .map(|l| l.trim().to_string());
        }
        Ok(output) => {
            local.error = Some(format!(
                "Claude Code did not start: {}",
                String::from_utf8_lossy(&output.stderr).trim()
            ));
            return local;
        }
        Err(e) => {
            local.error = Some(format!("Claude Code did not start: {e}"));
            return local;
        }
    }
    let mut status = local_command(program, installation);
    status.args(["auth", "status", "--json"]);
    // Signed out also answers with JSON (and a failure code).
    let parsed = run(status, Duration::from_secs(20))
        .ok()
        .and_then(|output| serde_json::from_slice::<serde_json::Value>(&output.stdout).ok());
    match parsed {
        Some(auth) => {
            let text = |key: &str| auth.get(key).and_then(|v| v.as_str()).map(String::from);
            local.logged_in = auth.get("loggedIn").and_then(|v| v.as_bool()).unwrap_or(false);
            local.auth_method = text("authMethod");
            local.subscription = text("subscriptionType");
            local.email = text("email");
        }
        None => local.error = Some("This Claude Code cannot report its login. Run `claude update` in Terminal, then check again.".into()),
    }
    local
}

/// The installed Claude Code to launch: the one found last time, or a fresh search.
pub fn local_program(
    app: &tauri::AppHandle,
    installation: &crate::Installation,
) -> Result<PathBuf, String> {
    let mut saved = load(app);
    if let Some(path) = saved.path.as_ref().filter(|p| p.is_file()) {
        return Ok(path.clone());
    }
    let found = find(&installation.claude).ok_or(
        "Claude Code is not installed on this computer. Install it from claude.com/code, or choose the built-in Claude Code in Settings → API keys.",
    )?;
    saved.path = Some(found.clone());
    store(app, &saved)?;
    Ok(found)
}

#[tauri::command]
pub async fn claude_code_status(app: tauri::AppHandle, check: bool) -> Result<Status, String> {
    crate::off_main_thread(move || {
        let mut saved = load(&app);
        if !check {
            return Ok(Status { mode: saved.mode, local: None });
        }
        let installation = crate::installation(&app).ok();
        let bundled = installation.as_ref().map(|i| i.claude.clone()).unwrap_or_default();
        let local = match find(&bundled) {
            Some(program) => {
                saved.path = Some(program.clone());
                store(&app, &saved)?;
                inspect(&program, installation.as_ref())
            }
            None => Local {
                error: Some("Claude Code is not installed on this computer. Install it from claude.com/code, then check again.".into()),
                ..Default::default()
            },
        };
        Ok(Status { mode: saved.mode, local: Some(local) })
    })
    .await
}

#[tauri::command]
pub async fn claude_code_set_mode(app: tauri::AppHandle, mode: Mode) -> Result<Status, String> {
    crate::off_main_thread(move || {
        let mut saved = load(&app);
        saved.mode = mode;
        store(&app, &saved)?;
        Ok(Status { mode, local: None })
    })
    .await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn modes_are_stored_by_name() {
        let saved: Saved = serde_json::from_str(r#"{"mode":"subscription"}"#).unwrap();
        assert_eq!(saved.mode, Mode::Subscription);
        assert_eq!(
            serde_json::to_string(&Mode::ApiKey).unwrap(),
            r#""api_key""#
        );
        assert_eq!(Saved::default().mode, Mode::ApiKey);
    }

    #[test]
    fn a_stalled_command_is_stopped() {
        let mut command = Command::new("sleep");
        command.arg("5");
        let started = Instant::now();
        assert!(run(command, Duration::from_millis(200)).is_err());
        assert!(started.elapsed() < Duration::from_secs(2));
    }

    /// Uses the Claude Code installed on this computer: `cargo test -- --ignored`.
    #[test]
    #[ignore]
    fn finds_and_inspects_the_installed_claude_code() {
        let program = find(Path::new("/nonexistent")).expect("Claude Code is installed");
        let local = inspect(&program, None);
        assert!(local.version.is_some(), "{:?}", local.error);
        assert!(local.error.is_none(), "{:?}", local.error);
        println!(
            "{} {:?} logged_in={} via {:?} plan {:?}",
            program.display(),
            local.version,
            local.logged_in,
            local.auth_method,
            local.subscription
        );
    }

    /// Starts the installed Claude Code as a subscription session does, with a launch prepared
    /// by the project service (STUDIO_TEST_LAUNCH), while this process carries a provider
    /// endpoint and key that must not reach it: `cargo test -- --ignored subscription_session`.
    #[test]
    #[ignore]
    fn subscription_session_uses_the_login() {
        assert!(
            std::env::var("ANTHROPIC_BASE_URL").is_ok(),
            "run with a dead ANTHROPIC_BASE_URL"
        );
        let path =
            std::env::var("STUDIO_TEST_LAUNCH").expect("STUDIO_TEST_LAUNCH names a launch.json");
        let launch: serde_json::Value =
            serde_json::from_slice(&std::fs::read(path).unwrap()).unwrap();
        let program = find(Path::new("/nonexistent")).expect("Claude Code is installed");
        let mut command = local_command(&program, None);
        command.current_dir(launch["cwd"].as_str().unwrap());
        command.args(
            launch["args"]
                .as_array()
                .unwrap()
                .iter()
                .map(|a| a.as_str().unwrap()),
        );
        command.args(["-p", "Reply with the single word READY."]);
        let output = run(command, Duration::from_secs(180)).unwrap();
        let reply = String::from_utf8_lossy(&output.stdout);
        assert!(
            output.status.success() && reply.contains("READY"),
            "{reply}\n{}",
            String::from_utf8_lossy(&output.stderr)
        );
        println!("Claude Code replied: {}", reply.trim());
    }
}
