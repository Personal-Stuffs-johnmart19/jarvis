//! Curated WinGet installation, initiated only from Settings.
use super::{
    proc::{
        capture::{self, Limits},
        hide_console,
    },
    utilities,
};
use serde::Serialize;
use std::{
    collections::VecDeque,
    process::{Command, Stdio},
    sync::{Arc, Mutex, OnceLock},
    time::Duration,
};
static INSTALL: Mutex<()> = Mutex::new(());

// Match the existing frontend/Python owned-message payload. Runtime diagnostics
// remain plain strings, even when their text happens to match a catalog entry.
#[derive(Debug, Serialize)]
#[serde(untagged)]
pub enum InstallError {
    Owned {
        source: &'static str,
        message: &'static str,
        values: [String; 0],
    },
    Raw(String),
}
fn owned_error(message: &'static str) -> InstallError {
    InstallError::Owned {
        source: "jarvis",
        message,
        values: [],
    }
}
impl From<String> for InstallError {
    fn from(value: String) -> Self {
        Self::Raw(value)
    }
}

#[derive(Default)]
struct InstallRegistry {
    closed: bool,
    active: Option<(String, Arc<capture::CancelHandle>)>,
    early: VecDeque<String>,
}
static REGISTRY: OnceLock<Mutex<InstallRegistry>> = OnceLock::new();
fn registry() -> &'static Mutex<InstallRegistry> {
    REGISTRY.get_or_init(Default::default)
}
fn request_key(window: &str, id: &str) -> Result<String, InstallError> {
    if id.is_empty()
        || id.len() > 64
        || !id
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"_-".contains(&b))
    {
        return Err(owned_error("Invalid utility installation request ID"));
    }
    Ok(format!("{window}:{id}"))
}
struct InstallLease {
    key: String,
    cancel: Arc<capture::CancelHandle>,
}
impl InstallRegistry {
    fn register(&mut self, key: String) -> Result<InstallLease, InstallError> {
        if self.closed {
            return Err(owned_error("Utility installer is shutting down"));
        }
        if self.active.is_some() {
            return Err(owned_error("Another utility installation is running"));
        }
        let cancel = Arc::new(capture::CancelHandle::default());
        if let Some(index) = self.early.iter().position(|id| id == &key) {
            self.early.remove(index);
            cancel.cancel();
        }
        self.active = Some((key.clone(), cancel.clone()));
        Ok(InstallLease { key, cancel })
    }
    fn cancellation(&mut self, key: String) -> Option<Arc<capture::CancelHandle>> {
        if let Some((active, cancel)) = &self.active {
            if active == &key {
                return Some(cancel.clone());
            }
        }
        if !self.early.contains(&key) {
            if self.early.len() >= 64 {
                self.early.pop_front();
            }
            self.early.push_back(key);
        }
        None
    }
}
impl Drop for InstallLease {
    fn drop(&mut self) {
        let mut state = registry().lock().unwrap();
        if state
            .active
            .as_ref()
            .is_some_and(|(key, cancel)| key == &self.key && Arc::ptr_eq(cancel, &self.cancel))
        {
            state.active.take();
        }
    }
}
pub fn shutdown() {
    let cancel = {
        let mut state = registry().lock().unwrap();
        state.closed = true;
        state.active.as_ref().map(|(_, cancel)| cancel.clone())
    };
    if let Some(cancel) = cancel {
        cancel.cancel();
    }
}

#[tauri::command]
pub fn utility_install_cancel(
    window: tauri::WebviewWindow,
    request_id: String,
) -> Result<(), InstallError> {
    let key = request_key(window.label(), &request_id)?;
    let cancel = registry().lock().unwrap().cancellation(key);
    if let Some(cancel) = cancel {
        cancel.cancel();
    }
    Ok(())
}

pub fn package(id: &str) -> Option<&'static str> {
    Some(match id {
        "rg" => "BurntSushi.ripgrep.MSVC",
        "fd" => "sharkdp.fd",
        "jq" => "jqlang.jq",
        "yq" => "MikeFarah.yq",
        "git" | "bash" => "Git.Git",
        "gh" => "GitHub.cli",
        "python" => "Python.Python.3.13",
        "node" | "npm" => "OpenJS.NodeJS.LTS",
        "pnpm" => "pnpm.pnpm",
        "uv" => "astral-sh.uv",
        "cargo" | "rustc" => "Rustlang.Rustup",
        "go" => "GoLang.Go",
        "dotnet" => "Microsoft.DotNet.SDK.10",
        "java" | "javac" => "EclipseAdoptium.Temurin.21.JDK",
        "clang" => "LLVM.LLVM",
        "cmake" => "Kitware.CMake",
        "ninja" => "Ninja-build.Ninja",
        "curl" => "cURL.cURL",
        "7z" => "7zip.7zip",
        "ffmpeg" | "ffprobe" => "Gyan.FFmpeg",
        "magick" => "ImageMagick.ImageMagick",
        "tesseract" => "UB-Mannheim.TesseractOCR",
        "pdftotext" | "pdfinfo" | "pdftoppm" => "oschwartz10612.Poppler",
        "sqlite3" => "SQLite.SQLite",
        "pwsh" => "Microsoft.PowerShell",
        "pandoc" => "JohnMacFarlane.Pandoc",
        "soffice" => "TheDocumentFoundation.LibreOffice",
        _ => return None,
    })
}
pub fn supported() -> bool {
    cfg!(windows) && which::which("winget").is_ok()
}
fn arguments(
    id: &str,
    expected_package: &str,
    scope: &str,
    agreements: bool,
) -> Result<Vec<String>, InstallError> {
    let package =
        package(id).ok_or_else(|| owned_error("No managed installer for this utility"))?;
    if !agreements || expected_package != package {
        return Err(owned_error(
            "Review the package and accept its agreements before installation",
        ));
    }
    if !["user", "machine"].contains(&scope) {
        return Err(owned_error("Invalid installation scope"));
    }
    Ok([
        "install",
        "--id",
        package,
        "--exact",
        "--source",
        "winget",
        "--scope",
        scope,
        "--silent",
        "--disable-interactivity",
        "--accept-package-agreements",
        "--accept-source-agreements",
        "--no-upgrade",
    ]
    .into_iter()
    .map(str::to_owned)
    .collect())
}
fn require_installer(available: bool, qa_restricted: bool) -> Result<(), InstallError> {
    if !available {
        return Err(owned_error(
            "Installation requires Windows App Installer (WinGet)",
        ));
    }
    if qa_restricted {
        return Err(owned_error(
            "Package installation is disabled in ordinary QA",
        ));
    }
    Ok(())
}
#[derive(Serialize)]
pub struct InstallResult {
    package: String,
    scope: String,
    success: bool,
    available: bool,
    exit_code: Option<i32>,
    timed_out: bool,
    cancelled: bool,
    stdout: String,
    stderr: String,
}
fn capture_installer(
    cmd: &mut Command,
    cancel: &capture::CancelHandle,
) -> std::io::Result<capture::Captured> {
    capture::run_tree_cancellable(
        cmd,
        Duration::from_secs(900),
        Limits {
            stdout: 12000,
            stderr: 4000,
            stop_on_stdout_cap: false,
        },
        Some(cancel),
    )
}
#[tauri::command]
pub async fn utility_install(
    window: tauri::WebviewWindow,
    tool: String,
    expected_package: String,
    scope: String,
    agreements: bool,
    request_id: String,
) -> Result<InstallResult, InstallError> {
    let key = request_key(window.label(), &request_id)?;
    tauri::async_runtime::spawn_blocking(move || {
        let args = arguments(&tool, &expected_package, &scope, agreements)?;
        require_installer(
            supported(),
            std::env::var_os("JARVIS_DESKTOP_TEST").is_some()
                && std::env::var("JARVIS_QA_INSTALL").as_deref() != Ok("1"),
        )?;
        let _guard = INSTALL
            .try_lock()
            .map_err(|_| owned_error("Another utility installation is running"))?;
        let lease = registry().lock().unwrap().register(key)?;
        let mut cmd = Command::new(which::which("winget").map_err(|e| e.to_string())?);
        cmd.args(args)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        hide_console(&mut cmd);
        // Own only this WinGet process tree. Detached Windows installer services
        // may outlive it; cancellation never searches for or kills them by name.
        let result = capture_installer(&mut cmd, &lease.cancel).map_err(|e| e.to_string())?;
        utilities::refresh_paths();
        let available = utilities::inventory()
            .iter()
            .any(|u| u.id == tool && u.path.is_some());
        Ok(InstallResult {
            package: expected_package,
            scope,
            success: result.exit_code == Some(0) && !result.timed_out && !result.cancelled,
            available,
            exit_code: result.exit_code,
            timed_out: result.timed_out,
            cancelled: result.cancelled,
            stdout: String::from_utf8_lossy(&result.stdout).into(),
            stderr: String::from_utf8_lossy(&result.stderr).into(),
        })
    })
    .await
    .map_err(|error| InstallError::Raw(error.to_string()))?
}
#[cfg(test)]
mod tests {
    use super::*;
    fn assert_owned(error: InstallError, template: &str) {
        assert_eq!(
            serde_json::to_value(error).unwrap(),
            serde_json::json!({
                "source": "jarvis", "message": template, "values": []
            })
        );
    }
    #[test]
    fn validation_registry_and_support_failures_keep_owned_provenance() {
        assert_owned(
            request_key("main", "bad request").err().unwrap(),
            "Invalid utility installation request ID",
        );
        assert_owned(
            arguments("unknown", "unknown", "user", true).err().unwrap(),
            "No managed installer for this utility",
        );
        assert_owned(
            arguments("yq", "MikeFarah.yq", "user", false)
                .err()
                .unwrap(),
            "Review the package and accept its agreements before installation",
        );
        assert_owned(
            arguments("yq", "wrong.package", "user", true)
                .err()
                .unwrap(),
            "Review the package and accept its agreements before installation",
        );
        assert_owned(
            arguments("yq", "MikeFarah.yq", "invalid", true)
                .err()
                .unwrap(),
            "Invalid installation scope",
        );
        assert_owned(
            require_installer(false, false).err().unwrap(),
            "Installation requires Windows App Installer (WinGet)",
        );
        assert_owned(
            require_installer(true, true).err().unwrap(),
            "Package installation is disabled in ordinary QA",
        );
        assert!(require_installer(true, false).is_ok());
        let mut state = InstallRegistry::default();
        let _lease = state.register("main:first".into()).unwrap();
        assert_owned(
            state.register("main:second".into()).err().unwrap(),
            "Another utility installation is running",
        );
        state.closed = true;
        assert_owned(
            state.register("main:third".into()).err().unwrap(),
            "Utility installer is shutting down",
        );
    }
    #[test]
    fn runtime_diagnostics_and_installer_output_remain_raw() {
        let raw = "Another utility installation is running";
        assert_eq!(
            serde_json::to_value(InstallError::from(raw.to_owned())).unwrap(),
            raw
        );
        let result = InstallResult {
            package: "test.fixture".into(),
            scope: "user".into(),
            success: false,
            available: false,
            exit_code: Some(1),
            timed_out: false,
            cancelled: false,
            stdout: "Installation requires Windows App Installer (WinGet) {0}".into(),
            stderr: "C:/raw/WinGet error: Utility installer is shutting down".into(),
        };
        let payload = serde_json::to_value(&result).unwrap();
        assert_eq!(payload["stdout"], result.stdout);
        assert_eq!(payload["stderr"], result.stderr);
    }
    #[test]
    fn cancellation_is_window_scoped_early_bounded_and_shutdown_rejects_new_work() {
        let mut state = InstallRegistry::default();
        let main = request_key("main", "request-1").unwrap();
        let other = request_key("other", "request-1").unwrap();
        assert!(state.cancellation(main.clone()).is_none());
        let lease = state.register(main.clone()).unwrap();
        assert!(lease.cancel.is_cancelled());
        assert!(state.cancellation(other).is_none());
        assert!(Arc::ptr_eq(
            &state.cancellation(main).unwrap(),
            &lease.cancel
        ));
        assert!(state.register("main:another".into()).is_err());
        for index in 0..100 {
            state.cancellation(format!("main:early-{index}"));
        }
        assert_eq!(state.early.len(), 64);
        assert_eq!(state.early.back().unwrap(), "main:early-99");
        state.active = None;
        state.closed = true;
        assert!(state.register("main:after-shutdown".into()).is_err());
        assert!(request_key("main", "invalid request").is_err());
    }
    #[test]
    fn cancelled_before_capture_never_starts_an_installer() {
        let cancel = capture::CancelHandle::default();
        cancel.cancel();
        let mut command = Command::new("nonexistent-installer-must-not-start");
        let result = capture_installer(&mut command, &cancel).unwrap();
        assert!(result.cancelled);
        assert!(!result.timed_out);
        assert!(result.exit_code.is_none());
    }
    #[cfg(windows)]
    #[test]
    fn cancellation_terminates_only_owned_fake_installer_descendants() {
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .ancestors()
            .nth(4)
            .unwrap();
        let python = root.join(".cache/qa-tools/Scripts/python.exe");
        assert!(python.is_file(), "offline Python fixture is required");
        let temp = tempfile::tempdir().unwrap();
        let mut command = Command::new(python);
        command.current_dir(temp.path()).args(["-c", "import subprocess,sys,time,pathlib;subprocess.Popen([sys.executable,'-c',\"import time,pathlib;time.sleep(1);pathlib.Path('late.txt').write_text('unexpected')\"],creationflags=0x08000000);pathlib.Path('ready.txt').write_text('ready');time.sleep(30)"])
            .stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::piped());
        hide_console(&mut command);
        let cancel = Arc::new(capture::CancelHandle::default());
        let running = cancel.clone();
        let task = std::thread::spawn(move || capture_installer(&mut command, &running).unwrap());
        let deadline = std::time::Instant::now() + Duration::from_secs(3);
        while !temp.path().join("ready.txt").exists() {
            assert!(
                std::time::Instant::now() < deadline,
                "fake installer did not start"
            );
            std::thread::sleep(Duration::from_millis(10));
        }
        cancel.cancel();
        let result = task.join().unwrap();
        assert!(result.cancelled);
        assert!(!result.timed_out);
        std::thread::sleep(Duration::from_millis(1100));
        assert!(!temp.path().join("late.txt").exists());
    }
    #[test]
    fn installer_is_exact_and_requires_agreements() {
        assert!(crate::handles("utility_install_cancel"));
        assert!(arguments("yq", "MikeFarah.yq", "user", false).is_err());
        assert!(arguments("yq", "other.package", "user", true).is_err());
        assert!(arguments("yq", "MikeFarah.yq", "user --force", true).is_err());
        assert!(arguments("arbitrary", "other.package", "user", true).is_err());
        let args = arguments("yq", "MikeFarah.yq", "user", true).unwrap();
        assert!(args.windows(2).any(|v| v == ["--source", "winget"]));
        assert!(!args
            .iter()
            .any(|v| v == "--force" || v == "--ignore-security-hash"));
    }
}
