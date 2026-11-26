use portable_pty::{native_pty_system, Child, CommandBuilder, MasterPty, PtySize};
use serde::{Deserialize, Serialize};
use std::{
    collections::HashMap,
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
};
use tauri::{Emitter, Manager};

struct Session {
    master: Box<dyn MasterPty + Send>,
    writer: Box<dyn Write + Send>,
    child: Box<dyn Child + Send + Sync>,
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
    agent: String,
    cwd: String,
    context: String,
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
        serde_json::from_slice(&std::fs::read(&manifest).map_err(|e| e.to_string())?)
            .map_err(|e| e.to_string())?;
    if trusted.id != launch.id || trusted.agent != launch.agent || trusted.cwd != launch.cwd || trusted.context != launch.context {
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
    // Claude remembers trust per project. Its task brief still names the edit or design directory.
    let project_cwd = strip_verbatim(&project_root);
    let manifest_path = strip_verbatim(&manifest);
    let cwd = if trusted.agent == "claude" { project_cwd.as_path() } else { Path::new(&trusted.cwd) };
    let command = session_command(&installation, &manifest_path, cwd)?;
    let child = pair.slave.spawn_command(command).map_err(|e| {
        format!("Cannot launch {}: {e}. Check it in Settings → Local Agents.", trusted.agent)
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
                    if let Some(data) = take_text(&mut pending) {
                        let _ = app.emit(
                            "terminal-output",
                            Output { id: thread_id.clone(), data, exited: false },
                        );
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
/// Strip Windows `\\?\` verbatim prefixes from canonicalized paths before passing them to child CLIs.
fn strip_verbatim(path: &Path) -> PathBuf {
    #[cfg(windows)]
    if let Some(text) = path.to_str() {
        if let Some(stripped) = text.strip_prefix(r"\\?\UNC\") {
            return PathBuf::from(format!(r"\\{stripped}"));
        }
        if let Some(stripped) = text.strip_prefix(r"\\?\") {
            return PathBuf::from(stripped);
        }
    }
    path.to_path_buf()
}

#[derive(Deserialize)]
struct AgentCommand {
    program: PathBuf,
    path: String,
    args: Vec<String>,
}

fn session_command(
    installation: &crate::Installation,
    manifest: &Path,
    cwd: &Path,
) -> Result<CommandBuilder, String> {
    let resolved = installation
        .command(&installation.python)
        .arg(installation.backend_script("local_agents.py"))
        .arg(manifest)
        .output()
        .map_err(|e| e.to_string())?;
    if !resolved.status.success() {
        return Err(String::from_utf8_lossy(&resolved.stderr).trim().into());
    }
    let agent: AgentCommand =
        serde_json::from_slice(&resolved.stdout).map_err(|e| e.to_string())?;
    let mut command = CommandBuilder::new(agent.program);
    command.args(&agent.args);
    command.cwd(cwd);
    command.env("TERM", "xterm-256color");
    for (name, value) in installation.environment() {
        command.env(name, value);
    }
    for name in crate::Installation::CLEARED {
        command.env_remove(name);
    }
    command.env("PATH", agent.path);
    command.env_remove("CLAUDECODE");
    Ok(command)
}

/// The complete UTF-8 text at the front of `pending`; a character split across reads waits
/// for its remaining bytes, and bytes that can never form one are replaced.
fn take_text(pending: &mut Vec<u8>) -> Option<String> {
    let valid = match std::str::from_utf8(pending) {
        Ok(_) => pending.len(),
        Err(e) if e.error_len().is_none() => e.valid_up_to(),
        Err(_) => pending.len(),
    };
    if valid == 0 {
        return None;
    }
    let text = String::from_utf8_lossy(&pending[..valid]).into_owned();
    pending.drain(..valid);
    Some(text)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        path::PathBuf,
        time::{Duration, Instant},
    };

    #[test]
    fn split_characters_wait_for_their_remaining_bytes() {
        let bytes = "写作 ok".as_bytes();
        let mut pending = bytes[..2].to_vec();
        assert_eq!(take_text(&mut pending), None);
        pending.extend_from_slice(&bytes[2..]);
        assert_eq!(take_text(&mut pending).as_deref(), Some("写作 ok"));
        assert!(pending.is_empty());
        let mut broken = vec![b'a', 0xff, b'b'];
        assert_eq!(take_text(&mut broken).as_deref(), Some("a\u{fffd}b"));
    }

    /// Screen text without terminal control sequences.
    fn plain(raw: &str) -> String {
        let (mut out, mut chars) = (String::new(), raw.chars().peekable());
        while let Some(c) = chars.next() {
            if c != '\x1b' {
                out.push(c);
                continue;
            }
            match chars.next() {
                Some('[') => while chars.next().is_some_and(|c| !('@'..='~').contains(&c)) {},
                Some(']') | Some('P') => while chars.next().is_some_and(|c| c != '\x07' && c != '\x1b') {},
                _ => {}
            }
        }
        out
    }

    /// Talks to the selected local CLI exactly as an app session does: a launch prepared by the project
    /// service (STUDIO_TEST_LAUNCH; its data folder is STUDIO_TEST_DATA, by default the
    /// development one), the staged build, the mode's program and connection, a real
    /// terminal. Run with cargo test -- --ignored --nocapture interactive_session.
    #[test]
    #[ignore]
    fn interactive_session_answers_through_the_terminal() {
        let path = std::env::var("STUDIO_TEST_LAUNCH").expect("STUDIO_TEST_LAUNCH names a launch.json");
        let launch: Launch = serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
        let build = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../build");
        // The data folder of the service that prepared the launch (its library holds the prompts).
        let data = std::env::var("STUDIO_TEST_DATA").map(PathBuf::from).unwrap_or_else(|_| build.join("dev-data"));
        let installation = crate::Installation::new(&build, data.clone(), data.join("projects")).unwrap();
        let manifest = Path::new(&path);
        let cwd = if launch.agent == "claude" { manifest.ancestors().nth(4).unwrap() } else { Path::new(&launch.cwd) };
        let command = session_command(&installation, manifest, cwd).unwrap();
        let pair = native_pty_system()
            .openpty(PtySize { rows: 40, cols: 120, pixel_width: 0, pixel_height: 0 })
            .unwrap();
        let mut child = pair.slave.spawn_command(command).unwrap();
        drop(pair.slave);
        let mut reader = pair.master.try_clone_reader().unwrap();
        let mut writer = pair.master.take_writer().unwrap();
        let screen = Arc::new(Mutex::new(String::new()));
        let output = screen.clone();
        std::thread::spawn(move || {
            let (mut buffer, mut pending) = ([0u8; 8192], Vec::new());
            while let Ok(n) = reader.read(&mut buffer) {
                if n == 0 { break; }
                pending.extend_from_slice(&buffer[..n]);
                if let Some(text) = take_text(&mut pending) { output.lock().unwrap().push_str(&text); }
            }
        });
        let wait_for = |what: &[&str], seconds: u64| {
            let deadline = Instant::now() + Duration::from_secs(seconds);
            while Instant::now() < deadline {
                let text = plain(&screen.lock().unwrap()).replace(' ', "");
                if let Some(found) = what.iter().find(|w| text.contains(&w.replace(' ', ""))) { return Some(found.to_string()); }
                std::thread::sleep(Duration::from_millis(200));
            }
            None
        };
        let mut send = |keys: &str| { writer.write_all(keys.as_bytes()).unwrap(); writer.flush().unwrap(); };
        if launch.agent == "agy" {
            if wait_for(&["Do you trust the contents of this project?"], 20).is_some() {
                // The test fixture is a disposable, empty workspace.
                send("\r");
            }
        } else {
            assert!(wait_for(&["shift+tab", "for shortcuts"], 60).is_some(), "{}", plain(&screen.lock().unwrap()));
            send("What is 17+25? Reply with only the number.");
            std::thread::sleep(Duration::from_millis(800));
            send("\r");
        }
        let expected = std::env::var("STUDIO_TEST_EXPECT").unwrap_or_else(|_| "42".into());
        let answered = wait_for(&[&expected], 120);
        let transcript = plain(&screen.lock().unwrap());
        let _ = child.kill();
        let _ = child.wait();
        assert!(answered.is_some(), "No answer through the terminal:\n{transcript}");
        println!("Answered through the terminal: {expected}");
    }
}
