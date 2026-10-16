//! Provider keys stay in the operating system's credential store. The WebView
//! can submit a replacement and read presence flags, but cannot retrieve keys.
//! Development builds keep the keys in a `.env` file at the repository root
//! instead: a rebuilt debug binary is not authorized automatically, so the
//! system would otherwise ask for store access again after every rebuild.
use std::{
    collections::BTreeMap,
    sync::{
        atomic::{AtomicBool, Ordering},
        Mutex,
    },
    time::Duration,
};

#[cfg(any(not(debug_assertions), test))]
use keyring::Entry;
#[cfg(not(debug_assertions))]
use keyring::Error;
use serde::Serialize;
use tauri::Manager;

#[cfg(any(not(debug_assertions), test))]
const SERVICE: &str = "com.book2video.studio.provider-keys";
pub const PROVIDERS: [&str; 8] = [
    "OPENROUTER_API_KEY",
    "DEEPSEEK_API_KEY",
    "MIMO_API_KEY",
    "KIMI_API_KEY",
    "SILICONFLOW_KEY",
    "VOLCENGINE_API_KEY",
    "GOOGLE_CLOUD_API_KEY",
    "BYTEDANCE_TTS_API_KEY",
];

#[derive(Serialize)]
pub struct Status {
    saved: BTreeMap<String, bool>,
    applied: bool,
}

/// All keys live in one item, so macOS asks for access once instead of once per provider.
#[cfg(not(debug_assertions))]
const VAULT: &str = "provider-keys";
/// Read the store once per launch; every later lookup is served from memory.
static CACHE: Mutex<Option<BTreeMap<String, String>>> = Mutex::new(None);
/// The service started without the keys because the store could not be read then.
static DEFERRED: AtomicBool = AtomicBool::new(false);

pub fn defer_apply() {
    DEFERRED.store(true, Ordering::SeqCst);
}

fn supported(name: &str) -> Result<(), String> {
    if PROVIDERS.contains(&name) {
        Ok(())
    } else {
        Err("Choose a supported API provider.".into())
    }
}

#[cfg(not(debug_assertions))]
fn vault() -> Result<Entry, String> {
    Entry::new(SERVICE, VAULT).map_err(|_| "Cannot access the system credential store.".into())
}

#[cfg(not(debug_assertions))]
const READ_ERROR: &str =
    "Cannot read saved API keys. Unlock the system credential store and retry.";

/// Where debug builds keep the keys: `<repository>/.env`, beside the source.
#[cfg(debug_assertions)]
fn env_file() -> std::path::PathBuf {
    std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../.env")
}

/// Read the development `.env` file (`KEY=value` lines, `#` comments); without one, no keys are saved.
#[cfg(debug_assertions)]
fn read_env_file() -> Result<BTreeMap<String, String>, String> {
    let path = env_file();
    if !path.is_file() {
        return Ok(BTreeMap::new());
    }
    let text = std::fs::read_to_string(&path)
        .map_err(|_| "Cannot read the .env file with the saved API keys.".to_string())?;
    let mut keys = BTreeMap::new();
    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        if let Some((name, value)) = line.split_once('=') {
            let (name, value) = (name.trim(), value.trim());
            if PROVIDERS.contains(&name) && !value.is_empty() {
                keys.insert(name.to_string(), value.to_string());
            }
        }
    }
    Ok(keys)
}

/// Write the development `.env` file, private to the current user.
#[cfg(debug_assertions)]
fn write_env_file(keys: &BTreeMap<String, String>) -> Result<(), String> {
    let mut text =
        String::from("# Development API keys for Book2Video Studio (debug builds only).\n");
    for (name, value) in keys {
        text.push_str(name);
        text.push('=');
        text.push_str(value);
        text.push('\n');
    }
    std::fs::write(env_file(), text)
        .map_err(|_| "Cannot save the API key to the development .env file.".to_string())?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(env_file(), std::fs::Permissions::from_mode(0o600))
            .map_err(|_| "Cannot restrict the development .env file.".to_string())?;
    }
    Ok(())
}

#[cfg(debug_assertions)]
fn write(keys: &BTreeMap<String, String>) -> Result<(), String> {
    write_env_file(keys)
}

#[cfg(not(debug_assertions))]
fn write(keys: &BTreeMap<String, String>) -> Result<(), String> {
    vault()?
        .set_password(&serde_json::to_string(keys).unwrap())
        .map_err(|_| "Cannot save the API key in the system credential store.".into())
}

pub fn load() -> Result<BTreeMap<String, String>, String> {
    let mut cache = CACHE.lock().unwrap();
    if let Some(keys) = cache.as_ref() {
        return Ok(keys.clone());
    }
    #[cfg(debug_assertions)]
    let keys = read_env_file()?;
    #[cfg(not(debug_assertions))]
    let keys = match vault()?.get_password() {
        Ok(json) => serde_json::from_str(&json).map_err(|_| READ_ERROR.to_string())?,
        Err(Error::NoEntry) => BTreeMap::new(),
        Err(_) => return Err(READ_ERROR.into()),
    };
    *cache = Some(keys.clone());
    Ok(keys)
}

fn status(keys: &BTreeMap<String, String>, applied: bool) -> Status {
    Status {
        saved: PROVIDERS
            .iter()
            .map(|name| (name.to_string(), keys.contains_key(*name)))
            .collect(),
        applied,
    }
}

pub fn apply(info: &crate::Bootstrap, keys: &BTreeMap<String, String>) -> Result<(), String> {
    // Only loopback is contacted; environment proxies must not receive keys.
    let agent: ureq::Agent = ureq::Agent::config_builder()
        .proxy(None)
        .timeout_global(Some(Duration::from_secs(5)))
        .build()
        .into();
    agent
        .post(format!("http://127.0.0.1:{}/api/credentials", info.port))
        .header("Authorization", format!("Bearer {}", info.token))
        .send_json(keys)
        .map_err(|_| {
            "Cannot apply API keys to the production service. Restart Book2Video Studio."
                .to_string()
        })?;
    Ok(())
}

#[tauri::command]
pub async fn credential_status(app: tauri::AppHandle) -> Result<Status, String> {
    crate::off_main_thread(move || {
        let runtime = app.state::<crate::Runtime>();
        let _startup = runtime.startup.lock().unwrap();
        let keys = load()?;
        let info = runtime.info.lock().unwrap().clone();
        // Keys that could not be read when the service started reach it once they can be.
        if let Some(info) = info.as_ref().filter(|_| DEFERRED.load(Ordering::SeqCst)) {
            apply(info, &keys)?;
            DEFERRED.store(false, Ordering::SeqCst);
        }
        Ok(status(&keys, info.is_some()))
    })
    .await
}

#[tauri::command]
pub async fn credential_save(
    app: tauri::AppHandle,
    name: String,
    value: Option<String>,
) -> Result<Status, String> {
    crate::off_main_thread(move || save(&app, name, value)).await
}

fn save(app: &tauri::AppHandle, name: String, value: Option<String>) -> Result<Status, String> {
    let runtime = app.state::<crate::Runtime>();
    let _startup = runtime.startup.lock().unwrap();
    supported(&name)?;
    let mut keys = load()?;
    match value {
        Some(value) => {
            let value = value.trim();
            if value.is_empty() || value.len() > 2400 || value.chars().any(char::is_control) {
                return Err("Enter a valid API key (up to 2,400 characters).".into());
            }
            // A pasted line can carry a comment or label; providers would only answer "invalid key" later.
            if !value.is_ascii() || value.chars().any(char::is_whitespace) {
                return Err(
                    "An API key has no spaces or other text. Paste only the key itself.".into(),
                );
            }
            keys.insert(name, value.into());
        }
        None => {
            keys.remove(&name);
        }
    }
    write(&keys)?;
    *CACHE.lock().unwrap() = Some(keys.clone());
    let applied = runtime
        .info
        .lock()
        .unwrap()
        .as_ref()
        .is_some_and(|info| apply(info, &keys).is_ok());
    if applied {
        DEFERRED.store(false, Ordering::SeqCst);
    }
    Ok(status(&keys, applied))
}

#[cfg(test)]
mod tests {
    use super::*;
    use keyring::Error;

    #[test]
    fn status_never_contains_secret_values() {
        let keys = BTreeMap::from([("DEEPSEEK_API_KEY".into(), "test-only-secret".into())]);
        let json = serde_json::to_string(&status(&keys, true)).unwrap();
        assert!(!json.contains("test-only-secret"));
        assert!(json.contains("\"DEEPSEEK_API_KEY\":true"));
        assert!(json.contains("\"MIMO_API_KEY\":false"));
    }

    #[test]
    fn unknown_provider_is_rejected_before_accessing_store() {
        assert!(supported("PATH").is_err());
    }

    #[test]
    #[ignore = "Writes a disposable key to the native OS credential store"]
    fn native_store_roundtrip() {
        let account = format!("test-{}", uuid::Uuid::new_v4());
        let entry = Entry::new(SERVICE, &account).unwrap();
        assert!(matches!(entry.get_password(), Err(Error::NoEntry)));
        entry.set_password("test-only-first").unwrap();
        assert_eq!(entry.get_password().unwrap(), "test-only-first");
        entry.set_password("test-only-replacement").unwrap();
        assert_eq!(entry.get_password().unwrap(), "test-only-replacement");
        entry.delete_credential().unwrap();
        assert!(matches!(entry.get_password(), Err(Error::NoEntry)));
    }
}
