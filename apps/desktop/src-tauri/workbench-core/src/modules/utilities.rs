//! Shared, optional CLI discovery and bounded argument-vector execution.
use super::{
    fs::{blocking, to_canon},
    proc::{
        capture::{self, Limits},
        hide_console,
    },
    workspace::{authorize_spawn_cwd, WorkspaceEnv, WorkspaceRegistry},
};
use serde::Serialize;
use std::{
    collections::HashSet,
    ffi::OsString,
    path::{Path, PathBuf},
    process::{Command, Stdio},
    time::Duration,
};
use tauri::Manager;

const TOOLS: &[(&str, &str, &str)] = &[
    ("rg", "Search", "ripgrep regex and file search"),
    ("fd", "Search", "Fast file discovery"),
    ("jq", "Data", "JSON filtering"),
    ("yq", "Data", "YAML processing"),
    ("git", "Development", "Version control"),
    ("gh", "Development", "GitHub CLI"),
    ("agy", "AI", "Antigravity CLI: optional external agent, models and login"),
    ("python", "Code", "Python scripts and installed packages"),
    ("node", "Code", "JavaScript runtime"),
    ("npm", "Development", "Node package manager"),
    ("pnpm", "Development", "Node package manager"),
    ("uv", "Development", "Python environment/package manager"),
    ("pip", "Development", "Python package manager"),
    ("cargo", "Development", "Rust build tools"),
    ("rustc", "Development", "Rust compiler"),
    ("go", "Development", "Go compiler"),
    ("dotnet", "Development", ".NET SDK"),
    ("java", "Development", "Java runtime"),
    ("javac", "Development", "Java compiler"),
    ("gcc", "Development", "C compiler"),
    ("clang", "Development", "C/C++ compiler"),
    ("cmake", "Development", "Build configuration"),
    ("ninja", "Development", "Build runner"),
    ("make", "Development", "Build runner"),
    ("curl", "Network", "HTTP requests"),
    ("tar", "Archives", "TAR/ZIP archives"),
    (
        "zip",
        "Archives",
        "ZIP CLI (native ZIP tools are also built in)",
    ),
    ("unzip", "Archives", "ZIP CLI"),
    ("7z", "Archives", "7-Zip archives"),
    ("ffmpeg", "Media", "Audio/video conversion"),
    ("ffprobe", "Media", "Audio/video metadata"),
    ("magick", "Media", "Image conversion"),
    ("tesseract", "Documents", "OCR"),
    ("pdftotext", "Documents", "PDF text extraction"),
    ("pdfinfo", "Documents", "PDF metadata"),
    ("pdftoppm", "Documents", "PDF rendering"),
    ("sqlite3", "Data", "SQLite CLI"),
    ("bash", "Shell", "POSIX shell"),
    ("pwsh", "Shell", "PowerShell"),
    ("powershell", "Shell", "Windows PowerShell"),
    ("wsl", "Shell", "Windows Subsystem for Linux"),
    ("pandoc", "Documents", "Document conversion"),
    ("soffice", "Documents", "LibreOffice conversion"),
];
#[derive(Serialize)]
pub struct Utility {
    pub id: String,
    pub category: String,
    pub description: String,
    pub available: bool,
    pub path: Option<String>,
    pub source: String,
    pub direct: bool,
    pub builtin: bool,
    pub install_package: Option<String>,
    pub installer_available: bool,
}

static EXTRA_PATHS: std::sync::OnceLock<std::sync::RwLock<Vec<PathBuf>>> =
    std::sync::OnceLock::new();
/// Read current installer-updated PATH without mutating the multithreaded process environment.
pub fn refresh_paths() {
    let mut paths = Vec::new();
    #[cfg(windows)]
    // SAFETY: buffers remain allocated for each Win32 call, sizes are in bytes/u16s
    // as required, failed/truncated queries are ignored, and expansion bounds are checked.
    // nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage
    unsafe {
        use windows_sys::Win32::System::{Environment::ExpandEnvironmentStringsW, Registry::*};
        for (root, subkey) in [
            (HKEY_CURRENT_USER, "Environment"),
            (
                HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
            ),
        ] {
            let key: Vec<u16> = subkey.encode_utf16().chain(Some(0)).collect();
            let value: Vec<u16> = "Path".encode_utf16().chain(Some(0)).collect();
            let mut data = vec![0u16; 32768];
            let mut bytes = (data.len() * 2) as u32;
            if RegGetValueW(
                root,
                key.as_ptr(),
                value.as_ptr(),
                RRF_RT_REG_SZ | RRF_RT_REG_EXPAND_SZ | RRF_NOEXPAND,
                std::ptr::null_mut(),
                data.as_mut_ptr().cast(),
                &mut bytes,
            ) == 0
            {
                let mut expanded = vec![0u16; 32768];
                let length = ExpandEnvironmentStringsW(
                    data.as_ptr(),
                    expanded.as_mut_ptr(),
                    expanded.len() as u32,
                );
                if length > 0 && length <= expanded.len() as u32 {
                    paths.extend(std::env::split_paths(&std::ffi::OsString::from(
                        String::from_utf16_lossy(&expanded[..length as usize - 1]),
                    )));
                }
            }
        }
        if let Some(local) = std::env::var_os("LOCALAPPDATA") {
            paths.push(PathBuf::from(local).join("Microsoft/WinGet/Links"));
        }
    }
    for root in project_roots() {
        paths.extend(managed_rust_bins(&root));
    }
    #[cfg(windows)]
    {
        // Several official installers do not register their bin directory in PATH.
        for key in ["ProgramFiles", "ProgramFiles(x86)"] {
            if let Some(base) = std::env::var_os(key) {
                let base = PathBuf::from(base);
                for suffix in [
                    "7-Zip",
                    "Tesseract-OCR",
                    "LibreOffice/program",
                    "CMake/bin",
                    "LLVM/bin",
                    "Go/bin",
                    "dotnet",
                    "Pandoc",
                ] {
                    paths.push(base.join(suffix));
                }
                if let Ok(entries) = std::fs::read_dir(&base) {
                    for entry in entries.flatten().take(500) {
                        if entry
                            .file_name()
                            .to_string_lossy()
                            .starts_with("ImageMagick-")
                        {
                            paths.push(entry.path());
                        }
                    }
                }
                if let Ok(entries) = std::fs::read_dir(base.join("Eclipse Adoptium")) {
                    for entry in entries.flatten().take(50) {
                        paths.push(entry.path().join("bin"));
                    }
                }
            }
        }
        if let Some(home) = dirs::home_dir() {
            paths.push(home.join(".cargo/bin"));
            paths.push(home.join("AppData/Local/agy/bin"));
        }
        if let Some(local) = std::env::var_os("LOCALAPPDATA") {
            paths.push(PathBuf::from(local).join("Pandoc"));
        }
        paths.retain(|path| path.is_dir());
    }
    *EXTRA_PATHS
        .get_or_init(Default::default)
        .write()
        .unwrap_or_else(|e| e.into_inner()) = paths;
}
fn dependency_root() -> Option<PathBuf> {
    std::env::var_os("JARVIS_TOOLCHAIN_DIR")
        .map(PathBuf::from)
        .or_else(|| {
            dirs::home_dir()
                .map(|p| p.join(".cache/codex-runtimes/codex-primary-runtime/dependencies"))
        })
}
fn project_roots() -> Vec<PathBuf> {
    let mut roots = Vec::new();
    if let Some(root) = std::env::var_os("JARVIS_ROOT") {
        roots.push(PathBuf::from(root));
    }
    // Runtime discovery beside this executable, never an authorization decision.
    // Discovered programs still require the normal workspace/execution approval.
    // nosemgrep: rust.lang.security.current-exe.current-exe
    if let Ok(exe) = std::env::current_exe() {
        if let Some(parent) = exe.parent().and_then(Path::parent) {
            roots.push(parent.to_path_buf());
        }
    }
    if let Ok(cwd) = std::env::current_dir() {
        roots.extend(cwd.ancestors().take(6).map(Path::to_path_buf));
    }
    roots
}
fn local_python() -> Option<PathBuf> {
    project_roots()
        .into_iter()
        .map(|root| {
            root.join(if cfg!(windows) {
                ".venv/Scripts/python.exe"
            } else {
                ".venv/bin/python"
            })
        })
        .find(|path| path.is_file())
}
fn managed_rust_toolchain() -> String {
    let platform = if cfg!(target_os = "windows") {
        if cfg!(target_env = "msvc") { "pc-windows-msvc" } else { "pc-windows-gnu" }
    } else if cfg!(target_os = "macos") { "apple-darwin" }
    else if cfg!(target_env = "musl") { "unknown-linux-musl" }
    else { "unknown-linux-gnu" };
    format!("stable-{}-{platform}", std::env::consts::ARCH)
}
fn managed_rust_bins(root: &Path) -> Vec<PathBuf> {
    let toolchains = root.join(".cache/desktop-toolchain/rustup/toolchains");
    let Ok(entries) = std::fs::read_dir(toolchains) else { return Vec::new() };
    let mut bins: Vec<_> = entries.flatten().take(100)
        .filter(|entry| entry.file_name().to_string_lossy() == managed_rust_toolchain())
        .map(|entry| entry.path().join("bin"))
        .filter(|bin| bin.is_dir()).collect();
    bins.sort();
    bins
}
fn resolve(id: &str) -> Option<(PathBuf, String)> {
    if id == "python" {
        if let Some(p) = local_python() {
            return Some((p, "jarvis-venv".into()));
        }
    }
    if id == "cargo" || id == "rustc" {
        let suffix = if cfg!(windows) { ".exe" } else { "" };
        for root in project_roots() {
            for bin in managed_rust_bins(&root) {
                let program = bin.join(format!("{id}{suffix}"));
                if program.is_file() { return Some((program, "jarvis-managed Rust".into())); }
            }
        }
    }
    if let Ok(p) = which::which(id) {
        return Some((p, "PATH".into()));
    }
    if let Some(paths) = EXTRA_PATHS.get() {
        let paths = paths.read().unwrap_or_else(|e| e.into_inner());
        if let (Ok(paths), Ok(cwd)) = (std::env::join_paths(paths.iter()), std::env::current_dir())
        {
            if let Ok(path) = which::which_in(id, Some(paths), cwd) {
                return Some((path, "installed PATH".into()));
            }
        }
    }
    if id == "python" {
        if let Ok(p) = which::which("python3") {
            return Some((p, "PATH".into()));
        }
    }
    if let Some(root) = dependency_root() {
        let suffix = if cfg!(windows) { ".exe" } else { "" };
        let candidates = [
            root.join(format!("node/bin/{id}{suffix}")),
            root.join(format!("python/{id}{suffix}")),
            root.join(format!("python/bin/{id}{suffix}")),
            root.join(format!("native/powershell/{id}{suffix}")),
            root.join(format!("native/poppler/Library/bin/{id}{suffix}")),
            root.join(format!("native/poppler/bin/{id}{suffix}")),
            root.join(format!("native/git/usr/bin/{id}{suffix}")),
            root.join(format!("bin/override/{id}{suffix}")),
            root.join(format!("bin/fallback/{id}{suffix}")),
        ];
        if let Some(p) = candidates.into_iter().find(|p| p.is_file()) {
            return Some((p, "shared-runtime".into()));
        }
    }
    #[cfg(windows)]
    if id == "bash" {
        if let Some(base) = std::env::var_os("ProgramFiles") {
            let p = PathBuf::from(base).join("Git/bin/bash.exe");
            if p.is_file() {
                return Some((p, "Git for Windows".into()));
            }
        }
    }
    None
}
fn program(id: &str) -> Result<(PathBuf, Vec<OsString>), String> {
    if !TOOLS.iter().any(|t| t.0 == id) {
        return Err("Unknown utility; use utility_catalog first".into());
    }
    if id == "pip" {
        let p = resolve("python").ok_or("Python is not installed")?.0;
        #[cfg(windows)]
        if let Some(prefix) = p.parent().and_then(Path::parent) {
            if !prefix.join("Lib/site-packages/pip/__main__.py").is_file() {
                return Err("pip module is not installed in this runtime".into());
            }
        }
        return Ok((p, vec!["-m".into(), "pip".into()]));
    }
    if id == "npm" || id == "pnpm" {
        if let Some((node, _)) = resolve("node") {
            let mut scripts = vec![];
            if let Some(parent) = node.parent() {
                scripts.push(parent.join(format!(
                    "node_modules/{id}/bin/{}",
                    if id == "npm" {
                        "npm-cli.js"
                    } else {
                        "pnpm.cjs"
                    }
                )));
                if let Some(root) = parent.parent() {
                    scripts.push(root.join(format!(
                        "node_modules/{id}/bin/{}",
                        if id == "npm" {
                            "npm-cli.js"
                        } else {
                            "pnpm.cjs"
                        }
                    )));
                }
            }
            if let Some(script) = scripts.into_iter().find(|p| p.is_file()) {
                return Ok((node, vec![script.into_os_string()]));
            }
        }
    }
    let path = resolve(id)
        .ok_or_else(|| {
            format!(
                "{id} is not installed; inspect utility_catalog or use an available alternative"
            )
        })?
        .0;
    if path
        .extension()
        .is_some_and(|e| e.eq_ignore_ascii_case("cmd") || e.eq_ignore_ascii_case("bat"))
    {
        return Err("Batch wrappers require the approved bash_run shell tool".into());
    }
    Ok((path, vec![]))
}
pub fn inventory() -> Vec<Utility> {
    let installer_available = super::utility_install::supported();
    TOOLS
        .iter()
        .map(|(id, category, description)| {
            let resolved = resolve(id);
            let executable = program(id).ok();
            let builtin = ["rg", "zip", "unzip"].contains(id);
            Utility {
                id: (*id).into(),
                category: (*category).into(),
                description: (*description).into(),
                available: executable.is_some() || resolved.is_some() || builtin,
                path: executable
                    .as_ref()
                    .map(|p| to_canon(&p.0))
                    .or_else(|| resolved.as_ref().map(|p| to_canon(&p.0))),
                source: resolved.map(|p| p.1).unwrap_or_else(|| {
                    if executable.is_some() {
                        "runtime module".into()
                    } else {
                        if builtin {
                            "built-in Rust".into()
                        } else {
                            "not installed".into()
                        }
                    }
                }),
                direct: executable.is_some(),
                builtin,
                install_package: super::utility_install::package(id).map(str::to_owned),
                installer_available,
            }
        })
        .collect()
}
/// Only this application's environment is changed, before worker threads start.
pub fn configure_env() {
    refresh_paths();
    let mut seen = HashSet::new();
    let mut paths = vec![];
    for item in inventory() {
        if let Some(path) = item.path {
            if let Some(parent) = Path::new(&path).parent() {
                let key = parent.to_string_lossy().to_lowercase();
                if seen.insert(key) {
                    paths.push(parent.to_path_buf());
                }
            }
        }
    }
    if let Some(old) = std::env::var_os("PATH") {
        for p in std::env::split_paths(&old) {
            if seen.insert(p.to_string_lossy().to_lowercase()) {
                paths.push(p);
            }
        }
    }
    if let Ok(path) = std::env::join_paths(paths) {
        std::env::set_var("PATH", path);
    }
}
#[tauri::command]
pub async fn utility_inventory() -> Result<Vec<Utility>, String> {
    blocking(|| {
        refresh_paths();
        Ok(inventory())
    })
    .await
}
#[derive(Serialize)]
pub struct Output {
    pub tool: String,
    pub stdout: String,
    pub stderr: String,
    pub exit_code: Option<i32>,
    pub timed_out: bool,
    pub truncated: bool,
    pub decoding_loss: bool,
    pub output_complete: bool,
}

fn output_from_capture(id: &str, result: capture::Captured) -> Output {
    let decoding_loss = std::str::from_utf8(&result.stdout).is_err()
        || std::str::from_utf8(&result.stderr).is_err();
    let truncated = result.stdout_truncated || result.stderr_truncated;
    Output {
        tool: id.into(),
        stdout: String::from_utf8_lossy(&result.stdout).into(),
        stderr: String::from_utf8_lossy(&result.stderr).into(),
        exit_code: result.exit_code,
        timed_out: result.timed_out,
        truncated,
        decoding_loss,
        output_complete: !(decoding_loss || truncated || result.timed_out || result.cancelled),
    }
}
#[cfg(test)]
fn run(
    id: &str,
    args: &[String],
    cwd: &Path,
    timeout: u64,
    max_output: usize,
) -> Result<Output, String> {
    run_cancellable(id, args, cwd, timeout, max_output, None)
}
fn run_cancellable(
    id: &str,
    args: &[String],
    cwd: &Path,
    timeout: u64,
    max_output: usize,
    cancel: Option<&capture::CancelHandle>,
) -> Result<Output, String> {
    if cancel.is_some_and(capture::CancelHandle::is_cancelled) {
        return Err("Command cancelled".into());
    }
    if args.len() > 128
        || args.iter().any(|s| s.contains('\0'))
        || args.iter().map(|s| s.len()).sum::<usize>() > 64000
    {
        return Err("Too many/invalid utility arguments".into());
    }
    let (program, prefix) = program(id)?;
    let mut cmd = Command::new(program);
    cmd.args(prefix)
        .args(args)
        .current_dir(cwd)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    if id == "python" || id == "pip" {
        cmd.env("PYTHONUTF8", "1").env("PYTHONIOENCODING", "utf-8");
    }
    if let Some(paths) = EXTRA_PATHS.get() {
        let extra = paths.read().unwrap_or_else(|e| e.into_inner());
        let current = std::env::var_os("PATH").unwrap_or_default();
        if let Ok(path) =
            std::env::join_paths(std::env::split_paths(&current).chain(extra.iter().cloned()))
        {
            cmd.env("PATH", path);
        }
    }
    hide_console(&mut cmd);
    let result = capture::run_tree_cancellable(
        &mut cmd,
        Duration::from_secs(timeout.clamp(1, 300)),
        Limits {
            stdout: max_output.clamp(1024, 32000),
            stderr: 4000,
            stop_on_stdout_cap: true,
        },
        cancel,
    )
    .map_err(|e| e.to_string())?;
    if result.cancelled { return Err("Command cancelled".into()); }
    Ok(output_from_capture(id, result))
}
#[tauri::command]
pub async fn utility_run(
    app: tauri::AppHandle,
    window: tauri::WebviewWindow,
    tool: String,
    args: Vec<String>,
    cwd: String,
    timeout_secs: Option<u64>,
    max_output: Option<usize>,
    workspace: Option<WorkspaceEnv>,
    request_id: Option<String>,
) -> Result<Output, String> {
    let lease = match request_id.as_deref() {
        Some(id) => Some(app.state::<super::shell::ShellState>().start_run(window.label(), id)?),
        None => None,
    };
    blocking(move || {
        let workspace = WorkspaceEnv::from_option(workspace);
        if workspace.is_wsl() {
            return Err("utility_run uses the host toolchain. Use bash_run for utilities inside the selected WSL distribution.".into());
        }
        let cwd = authorize_spawn_cwd(&app.state::<WorkspaceRegistry>(), Some(&cwd), &workspace)?
            .ok_or("Working directory is required")?;
        run_cancellable(&tool, &args, &cwd, timeout_secs.unwrap_or(30), max_output.unwrap_or(8000), lease.as_ref().map(|l| l.cancel.as_ref()))
    }).await
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn utility_output_valid_replacement_character_is_complete() {
        let out = output_from_capture("python", capture::Captured {
            stdout: "valid \u{fffd} Русский".as_bytes().to_vec(),
            exit_code: Some(0), ..Default::default()
        });
        assert_eq!(out.stdout, "valid \u{fffd} Русский");
        assert!(!out.decoding_loss);
        assert!(out.output_complete);
    }
    #[test]
    fn utility_output_invalid_utf8_is_incomplete_without_changing_exit_status() {
        for stderr in [false, true] {
            let mut captured = capture::Captured { exit_code: Some(0), ..Default::default() };
            if stderr { captured.stderr = vec![0xff]; } else { captured.stdout = vec![0xff]; }
            let out = output_from_capture("python", captured);
            assert_eq!(out.exit_code, Some(0));
            assert!(out.decoding_loss);
            assert!(!out.output_complete);
            assert!(!out.truncated);
        }
    }
    #[test]
    fn utility_output_interruption_or_truncation_is_incomplete() {
        for captured in [
            capture::Captured { timed_out: true, ..Default::default() },
            capture::Captured { cancelled: true, ..Default::default() },
            capture::Captured { stdout_truncated: true, ..Default::default() },
            capture::Captured { stderr_truncated: true, ..Default::default() },
        ] {
            let out = output_from_capture("python", captured);
            assert!(!out.output_complete);
            assert!(!out.decoding_loss);
        }
    }
    #[test]
    fn unknown_and_nul_arguments_are_rejected() {
        assert!(program("arbitrary.exe").is_err());
        assert!(run("python", &["bad\0arg".into()], Path::new("."), 1, 1024).is_err());
    }
    #[test]
    fn managed_rust_discovery_is_scoped_to_stable_installed_bins() {
        let dir = tempfile::tempdir().unwrap();
        assert!(managed_rust_bins(dir.path()).is_empty());
        let base = dir.path().join(".cache/desktop-toolchain/rustup/toolchains");
        std::fs::create_dir_all(base.join(managed_rust_toolchain()).join("bin")).unwrap();
        std::fs::create_dir_all(base.join("stable-incompatible-host/bin")).unwrap();
        std::fs::create_dir_all(base.join("nightly-x86_64-pc-windows-msvc/bin")).unwrap();
        std::fs::create_dir_all(base.join("stable-incomplete")).unwrap();
        assert_eq!(managed_rust_bins(dir.path()), vec![base.join(managed_rust_toolchain()).join("bin")]);
    }
    #[test]
    fn inventory_is_truthful() {
        let items = inventory();
        assert!(items.iter().any(|t| t.id == "rg"));
        let agy = items.iter().find(|t| t.id == "agy").unwrap();
        assert_eq!(agy.category, "AI");
        assert!(agy.install_package.is_none());
        for t in items {
            if t.direct {
                assert!(t.available);
                assert!(Path::new(t.path.as_ref().unwrap()).is_file());
            }
        }
    }
    #[cfg(windows)]
    #[test]
    fn timeout_ends_owned_descendants() {
        if program("python").is_err() {
            return;
        }
        let dir = tempfile::tempdir().unwrap();
        let code="import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',\"import pathlib,time;time.sleep(2);pathlib.Path('late.txt').write_text('bad')\"],creationflags=0x08000000);time.sleep(10)";
        let out = run("python", &["-c".into(), code.into()], dir.path(), 1, 1024).unwrap();
        assert!(out.timed_out);
        std::thread::sleep(Duration::from_millis(1400));
        assert!(!dir.path().join("late.txt").exists());
    }
    #[test]
    fn timeout_and_output_are_bounded() {
        if program("python").is_err() {
            return;
        }
        let dir = tempfile::tempdir().unwrap();
        let out = run(
            "python",
            &["-c".into(), "import time;time.sleep(10)".into()],
            dir.path(),
            1,
            1024,
        )
        .unwrap();
        assert!(out.timed_out);
        let out = run(
            "python",
            &["-c".into(), "print('x'*100000)".into()],
            dir.path(),
            10,
            1024,
        )
        .unwrap();
        assert!(out.truncated);
        assert!(out.stdout.len() <= 1024);
    }
    #[test]
    fn literal_arguments_are_not_shell_syntax() {
        if program("python").is_err() {
            return;
        }
        let temp = tempfile::tempdir().unwrap();
        let out = run(
            "python",
            &[
                "-c".into(),
                "import sys; print(sys.argv[1])".into(),
                "a & echo INJECTED > escaped.txt".into(),
            ],
            temp.path(),
            10,
            8000,
        )
        .unwrap();
        assert_eq!(out.exit_code, Some(0));
        assert!(out.stdout.contains("a & echo"));
        assert!(!temp.path().join("escaped.txt").exists());
    }
}
