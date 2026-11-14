// Private stdio bridge: the WebView receives no shell, filesystem or credentials.
use serde_json::{json, Value};
use std::{
    collections::HashMap,
    io::{BufRead, BufReader, Write},
    path::{Path, PathBuf},
    process::{Child, ChildStdin, Command, Stdio},
    sync::{
        atomic::{AtomicU64, Ordering},
        Arc, Mutex, OnceLock,
    },
    time::Duration,
};
use tauri::{Emitter, Manager};
use tokio::sync::oneshot;

type Reply = Result<Value, Value>;
const QUEUED_UI_METHODS: &[&str] = &[
    "tasks.ui.pending", "tasks.ui.claim", "tasks.ui.complete", "tasks.ui.check",
    "tasks.ui.start", "tasks.ui.settled", "tasks.ui.orphaned",
];
fn backend_method_allowed(method: &str, ordinary_methods: &[&str]) -> bool {
    ordinary_methods.contains(&method) || QUEUED_UI_METHODS.contains(&method)
}
fn backend_pending_limit(method: &str) -> usize {
    if matches!(method, "control" | "managed_chat.cancel" | "embedding.cancel" | "helper.text.complete" | "workbench.bootstrap")
        || QUEUED_UI_METHODS.contains(&method) { 32 } else { 28 }
}

#[cfg(test)]
mod queued_ui_admission_tests {
    use super::*;
    use std::collections::BTreeSet;

    #[test]
    fn actual_queued_ui_consumer_methods_are_admitted_with_reserved_capacity() {
        let source = include_str!("../../workbench/src/modules/ai/components/QueuedTaskApprovals.tsx");
        let consumer_methods: BTreeSet<_> = source.split("method:")
            .filter_map(|tail| tail.trim_start().strip_prefix('"'))
            .filter_map(|tail| tail.split_once('"').map(|(method, _)| method))
            .filter(|method| method.starts_with("tasks.ui."))
            .collect();
        assert_eq!(consumer_methods, QUEUED_UI_METHODS.iter().copied().collect());
        for method in consumer_methods {
            assert!(backend_method_allowed(method, &[]), "not admitted: {method}");
            assert_eq!(backend_pending_limit(method), 32, "no reserved capacity: {method}");
        }
    }

    #[test]
    fn queued_ui_admission_is_exact_and_separate_from_ordinary_methods() {
        for method in ["tasks.ui", "tasks.ui.unknown", "tasks.ui.start.extra", "tasks.ui.read", "tasks.ui.cancel", "tasks.ui.Start"] {
            assert!(!backend_method_allowed(method, &[]), "unexpected admission: {method}");
            assert_eq!(backend_pending_limit(method), 28);
        }
        assert!(backend_method_allowed("services.read", &["services.read"]));
        assert_eq!(backend_pending_limit("services.read"), 28);
    }
}
fn backend_disconnected_error() -> Value {
    json!({"source":"jarvis","message":"Python connection was interrupted. Restart Jarvis.","values":[]})
}

fn backend_stopped_event() -> Value {
    json!({"event":"backend.error","data":{"text":{"source":"jarvis","message":"Python backend stopped. Check data/logs/desktop-backend.log.","values":[]}}})
}

#[cfg(test)]
mod disconnect_event_tests {
    use super::*;

    #[test]
    fn disconnect_messages_retain_owned_provenance_through_json() {
        let event: Value = serde_json::from_str(&backend_stopped_event().to_string()).unwrap();
        assert_eq!(event["event"], "backend.error");
        assert_eq!(event["data"]["text"], json!({
            "source": "jarvis",
            "message": "Python backend stopped. Check data/logs/desktop-backend.log.",
            "values": []
        }));
        assert_eq!(backend_disconnected_error(), json!({
            "source": "jarvis",
            "message": "Python connection was interrupted. Restart Jarvis.",
            "values": []
        }));
    }
}
#[derive(Clone)]
pub struct Bridge {
    pub inner: Arc<Inner>,
}
pub struct Inner {
    #[cfg(windows)]
    _job: crate::job::ProcessJob,
    input: Mutex<Option<ChildStdin>>,
    child: Mutex<Child>,
    pending: Mutex<HashMap<u64, oneshot::Sender<Reply>>>,
    sequence: AtomicU64,
}

static REPOSITORY: OnceLock<PathBuf> = OnceLock::new();

#[cfg(test)]
mod root_tests {
    use super::*;


    fn installation(parent: &Path, name: &str) -> PathBuf {
        let root = parent.join(name);
        std::fs::create_dir_all(root.join("local_mode")).unwrap();
        std::fs::create_dir_all(root.join(".venv/Scripts")).unwrap();
        std::fs::write(root.join("local_mode/desktop_host.py"), "").unwrap();
        std::fs::write(root.join(".venv/Scripts/python.exe"), "").unwrap();
        root
    }

    #[test]
    fn portable_resource_parent_works_from_unrelated_cwd() {
        let temp = tempfile::tempdir().unwrap();
        let root = installation(temp.path(), "portable");
        let resource = root.join("desktop-bin");
        std::fs::create_dir(&resource).unwrap();
        let elsewhere = temp.path().join("elsewhere");
        std::fs::create_dir(&elsewhere).unwrap();
        assert_eq!(discover_repository(None, Some(resource), Some(elsewhere)).unwrap(), root.canonicalize().unwrap());
    }

    #[test]
    fn invalid_explicit_root_never_falls_back_to_other_installation() {
        let temp = tempfile::tempdir().unwrap();
        let root = installation(temp.path(), "valid");
        assert!(discover_repository(Some(temp.path().join("missing")), Some(root.clone()), Some(root)).unwrap_err().contains("JARVIS_ROOT"));
        assert!(discover_repository(Some(PathBuf::from("relative-installation")), None, None).unwrap_err().contains("absolute"));
    }

    #[test]
    fn arbitrary_working_directory_ancestors_are_not_searched() {
        let temp = tempfile::tempdir().unwrap();
        let root = installation(temp.path(), "installation");
        let nested = root.join("unrelated/nested");
        std::fs::create_dir_all(&nested).unwrap();
        assert!(discover_repository(None, None, Some(nested.clone())).is_err());
        assert!(discover_repository(None, Some(nested), None).is_err());
        assert!(discover_repository(None, None, Some(root.clone())).is_err());
        let fallback = discover_repository(None, Some(temp.path().join("missing-resource")), Some(root.clone()));
        if cfg!(debug_assertions) { assert_eq!(fallback.unwrap(), root.canonicalize().unwrap()); }
        else { assert!(fallback.unwrap_err().contains("resource directory does not identify")); }
    }

    #[test]
    fn explicit_root_is_canonical_and_pinned_for_attachment_reads() {
        let temp = tempfile::tempdir().unwrap();
        let first = installation(temp.path(), "first");
        let second = installation(temp.path(), "second");
        let chosen = discover_repository(Some(first.join("local_mode/..")), Some(second.clone()), Some(second.clone())).unwrap();
        assert_eq!(chosen, first.canonicalize().unwrap());
        let slot = OnceLock::new();
        assert_eq!(pin_repository(&slot, chosen.clone()), chosen);
        assert_eq!(pin_repository(&slot, second), chosen);
        assert_eq!(slot.get(), Some(&chosen));
    }

    #[test]
    fn missing_resource_requires_valid_explicit_override_even_from_valid_installation() {
        let temp = tempfile::tempdir().unwrap();
        let root = installation(temp.path(), "valid-working-directory");
        let error = discover_repository(None, None, Some(root.clone())).unwrap_err();
        assert!(error.contains("resource directory is unavailable"));
        assert_eq!(discover_repository(Some(root.clone()), None, Some(root.clone())).unwrap(), root.canonicalize().unwrap());
        assert!(discover_repository(Some(temp.path().join("missing")), None, Some(root)).is_err());
    }

    #[test]
    fn resource_root_is_valid_without_using_the_working_directory() {
        let temp = tempfile::tempdir().unwrap();
        let trusted = installation(temp.path(), "trusted-resource");
        let unrelated = installation(temp.path(), "caller-controlled");
        assert_eq!(discover_repository(None, Some(trusted.clone()), Some(unrelated)).unwrap(), trusted.canonicalize().unwrap());
    }

    #[test]
    fn developer_fallback_is_limited_to_known_project_folders() {
        let temp = tempfile::tempdir().unwrap();
        let root = installation(temp.path(), "development");
        for suffix in ["apps/desktop", "apps/desktop/src-tauri"] {
            let cwd = root.join(suffix);
            std::fs::create_dir_all(&cwd).unwrap();
            let result = discover_repository(None, Some(temp.path().join("missing-resource")), Some(cwd));
            if cfg!(debug_assertions) {
                assert_eq!(result.unwrap(), root.canonicalize().unwrap());
            } else {
                assert!(result.is_err());
            }
        }
    }
}

fn validated_root(path: &Path) -> Option<PathBuf> {
    let root = path.canonicalize().ok()?;
    (root.join("local_mode/desktop_host.py").is_file()
        && root.join(".venv/Scripts/python.exe").is_file())
        .then_some(root)
}

fn discover_repository(
    explicit: Option<PathBuf>,
    resource: Option<PathBuf>,
    cwd: Option<PathBuf>,
) -> Result<PathBuf, String> {
    if let Some(path) = explicit {
        if !path.is_absolute() {
            return Err("JARVIS_ROOT must be an absolute installation path".into());
        }
        return validated_root(&path)
            .ok_or_else(|| "JARVIS_ROOT does not identify a valid Jarvis installation".into());
    }
    // Resource discovery failure must not reinterpret an arbitrary launch cwd
    // as the trusted installation. A validated explicit override above is the
    // only recovery path when the platform cannot provide its resource root.
    if resource.is_none() {
        return Err("Jarvis resource directory is unavailable; set a valid absolute JARVIS_ROOT".into());
    }
    let mut candidates = Vec::new();
    if let Some(resource) = resource {
        candidates.push(resource.clone());
        if let Some(parent) = resource.parent() {
            candidates.push(parent.to_path_buf());
        }
    }
    if let Some(root) = candidates.iter().find_map(|path| validated_root(path)) {
        return Ok(root);
    }
    // Release builds trust only the resource directory or its immediate parent.
    // An invalid platform resource path must not promote caller-controlled cwd.
    if !cfg!(debug_assertions) {
        return Err("Jarvis resource directory does not identify a valid installation; set JARVIS_ROOT".into());
    }
    candidates.clear();
    if let Some(cwd) = cwd {
        candidates.push(cwd.clone());
        // cargo/tauri development starts from these two known project folders.
        // Never search arbitrary working-directory ancestors in a portable app.
        if cfg!(debug_assertions) {
            for suffix in [Path::new("apps/desktop"), Path::new("apps/desktop/src-tauri")] {
                if cwd.ends_with(suffix) {
                    let mut root = cwd.clone();
                    for _ in suffix.components() {
                        root.pop();
                    }
                    candidates.push(root);
                }
            }
        }
    }
    candidates.into_iter().find_map(|path| validated_root(&path))
        .ok_or_else(|| "Installed Jarvis Python components were not found. Run INSTALL.cmd.".into())
}

fn pin_repository(slot: &OnceLock<PathBuf>, root: PathBuf) -> PathBuf {
    slot.get_or_init(|| root).clone()
}

pub(crate) fn repository() -> Result<PathBuf, String> {
    REPOSITORY.get().cloned().ok_or_else(|| "Jarvis backend root is not initialized".into())
}

pub(crate) fn initialize_repository(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    initialize_repository_from_resource(app.path().resource_dir().ok())
}

pub(crate) fn initialize_repository_from_resource(resource: Option<PathBuf>) -> Result<PathBuf, String> {
    match REPOSITORY.get() {
        Some(root) => Ok(root.clone()),
        None => Ok(pin_repository(&REPOSITORY, discover_repository(
            std::env::var_os("JARVIS_ROOT").map(PathBuf::from),
            resource,
            std::env::current_dir().ok(),
        )?)),
    }
}

impl Bridge {
    pub fn spawn(app: &tauri::AppHandle) -> Result<Self, String> {
        let root = initialize_repository(app)?;
        let logs = root.join("data/logs");
        std::fs::create_dir_all(&logs).map_err(|e| e.to_string())?;
        let path = logs.join("desktop-backend.log");
        if std::fs::metadata(&path)
            .map(|m| m.len() > 2_000_000)
            .unwrap_or(false)
        {
            let _ = std::fs::remove_file(&path);
        }
        let log = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(path)
            .map_err(|e| e.to_string())?;
        let mut command = Command::new(root.join(".venv/Scripts/python.exe"));
        command
            .args(["-u", "-X", "utf8", "-m", "local_mode.desktop_host"])
            .current_dir(&root)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::from(log))
            .env("PYTHONIOENCODING", "utf-8");
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000);
        }
        let mut child = command
            .spawn()
            .map_err(|_| "Could not start the Python backend.".to_string())?;
        #[cfg(windows)]
        let job = crate::job::ProcessJob::create_for(child.id()).map_err(|e| e.to_string())?;
        let input = child.stdin.take();
        let output = child.stdout.take().ok_or("Python output channel is unavailable.")?;
        let bridge = Self {
            inner: Arc::new(Inner {
                #[cfg(windows)]
                _job: job,
                input: Mutex::new(input),
                child: Mutex::new(child),
                pending: Mutex::new(HashMap::new()),
                sequence: AtomicU64::new(1),
            }),
        };
        let state = bridge.clone();
        let handle = app.clone();
        std::thread::spawn(move || {
            let mut reader = BufReader::new(output);
            loop {
                let mut bytes = Vec::new();
                let read =
                    std::io::Read::take(&mut reader, 1_000_001).read_until(b'\n', &mut bytes);
                if !matches!(read,Ok(n) if n>0) || bytes.len() > 1_000_000 {
                    break;
                }
                let Ok(line) = String::from_utf8(bytes) else {
                    continue;
                };
                let Ok(value) = serde_json::from_str::<Value>(&line) else {
                    continue;
                };
                if value.get("event").is_some() {
                    let _ = handle.emit("backend-event", &value);
                } else if let Some(id) = value.get("id").and_then(Value::as_u64) {
                    if let Some(sender) = state.inner.pending.lock().unwrap().remove(&id) {
                        let response = match value.get("error") {
                            Some(error) => {
                                Err(error.clone())
                            }
                            None => Ok(value.get("result").cloned().unwrap_or(Value::Null)),
                        };
                        let _ = sender.send(response);
                    }
                }
            }
            let pending = std::mem::take(&mut *state.inner.pending.lock().unwrap());
            for (_, sender) in pending {
                let _ = sender.send(Err(backend_disconnected_error()));
            }
            let _=handle.emit("backend-event", backend_stopped_event());
        });
        Ok(bridge)
    }
    pub async fn request(&self, method: String, params: Value) -> Reply {
        const METHODS: &[&str] = &[
            "documents.import", "documents.read", "documents.search", "documents.embed", "documents.rebuild", "documents.remove", "documents.refresh",
            "llama.status", "llama.start", "llama.stop",
            "managed_chat.list", "managed_chat.resolve", "managed_chat.cancel", "managed_chat.unload",
            "models.activate",
            "services.read", "services.catalog", "services.register_workspace", "conversation.sync", "agent.activity", "agent.ui_speech", "agent.detach", "agent.attach", "agent.complete", "agent.catalog", "agent.prepare", "agent.execute", "helper.text.complete",
            "voice.activation",
            "translation.settings",
            "translation.run",
            "embedding.settings",
            "embedding.cancel",
            "api.embed",
            "api.usage",
            "api.catalog",
            "snapshot",
            "screen.capture",
            "chat",
            "control",
            "history.clear",
            "suggestions.get",
            "suggestions.catalog",
            "suggestions.save",
            "browser.profiles",
            "browser.save",
            "workbench.bootstrap",
            "speech.recognize",
            "chrome.token",
            "voices",
            "settings.key",
            "settings.microphone",
            "settings.locale",
            "audio.devices",
            "settings.voice",
            "network.check",
            "camera.list",
            "camera.allow",
            "camera.command",
            "capabilities",
            "settings.models",
            "settings.network",
            "browser.action",
            "tool",
        ];
        if !backend_method_allowed(&method, METHODS) {
            return Err(json!({"source":"jarvis","message":"Unsupported Jarvis method: {0}","values":[method]}));
        }
        let cancel_request = if method == "managed_chat.resolve" {
            Some("managed_chat.cancel")
        } else if matches!(method.as_str(), "documents.rebuild" | "documents.embed" | "api.embed") {
            Some("embedding.cancel")
        } else { None }.and_then(|cancel_method| params.get("request_id").and_then(Value::as_str).map(|id| (cancel_method, id.to_owned())));
        let id = self.inner.sequence.fetch_add(1, Ordering::Relaxed);
        let message =
            json!({"version":1,"id":id,"method":method,"params":params}).to_string() + "\n";
        if message.len() > 65536 {
            return Err("Request is too large.".into());
        }
        let (sender, receiver) = oneshot::channel();
        {
            let mut pending = self.inner.pending.lock().unwrap();
            if pending.len() >= backend_pending_limit(&method) {
                return Err("The application is busy.".into());
            }
            pending.insert(id, sender);
        }
        let written = {
            let mut input = self.inner.input.lock().unwrap();
            match input.as_mut() {
                Some(stream) => stream
                    .write_all(message.as_bytes())
                    .and_then(|_| stream.flush()),
                None => Err(std::io::Error::new(
                    std::io::ErrorKind::BrokenPipe,
                    "Closed",
                )),
            }
        };
        if written.is_err() {
            self.inner.pending.lock().unwrap().remove(&id);
            return Err("Backend is unavailable.".into());
        }
        // First local embedding can download verified weights on a slow connection.
        let wait_seconds = if method == "api.embed" || method == "translation.run" || method == "llama.start" || method == "managed_chat.resolve" || method == "documents.import" || method == "documents.embed" || method == "documents.rebuild" { 900 } else { 190 };
        match tokio::time::timeout(Duration::from_secs(wait_seconds), receiver).await {
            Ok(Ok(reply)) => reply,
            _ => {
                self.inner.pending.lock().unwrap().remove(&id);
                if let Some((cancel_method, request_id)) = cancel_request {
                    let cancel_id = self.inner.sequence.fetch_add(1, Ordering::Relaxed);
                    let cancel = json!({"version":1,"id":cancel_id,"method":cancel_method,"params":{"request_id":request_id}}).to_string() + "\n";
                    if let Some(stream) = self.inner.input.lock().unwrap().as_mut() {
                        let _ = stream.write_all(cancel.as_bytes()).and_then(|_| stream.flush());
                    }
                }
                Err("Backend request timed out.".into())
            }
        }
    }
    pub fn shutdown(&self) {
        self.inner.input.lock().unwrap().take();
        if let Ok(mut child) = self.inner.child.lock() {
            for _ in 0..150 {
                if child.try_wait().ok().flatten().is_some() {
                    return;
                }
                std::thread::sleep(Duration::from_millis(100));
            }
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}
