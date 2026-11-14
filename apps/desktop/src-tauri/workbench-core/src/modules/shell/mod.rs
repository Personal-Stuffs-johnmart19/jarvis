pub mod background;
pub mod ringbuffer;
pub mod session;

use std::collections::{HashMap, HashSet, VecDeque};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::{Arc, Mutex, RwLock};
use std::time::Duration;

use serde::Serialize;
use tauri::Manager;

use crate::modules::proc::capture::{self, CancelHandle, Limits};
#[cfg(windows)]
use crate::modules::workspace::validate_wsl_distro_name;
use crate::modules::workspace::{authorize_spawn_cwd, WorkspaceEnv, WorkspaceRegistry};

use background::{BackgroundLogResponse, BackgroundProc, BackgroundProcInfo};
use session::{SessionRunOutput, ShellSession};

const DEFAULT_TIMEOUT_SECS: u64 = 30;
const MAX_TIMEOUT_SECS: u64 = 300;
const MAX_OUTPUT_BYTES: usize = 256 * 1024;
const MAX_ACTIVE_RUNS: usize = 32;
const MAX_PREREGISTERED_CANCELS: usize = 64;
const MAX_SEALED_RUNS: usize = 4096;
type RunKey = (String, String);

pub const CANCELLED_ERROR: &str = "Command cancelled";

#[derive(Serialize)]
pub struct CommandOutput {
    pub stdout: String,
    pub stderr: String,
    pub exit_code: Option<i32>,
    pub timed_out: bool,
    pub truncated: bool,
}

/// Runs a one-shot command via the user's login shell. Output is capped and
/// the process is force-killed on timeout. We deliberately do NOT pipe into
/// the user's interactive PTY — that would fight their input. AI tool calls
/// are presented in chat as their own structured result.
#[tauri::command]
pub async fn shell_run_command(
    app: tauri::AppHandle,
    window: tauri::WebviewWindow,
    command: String,
    cwd: Option<String>,
    timeout_secs: Option<u64>,
    workspace: Option<WorkspaceEnv>,
    request_id: Option<String>,
) -> Result<CommandOutput, String> {
    let trimmed = command.trim().to_string();
    if trimmed.is_empty() {
        return Err("empty command".into());
    }
    let workspace = WorkspaceEnv::from_option(workspace);
    let dur = clamp_timeout(timeout_secs);
    let lease = match request_id.as_deref() {
        Some(id) => Some(app.state::<ShellState>().start_run(window.label(), id)?),
        None => None,
    };

    // Authorization canonicalizes the cwd and the run blocks until exit, so
    // both belong on the blocking pool, never on an async worker.
    tauri::async_runtime::spawn_blocking(move || {
        let cancel = lease.as_ref().map(|l| l.cancel.as_ref());
        if cancel.is_some_and(CancelHandle::is_cancelled) {
            return Err(CANCELLED_ERROR.into());
        }
        authorize_spawn_cwd(
            &app.state::<WorkspaceRegistry>(),
            cwd.as_deref(),
            &workspace,
        )?;
        let cwd_path = cwd
            .as_deref()
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(str::to_string);
        run_blocking_cancellable(trimmed, cwd_path, workspace, dur, cancel)
    })
    .await
    .map_err(|e| e.to_string())?
}

fn clamp_timeout(timeout_secs: Option<u64>) -> Duration {
    Duration::from_secs(
        timeout_secs
            .unwrap_or(DEFAULT_TIMEOUT_SECS)
            .clamp(1, MAX_TIMEOUT_SECS),
    )
}

pub(crate) fn run_blocking_inner(
    command: String,
    cwd: Option<String>,
    workspace: WorkspaceEnv,
    dur: Duration,
) -> Result<CommandOutput, String> {
    run_blocking_cancellable(command, cwd, workspace, dur, None)
}

pub(crate) fn run_blocking_cancellable(
    command: String,
    cwd: Option<String>,
    workspace: WorkspaceEnv,
    dur: Duration,
    cancel: Option<&CancelHandle>,
) -> Result<CommandOutput, String> {
    if cancel.is_some_and(CancelHandle::is_cancelled) {
        return Err(CANCELLED_ERROR.into());
    }
    let mut cmd = build_oneshot_command(&command, &workspace, cwd.as_deref())?;
    if let (WorkspaceEnv::Local, Some(dir)) = (&workspace, cwd) {
        cmd.current_dir(dir);
    }
    cmd.stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    crate::modules::proc::hide_console(&mut cmd);

    let captured = capture::run_tree_cancellable(
        &mut cmd,
        dur,
        Limits {
            stdout: MAX_OUTPUT_BYTES,
            stderr: MAX_OUTPUT_BYTES,
            stop_on_stdout_cap: false,
        },
        cancel,
    )
    .map_err(|e| {
        log::warn!("shell_run_command failed: {e}");
        e.to_string()
    })?;

    if captured.cancelled {
        return Err(CANCELLED_ERROR.into());
    }

    Ok(CommandOutput {
        stdout: String::from_utf8_lossy(&captured.stdout).into_owned(),
        stderr: String::from_utf8_lossy(&captured.stderr).into_owned(),
        exit_code: captured.exit_code,
        timed_out: captured.timed_out,
        truncated: captured.stdout_truncated || captured.stderr_truncated,
    })
}

// ──────────────────────────────────────────────────────────────────────────
// Persistent agent shell state + background process state.
// ──────────────────────────────────────────────────────────────────────────

#[derive(Default)]
struct RunRegistry {
    active: HashMap<RunKey, Arc<CancelHandle>>,
    preregistered: VecDeque<RunKey>,
    sealed: HashSet<String>,
}

pub struct ShellRunLease {
    runs: Arc<Mutex<RunRegistry>>,
    key: RunKey,
    pub cancel: Arc<CancelHandle>,
}

impl Drop for ShellRunLease {
    fn drop(&mut self) {
        let mut reg = self.runs.lock().unwrap_or_else(|e| e.into_inner());
        reg.active.remove(&self.key);
    }
}

fn is_valid_request_id(id: &str) -> bool {
    !id.is_empty()
        && id.len() <= 64
        && id
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_')
}

pub struct ShellState {
    sessions: RwLock<HashMap<u32, Arc<ShellSession>>>,
    bg: RwLock<HashMap<u32, Arc<BackgroundProc>>>,
    runs: Arc<Mutex<RunRegistry>>,
    next_session_id: AtomicU32,
    next_bg_id: AtomicU32,
}

impl Default for ShellState {
    fn default() -> Self {
        Self {
            sessions: RwLock::new(HashMap::new()),
            bg: RwLock::new(HashMap::new()),
            runs: Arc::new(Mutex::new(RunRegistry::default())),
            next_session_id: AtomicU32::new(1),
            next_bg_id: AtomicU32::new(1),
        }
    }
}

impl ShellState {
    pub fn start_run(&self, window: &str, id: &str) -> Result<ShellRunLease, String> {
        if !is_valid_request_id(id) {
            return Err("invalid shell request id".into());
        }
        let key = (window.to_owned(), id.to_owned());
        let mut reg = self.runs.lock().unwrap_or_else(|e| e.into_inner());
        if reg.sealed.contains(id) {
            return Err(CANCELLED_ERROR.into());
        }
        let pre_cancelled = if let Some(pos) = reg.preregistered.iter().position(|k| k == &key) {
            reg.preregistered.remove(pos);
            true
        } else {
            false
        };
        if reg.active.contains_key(&key) {
            return Err("shell request id is already active".into());
        }
        if reg.active.len() >= MAX_ACTIVE_RUNS {
            return Err("too many active shell runs".into());
        }
        let cancel = Arc::new(CancelHandle::default());
        if pre_cancelled {
            cancel.cancel();
        }
        reg.active.insert(key.clone(), cancel.clone());
        Ok(ShellRunLease {
            runs: self.runs.clone(),
            key,
            cancel,
        })
    }

    pub fn cancel_run(&self, window: &str, id: &str) {
        if !is_valid_request_id(id) {
            return;
        }
        let key = (window.to_owned(), id.to_owned());
        let handle = {
            let mut reg = self.runs.lock().unwrap_or_else(|e| e.into_inner());
            if let Some(handle) = reg.active.get(&key).cloned() {
                Some(handle)
            } else {
                if !reg.preregistered.contains(&key) {
                    if reg.preregistered.len() >= MAX_PREREGISTERED_CANCELS {
                        reg.preregistered.pop_front();
                    }
                    reg.preregistered.push_back(key);
                }
                None
            }
        };
        if let Some(handle) = handle {
            handle.cancel();
        }
    }

    /// Revoke delayed admission and prove physical settlement of this opaque ID.
    /// Tombstones are never evicted: capacity exhaustion requires app recovery.
    pub fn seal_run(&self, window: &str, id: &str) -> Result<bool, String> {
        if !is_valid_request_id(id) {
            return Err("invalid shell request id".into());
        }
        let handle = {
            let mut reg = self.runs.lock().unwrap_or_else(|e| e.into_inner());
            if !reg.sealed.contains(id) && reg.sealed.len() >= MAX_SEALED_RUNS {
                return Err("Queued native cleanup requires restarting Jarvis.".into());
            }
            reg.sealed.insert(id.to_owned());
            reg.active.get(&(window.to_owned(), id.to_owned())).cloned()
        };
        if let Some(handle) = handle { handle.cancel(); }
        let reg = self.runs.lock().unwrap_or_else(|e| e.into_inner());
        // Never cancel a different window's process or report it settled. The
        // global tombstone also blocks a delayed invocation from an old window.
        Ok(!reg.active.keys().any(|(_, request)| request == id))
    }

    pub fn cancel_window(&self, window: &str) {
        let handles: Vec<Arc<CancelHandle>> = {
            let mut reg = self.runs.lock().unwrap_or_else(|e| e.into_inner());
            reg.preregistered.retain(|(w, _)| w != window);
            reg.active
                .iter()
                .filter(|((owner, _), _)| owner == window)
                .map(|(_, h)| h.clone())
                .collect()
        };
        for handle in handles {
            handle.cancel();
        }
    }

    pub fn kill_all(&self) {
        let active_runs: Vec<Arc<CancelHandle>> = {
            let mut reg = self.runs.lock().unwrap_or_else(|e| e.into_inner());
            reg.preregistered.clear();
            reg.active.values().cloned().collect()
        };
        for handle in active_runs {
            handle.cancel();
        }
        let drained: Vec<Arc<BackgroundProc>> =
            self.bg.write().unwrap().drain().map(|(_, p)| p).collect();
        for proc in drained {
            proc.kill();
        }
    }
}

#[tauri::command]
pub async fn shell_session_open(
    app: tauri::AppHandle,
    cwd: Option<String>,
    workspace: Option<WorkspaceEnv>,
) -> Result<u32, String> {
    let workspace = WorkspaceEnv::from_option(workspace);
    // wsl_home spawns wsl.exe on Windows and authorization hits the disk.
    let registry_app = app.clone();
    let (initial, workspace) = tauri::async_runtime::spawn_blocking(move || {
        authorize_spawn_cwd(
            &registry_app.state::<WorkspaceRegistry>(),
            cwd.as_deref(),
            &workspace,
        )?;
        let initial = match cwd.as_deref().filter(|s| !s.is_empty()) {
            Some(c) => c.to_string(),
            None => {
                if let WorkspaceEnv::Wsl { distro } = &workspace {
                    crate::modules::workspace::wsl_home(distro.clone())?
                } else {
                    crate::modules::fs::to_canon(
                        dirs::home_dir().unwrap_or_else(|| PathBuf::from("/")),
                    )
                }
            }
        };
        Ok::<_, String>((initial, workspace))
    })
    .await
    .map_err(|e| e.to_string())??;
    Ok(register_session(&app, initial, workspace))
}

fn register_session(app: &tauri::AppHandle, initial: String, workspace: WorkspaceEnv) -> u32 {
    let state = app.state::<ShellState>();
    let session = Arc::new(ShellSession::new(initial, workspace));
    let id = state.next_session_id.fetch_add(1, Ordering::Relaxed);
    state.sessions.write().unwrap().insert(id, session);
    id
}

#[tauri::command]
pub async fn shell_session_run(
    app: tauri::AppHandle,
    window: tauri::WebviewWindow,
    id: u32,
    command: String,
    cwd: Option<String>,
    timeout_secs: Option<u64>,
    workspace: Option<WorkspaceEnv>,
    request_id: Option<String>,
) -> Result<SessionRunOutput, String> {
    let session = app
        .state::<ShellState>()
        .sessions
        .read()
        .unwrap()
        .get(&id)
        .cloned()
        .ok_or_else(|| "no shell session".to_string())?;
    let dur = clamp_timeout(timeout_secs);
    let lease = match request_id.as_deref() {
        Some(req_id) => Some(app.state::<ShellState>().start_run(window.label(), req_id)?),
        None => None,
    };
    tauri::async_runtime::spawn_blocking(move || {
        let cancel = lease.as_ref().map(|l| l.cancel.as_ref());
        if cancel.is_some_and(CancelHandle::is_cancelled) {
            return Err(CANCELLED_ERROR.into());
        }
        let effective_workspace = workspace
            .clone()
            .unwrap_or_else(|| session.workspace.clone());
        authorize_spawn_cwd(
            &app.state::<WorkspaceRegistry>(),
            cwd.as_deref(),
            &effective_workspace,
        )?;
        session.run_cancellable(command, cwd, workspace, dur, cancel)
    })
    .await
    .map_err(|e| e.to_string())?
}

#[tauri::command]
pub async fn shell_run_cancel(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, ShellState>,
    request_id: String,
    seal: Option<bool>,
) -> Result<RunSettlement, String> {
    if seal == Some(true) {
        return state.seal_run(window.label(), &request_id).map(|settled| RunSettlement { settled });
    }
    state.cancel_run(window.label(), &request_id);
    Ok(RunSettlement { settled: false })
}

#[derive(Serialize)]
pub struct RunSettlement { settled: bool }

#[tauri::command]
pub fn shell_session_close(state: tauri::State<ShellState>, id: u32) -> Result<(), String> {
    state.sessions.write().unwrap().remove(&id);
    Ok(())
}

#[tauri::command]
pub async fn shell_bg_spawn(
    app: tauri::AppHandle,
    command: String,
    cwd: Option<String>,
    workspace: Option<WorkspaceEnv>,
) -> Result<u32, String> {
    let workspace = WorkspaceEnv::from_option(workspace);
    tauri::async_runtime::spawn_blocking(move || {
        authorize_spawn_cwd(
            &app.state::<WorkspaceRegistry>(),
            cwd.as_deref(),
            &workspace,
        )?;
        let proc = background::spawn(command, cwd, workspace)?;
        let state = app.state::<ShellState>();
        let id = state.next_bg_id.fetch_add(1, Ordering::Relaxed);
        state.bg.write().unwrap().insert(id, proc);
        Ok(id)
    })
    .await
    .map_err(|e| e.to_string())?
}

#[tauri::command]
pub fn shell_bg_logs(
    state: tauri::State<ShellState>,
    handle: u32,
    since_offset: Option<u64>,
) -> Result<BackgroundLogResponse, String> {
    let proc = state
        .bg
        .read()
        .unwrap()
        .get(&handle)
        .cloned()
        .ok_or_else(|| "no background handle".to_string())?;
    Ok(proc.read_logs(since_offset.unwrap_or(0)))
}

#[tauri::command]
pub fn shell_bg_kill(state: tauri::State<ShellState>, handle: u32) -> Result<(), String> {
    if let Some(proc) = state.bg.read().unwrap().get(&handle).cloned() {
        proc.kill();
    }
    Ok(())
}

#[tauri::command]
pub fn shell_bg_list(state: tauri::State<ShellState>) -> Result<Vec<BackgroundProcInfo>, String> {
    let map = state.bg.read().unwrap();
    let mut out = Vec::with_capacity(map.len());
    for (id, p) in map.iter() {
        out.push(p.info(*id));
    }
    out.sort_by_key(|i| i.handle);
    Ok(out)
}

pub(crate) fn build_oneshot_command(
    command: &str,
    #[cfg_attr(not(windows), allow(unused_variables))] workspace: &WorkspaceEnv,
    #[cfg_attr(not(windows), allow(unused_variables))] cwd: Option<&str>,
) -> Result<Command, String> {
    #[cfg(windows)]
    if let WorkspaceEnv::Wsl { distro } = workspace {
        validate_wsl_distro_name(distro)?;
        let mut cmd = Command::new("wsl.exe");
        cmd.arg("-d").arg(distro);
        if let Some(cwd) = cwd.filter(|s| !s.is_empty()) {
            cmd.arg("--cd").arg(cwd);
        }
        cmd.arg("--exec").arg("sh").arg("-lc").arg(command);
        return Ok(cmd);
    }
    #[cfg(unix)]
    {
        let mut cmd = Command::new("/bin/sh");
        cmd.arg("-c").arg(command);
        for (key, value) in crate::modules::workspace::appimage_env_overrides() {
            match value {
                Some(v) => {
                    cmd.env(key, v);
                }
                None => {
                    cmd.env_remove(key);
                }
            }
        }
        Ok(cmd)
    }
    #[cfg(windows)]
    {
        let shell = crate::modules::pty::shell_init::windows_shell_path();
        let mut cmd = Command::new(&shell);
        let is_cmd = shell
            .file_name()
            .and_then(|s| s.to_str())
            .map(|s| s.eq_ignore_ascii_case("cmd.exe"))
            .unwrap_or(false);
        if is_cmd {
            cmd.arg("/C").arg(command);
        } else {
            cmd.arg("-NoProfile").arg("-Command").arg(command);
        }
        Ok(cmd)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[cfg(unix)]
    fn run(cmd: &str, timeout_secs: u64) -> CommandOutput {
        run_blocking_inner(
            cmd.into(),
            None,
            WorkspaceEnv::Local,
            Duration::from_secs(timeout_secs),
        )
        .expect("run")
    }

    #[cfg(unix)]
    #[test]
    fn run_blocking_captures_stdout_and_zero_exit() {
        let out = run("printf 'hello\\n'", 5);
        assert_eq!(out.stdout, "hello\n");
        assert_eq!(out.exit_code, Some(0));
        assert!(!out.timed_out);
        assert!(!out.truncated);
    }

    #[cfg(unix)]
    #[test]
    fn run_blocking_captures_stderr_and_nonzero_exit() {
        let out = run("printf 'oops\\n' >&2; exit 3", 5);
        assert!(out.stderr.contains("oops"));
        assert_eq!(out.exit_code, Some(3));
    }

    #[cfg(unix)]
    #[test]
    fn run_blocking_times_out_long_running_command() {
        let out = run("sleep 10", 1);
        assert!(out.timed_out);
        assert_eq!(out.exit_code, None);
    }

    #[cfg(unix)]
    #[test]
    fn run_blocking_truncates_huge_output() {
        let big = MAX_OUTPUT_BYTES + 4096;
        let out = run(&format!("head -c {big} /dev/zero"), 10);
        assert!(out.truncated);
        assert!(out.stdout.len() <= MAX_OUTPUT_BYTES);
    }

    #[cfg(unix)]
    #[test]
    fn build_oneshot_command_uses_sh_minus_c_on_unix() {
        let cmd = build_oneshot_command("echo hi", &WorkspaceEnv::Local, None).unwrap();
        assert_eq!(cmd.get_program(), "/bin/sh");
        let args: Vec<_> = cmd.get_args().collect();
        assert_eq!(args, vec!["-c", "echo hi"]);
    }

    #[test]
    fn run_blocking_timeout_kills_grandchild_tree() {
        let dir = tempfile::tempdir().expect("tempdir");
        let marker = dir.path().join("late.txt");
        let cwd = dir.path().to_string_lossy().into_owned();
        #[cfg(unix)]
        let script = "(printf ready > ready.txt; sleep 3; printf bad > late.txt) & sleep 10";
        #[cfg(windows)]
        let script = "cmd.exe /C \"echo ready > ready.txt & ping -n 4 127.0.0.1 > nul & echo bad > late.txt\"";

        let out = run_blocking_inner(
            script.into(),
            Some(cwd),
            WorkspaceEnv::Local,
            Duration::from_secs(2),
        )
        .expect("run");
        assert!(dir.path().join("ready.txt").exists(), "grandchild did not start");
        assert!(out.timed_out);
        std::thread::sleep(Duration::from_millis(3500));
        assert!(
            !marker.exists(),
            "grandchild wrote marker after foreground shell timeout"
        );
    }

    #[test]
    fn shell_state_kill_all_terminates_background_trees() {
        let dir = tempfile::tempdir().expect("tempdir");
        let marker = dir.path().join("late_state.txt");
        let cwd = dir.path().to_string_lossy().into_owned();
        #[cfg(unix)]
        let script = "(printf ready > ready.txt; sleep 3; printf bad > late_state.txt) & sleep 30";
        #[cfg(windows)]
        let script = "cmd.exe /C \"echo ready > ready.txt & ping -n 4 127.0.0.1 > nul & echo bad > late_state.txt\"";

        let proc = background::spawn(script.into(), Some(cwd), WorkspaceEnv::Local).expect("spawn");
        let state = ShellState::default();
        state.bg.write().unwrap().insert(1, proc.clone());
        let deadline = std::time::Instant::now() + Duration::from_secs(5);
        while !dir.path().join("ready.txt").exists() {
            assert!(std::time::Instant::now() < deadline, "grandchild did not start");
            std::thread::sleep(Duration::from_millis(25));
        }
        state.kill_all();

        std::thread::sleep(Duration::from_millis(3500));
        assert!(
            !marker.exists(),
            "grandchild survived ShellState::kill_all"
        );
    }

    #[test]
    fn cancel_run_terminates_active_foreground_grandchild_tree_promptly() {
        let dir = tempfile::tempdir().expect("tempdir");
        let marker = dir.path().join("late_cancel.txt");
        let cwd = dir.path().to_string_lossy().into_owned();
        #[cfg(unix)]
        let script = "(printf ready > ready.txt; sleep 3; printf bad > late_cancel.txt) & sleep 30";
        #[cfg(windows)]
        let script = "cmd.exe /C \"echo ready > ready.txt & ping -n 4 127.0.0.1 > nul & echo bad > late_cancel.txt\"";

        let state = Arc::new(ShellState::default());
        let lease = state.start_run("main", "req-active").expect("start_run");
        let cancel = lease.cancel.clone();
        let worker_cwd = cwd.clone();
        let worker = std::thread::spawn(move || {
            let _lease = lease;
            run_blocking_cancellable(
                script.into(),
                Some(worker_cwd),
                WorkspaceEnv::Local,
                Duration::from_secs(30),
                Some(&cancel),
            )
        });

        let deadline = std::time::Instant::now() + Duration::from_secs(5);
        while !dir.path().join("ready.txt").exists() {
            assert!(std::time::Instant::now() < deadline, "grandchild did not start");
            std::thread::sleep(Duration::from_millis(25));
        }

        let cancel_at = std::time::Instant::now();
        state.cancel_run("main", "req-active");
        let res = worker.join().expect("worker join");
        assert!(cancel_at.elapsed() < Duration::from_secs(3), "cancel did not terminate promptly");
        assert_eq!(res.err().as_deref(), Some(CANCELLED_ERROR));

        std::thread::sleep(Duration::from_millis(3500));
        assert!(!marker.exists(), "grandchild survived foreground cancel");
    }

    #[test]
    fn cancel_before_spawn_prevents_execution_and_scopes_by_window_and_id() {
        let dir = tempfile::tempdir().expect("tempdir");
        let marker = dir.path().join("never.txt");
        let cwd = dir.path().to_string_lossy().into_owned();
        #[cfg(unix)]
        let script = "printf ran > never.txt";
        #[cfg(windows)]
        let script = "cmd.exe /C \"echo ran > never.txt\"";

        let state = ShellState::default();
        // Pre-cancel before start_run registers the request.
        state.cancel_run("main", "req-early");
        // Foreign window / request ID must not affect another run.
        let other_lease = state.start_run("main", "req-other").expect("other");
        state.cancel_run("settings", "req-other");
        state.cancel_run("main", "req-unrelated");
        assert!(!other_lease.cancel.is_cancelled());

        let early_lease = state.start_run("main", "req-early").expect("early");
        assert!(early_lease.cancel.is_cancelled());
        let res = run_blocking_cancellable(
            script.into(),
            Some(cwd),
            WorkspaceEnv::Local,
            Duration::from_secs(5),
            Some(&early_lease.cancel),
        );
        assert_eq!(res.err().as_deref(), Some(CANCELLED_ERROR));
        assert!(!marker.exists(), "pre-cancelled command must not spawn");
    }
    #[test]
    fn registry_lease_cleanup_and_window_cancellation_are_isolated() {
        let state = ShellState::default();
        let main = state.start_run("main", "same").unwrap();
        let settings = state.start_run("settings", "same").unwrap();
        assert!(state.start_run("main", "same").is_err());
        state.cancel_window("main");
        assert!(main.cancel.is_cancelled());
        assert!(!settings.cancel.is_cancelled());
        drop(main);
        assert_eq!(state.runs.lock().unwrap().active.len(), 1);
        drop(settings);
        assert!(state.runs.lock().unwrap().active.is_empty());
    }

    #[test]
    fn queued_native_seal_blocks_delayed_admission_and_waits_for_lease_drop() {
        let state = ShellState::default();
        let lease = state.start_run("main", "queued-native").unwrap();
        assert!(!state.seal_run("main", "queued-native").unwrap());
        assert!(lease.cancel.is_cancelled());
        assert!(state.start_run("main", "queued-native").is_err());
        drop(lease);
        assert!(state.seal_run("main", "queued-native").unwrap());
        assert!(state.start_run("old-window", "queued-native").is_err());
    }

    #[test]
    fn queued_native_seal_does_not_kill_foreign_window_or_forget_active_shutdown() {
        let state = ShellState::default();
        let lease = state.start_run("other", "queued-foreign").unwrap();
        assert!(!state.seal_run("main", "queued-foreign").unwrap());
        assert!(!lease.cancel.is_cancelled());
        state.kill_all();
        assert!(lease.cancel.is_cancelled());
        assert!(!state.seal_run("main", "queued-foreign").unwrap());
        drop(lease);
        assert!(state.seal_run("main", "queued-foreign").unwrap());
    }

    #[test]
    fn queued_native_seal_never_evicts_revocations_at_capacity() {
        let state = ShellState::default();
        for i in 0..MAX_SEALED_RUNS {
            assert!(state.seal_run("main", &format!("sealed-{i}")).unwrap());
        }
        assert!(state.seal_run("main", "one-too-many").is_err());
        assert!(state.start_run("main", "sealed-0").is_err());
    }

}
