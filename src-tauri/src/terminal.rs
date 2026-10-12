use portable_pty::{native_pty_system, Child, CommandBuilder, MasterPty, PtySize};
use serde::{Deserialize, Serialize};
use std::{
    collections::HashMap,
    io::{Read, Write},
    sync::{Arc, Mutex},
};
use tauri::{Emitter, Manager};

struct Session {
    master: Box<dyn MasterPty + Send>,
    writer: Box<dyn Write + Send>,
    child: Box<dyn Child + Send + Sync>,
    project: String,
}
#[derive(Default)]
pub struct Terminals {
    sessions: Arc<Mutex<HashMap<String, Session>>>,
}
impl Terminals {
    pub fn stop_all(&self) {
        for (_, mut session) in self.sessions.lock().unwrap().drain() {
            let _ = session.child.kill();
            let _ = session.child.wait();
        }
    }
}
#[derive(Deserialize)]
pub struct Launch {
    id: String,
    project: String,
    step: String,
    cwd: String,
    context: String,
    args: Vec<String>,
}
#[derive(Clone, Serialize)]
struct Output {
    id: String,
    data: String,
    exited: bool,
}

#[tauri::command]
pub async fn terminal_start(
    app: tauri::AppHandle,
    launch: Launch,
    cols: u16,
    rows: u16,
) -> Result<String, String> {
    crate::off_main_thread(move || start(app, launch, cols, rows)).await
}

fn start(app: tauri::AppHandle, launch: Launch, cols: u16, rows: u16) -> Result<String, String> {
    let id = launch.id.clone();
    let sessions = app.state::<Terminals>().sessions.clone();
    if sessions.lock().unwrap().contains_key(&id) {
        return Ok(id);
    }
    // Only launch manifests prepared by the local project service are accepted.
    let runtime = app
        .state::<crate::Runtime>()
        .info
        .lock()
        .unwrap()
        .clone()
        .ok_or("Start the project service first")?;
    let context = std::path::PathBuf::from(&launch.context)
        .canonicalize()
        .map_err(|e| e.to_string())?;
    if context.file_name().and_then(|s| s.to_str()) != Some("context.md") {
        return Err("Invalid agent context".into());
    }
    let manifest = context.parent().unwrap().join("launch.json");
    let trusted: Launch =
        serde_json::from_slice(&std::fs::read(manifest).map_err(|e| e.to_string())?)
            .map_err(|e| e.to_string())?;
    if trusted.id != launch.id || trusted.args != launch.args || trusted.cwd != launch.cwd {
        return Err("Agent launch context changed".into());
    }
    // The project can be anywhere the user created it; the service says which folders are
    // projects, and the context must be one of that project's own agent sessions.
    let project_root = crate::project_root(&runtime, &trusted.project)?;
    if !context.starts_with(project_root.join(".studio").join("agent")) {
        return Err("Invalid agent context".into());
    }
    let pair = native_pty_system()
        .openpty(PtySize {
            rows: rows.max(5),
            cols: cols.max(20),
            pixel_width: 0,
            pixel_height: 0,
        })
        .map_err(|e| e.to_string())?;
    let installation = crate::installation(&app)?;
    let mode = crate::claude_code::mode(&app);
    let program = match mode {
        crate::claude_code::Mode::ApiKey => installation.claude.clone(),
        crate::claude_code::Mode::Subscription => {
            crate::claude_code::local_program(&app, &installation)?
        }
    };
    let mut command = CommandBuilder::new(&program);
    command.args(&trusted.args);
    command.cwd(&trusted.cwd);
    command.env("TERM", "xterm-256color");
    for (name, value) in installation.environment() {
        command.env(name, value);
    }
    for name in crate::Installation::CLEARED {
        command.env_remove(name);
    }
    if mode == crate::claude_code::Mode::Subscription {
        // The user's own login pays for the session; no provider endpoint or key may override it.
        for name in crate::claude_code::PROVIDER_ENV {
            command.env_remove(name);
        }
    } else {
        apply_provider(&mut command, &installation, &project_root, &trusted.step)?;
    }
    let child = pair.slave.spawn_command(command).map_err(|e| match mode {
        crate::claude_code::Mode::ApiKey => {
            format!("Cannot launch Claude Code: {e}. Reinstall Book2Video Studio.")
        }
        crate::claude_code::Mode::Subscription => format!(
            "Cannot launch your Claude Code ({}): {e}. Check it in Settings → API keys.",
            program.display()
        ),
    })?;
    drop(pair.slave);
    let mut reader = pair.master.try_clone_reader().map_err(|e| e.to_string())?;
    let writer = pair.master.take_writer().map_err(|e| e.to_string())?;
    sessions.lock().unwrap().insert(
        id.clone(),
        Session {
            master: pair.master,
            writer,
            child,
            project: trusted.project,
        },
    );
    let thread_id = id.clone();
    std::thread::spawn(move || {
        let mut buffer = [0u8; 8192];
        let mut pending = Vec::new();
        loop {
            match reader.read(&mut buffer) {
                Ok(0) | Err(_) => break,
                Ok(n) => {
                    pending.extend_from_slice(&buffer[..n]);
                    let valid = match std::str::from_utf8(&pending) {
                        Ok(_) => pending.len(),
                        Err(e) => e.valid_up_to(),
                    };
                    if valid > 0 {
                        let data = String::from_utf8_lossy(&pending[..valid]).into_owned();
                        let _ = app.emit(
                            "terminal-output",
                            Output {
                                id: thread_id.clone(),
                                data,
                                exited: false,
                            },
                        );
                        pending.drain(..valid);
                    }
                    if pending.len() > 4 {
                        let data = String::from_utf8_lossy(&pending).into_owned();
                        let _ = app.emit(
                            "terminal-output",
                            Output {
                                id: thread_id.clone(),
                                data,
                                exited: false,
                            },
                        );
                        pending.clear();
                    }
                }
            }
        }
        if let Some(mut s) = sessions.lock().unwrap().remove(&thread_id) {
            let _ = s.child.wait();
        }
        let _ = app.emit(
            "terminal-output",
            Output {
                id: thread_id,
                data: "\r\nSession ended. Outputs are ready to refresh.\r\n".into(),
                exited: true,
            },
        );
    });
    Ok(id)
}

#[tauri::command]
pub fn terminal_write(
    state: tauri::State<Terminals>,
    id: String,
    data: String,
) -> Result<(), String> {
    let mut map = state.sessions.lock().unwrap();
    let session = map.get_mut(&id).ok_or("This terminal session has ended")?;
    session
        .writer
        .write_all(data.as_bytes())
        .map_err(|e| e.to_string())?;
    session.writer.flush().map_err(|e| e.to_string())
}
#[tauri::command]
pub fn terminal_resize(
    state: tauri::State<Terminals>,
    id: String,
    cols: u16,
    rows: u16,
) -> Result<(), String> {
    state
        .sessions
        .lock()
        .unwrap()
        .get(&id)
        .ok_or("Session ended")?
        .master
        .resize(PtySize {
            cols: cols.max(20),
            rows: rows.max(5),
            pixel_width: 0,
            pixel_height: 0,
        })
        .map_err(|e| e.to_string())
}
#[tauri::command]
pub fn terminal_stop(state: tauri::State<Terminals>, id: String) -> Result<(), String> {
    if let Some(mut session) = state.sessions.lock().unwrap().remove(&id) {
        session.child.kill().map_err(|e| e.to_string())?;
        let _ = session.child.wait();
    }
    Ok(())
}
#[tauri::command]
pub fn terminal_list(state: tauri::State<Terminals>) -> Vec<serde_json::Value> {
    state
        .sessions
        .lock()
        .unwrap()
        .iter()
        .map(|(id, s)| serde_json::json!({"id":id,"project":s.project}))
        .collect()
}

/// Built-in mode: the engine resolves the step's provider endpoint and key. Credentials go
/// straight to the child and are never emitted to the WebView or written into a manifest.
fn apply_provider(
    command: &mut CommandBuilder,
    installation: &crate::Installation,
    project_root: &std::path::Path,
    step: &str,
) -> Result<(), String> {
    let resolved = installation
        .command(&installation.python)
        .arg(installation.backend_script("agent_env.py"))
        .arg(&installation.engine)
        .arg(project_root)
        .arg(step)
        .envs(crate::credentials::load()?)
        .output()
        .map_err(|e| e.to_string())?;
    if !resolved.status.success() {
        return Err(format!(
            "Cannot prepare the configured agent provider: {}",
            String::from_utf8_lossy(&resolved.stderr)
        ));
    }
    let env: HashMap<String, String> = serde_json::from_slice(&resolved.stdout)
        .map_err(|_| "The engine did not return a valid agent environment")?;
    for (name, value) in env {
        command.env(name, value);
    }
    Ok(())
}
