// Adapted from Terax Apache-2.0: native workbench inside Jarvis, no second app.
pub mod modules;

#[cfg(target_os = "macos")]
use modules::app_menu;
use modules::{
    agent, control, fs, git, history, lsp, net, pty, secrets, shell, vibrancy, workspace,
};
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use tauri::{Emitter, Manager, State};
#[cfg(target_os = "macos")]
use tauri::{PhysicalPosition, WindowEvent};


/// Drained on first read so HMR / re-mounts can't replay the launch dir.
#[derive(Default)]
struct LaunchDir(Mutex<Option<String>>);

/// Drained on first read so HMR / re-mounts can't replay the launch files.
#[derive(Default)]
struct LaunchFiles(Mutex<Vec<String>>);

#[tauri::command]
fn get_launch_dir(state: State<'_, LaunchDir>) -> Option<String> {
    state.0.lock().expect("LaunchDir mutex poisoned").take()
}

#[tauri::command]
fn get_launch_files(state: State<'_, LaunchFiles>) -> Vec<String> {
    std::mem::take(&mut *state.0.lock().expect("LaunchFiles mutex poisoned"))
}

enum LaunchEntry {
    Dir(PathBuf),
    File(PathBuf),
}

#[derive(Default, Debug, PartialEq)]
struct LaunchTarget {
    dir: Option<String>,
    files: Vec<String>,
}

/// First dir arg (else the first file's parent) becomes the workspace; every
/// file arg is opened. Kept free of fs/env access so it stays unit-testable.
fn resolve_launch_target(entries: Vec<LaunchEntry>) -> LaunchTarget {
    let mut dir = None;
    let mut files = Vec::new();
    for entry in entries {
        match entry {
            LaunchEntry::Dir(path) => {
                if dir.is_none() {
                    dir = Some(fs::to_canon(&path));
                }
            }
            LaunchEntry::File(path) => {
                if dir.is_none() {
                    dir = path.parent().map(fs::to_canon);
                }
                files.push(fs::to_canon(&path));
            }
        }
    }
    LaunchTarget { dir, files }
}

fn parse_launch_target(caller_cwd: Option<&Path>) -> LaunchTarget {
    // User-requested open-file targets, never credentials or authentication input.
    // Paths are canonicalized and checked below before workspace registration.
    // nosemgrep: rust.lang.security.args.args
    parse_launch_args(std::env::args().skip(1), caller_cwd)
}

fn parse_launch_args(args: impl IntoIterator<Item = String>, caller_cwd: Option<&Path>) -> LaunchTarget {
    let entries = args.into_iter()
        .filter(|arg| !arg.starts_with('-'))
        .filter_map(|arg| {
            let path = PathBuf::from(arg);
            let path = if path.is_absolute() { path } else {
                caller_cwd.map(|cwd| cwd.join(&path)).unwrap_or(path)
            };
            std::fs::canonicalize(path).ok()
        })
        .filter_map(|path| {
            let meta = std::fs::metadata(&path).ok()?;
            Some(if meta.is_dir() {
                LaunchEntry::Dir(path)
            } else {
                LaunchEntry::File(path)
            })
        })
        .collect();
    resolve_launch_target(entries)
}

#[cfg(test)]
mod launch_arg_tests {
    use super::*;

    #[test]
    fn relative_file_uses_original_caller_directory() {
        let caller = tempfile::tempdir().unwrap();
        let file = caller.path().join("notes.txt");
        std::fs::write(&file, "caller file").unwrap();
        let target = parse_launch_args(vec!["notes.txt".into()], Some(caller.path()));
        assert_eq!(target.files, vec![fs::to_canon(&file.canonicalize().unwrap())]);
        assert_eq!(target.dir, Some(fs::to_canon(&caller.path().canonicalize().unwrap())));
    }

    #[test]
    fn relative_directory_uses_original_caller_directory() {
        let caller = tempfile::tempdir().unwrap();
        let directory = caller.path().join("project");
        std::fs::create_dir(&directory).unwrap();
        let target = parse_launch_args(vec!["project".into()], Some(caller.path()));
        assert_eq!(target.dir, Some(fs::to_canon(&directory.canonicalize().unwrap())));
        assert!(target.files.is_empty());
    }

    #[test]
    fn absolute_paths_flags_and_missing_targets_keep_existing_semantics() {
        let caller = tempfile::tempdir().unwrap();
        let elsewhere = tempfile::tempdir().unwrap();
        let file = elsewhere.path().join("absolute.txt");
        std::fs::write(&file, "absolute file").unwrap();
        std::fs::write(caller.path().join("--ignored"), "flag-like filename").unwrap();
        let target = parse_launch_args(vec!["--ignored".into(), "missing".into(), file.to_string_lossy().into_owned()], Some(caller.path()));
        assert_eq!(target.files, vec![fs::to_canon(&file.canonicalize().unwrap())]);
        assert_eq!(target.dir, Some(fs::to_canon(&elsewhere.path().canonicalize().unwrap())));
    }
}


#[tauri::command]
async fn open_settings_window(app: tauri::AppHandle, tab: Option<String>) -> Result<(), String> {
 app.emit("jarvis:workbench-settings", tab.unwrap_or_else(||"general".into())).map_err(|e|e.to_string())
}



pub fn configure(builder: tauri::Builder<tauri::Wry>) -> tauri::Builder<tauri::Wry> {
    configure_with_launch_cwd(builder, None)
}

/// Resolve explicit relative launch targets against the caller's original CWD,
/// independently of the pinned installation directory used by native managers.
pub fn configure_with_launch_cwd(builder: tauri::Builder<tauri::Wry>, caller_cwd: Option<PathBuf>) -> tauri::Builder<tauri::Wry> {
    let mut launch = parse_launch_target(caller_cwd.as_deref());
    if launch.dir.is_none() {
        let base = std::env::var_os("JARVIS_ROOT").map(PathBuf::from)
            .unwrap_or_else(|| std::env::current_dir().expect("application directory"));
        let directory = if std::env::var("JARVIS_DESKTOP_TEST").is_ok() {
            base.join(".cache/desktop-test/native-workspace")
        } else { base.join("data/workspace") };
        std::fs::create_dir_all(&directory).expect("workspace directory");
        launch.dir=Some(fs::to_canon(&directory));
    }
    let cli_dir = launch.dir.clone();
    workspace::init_launch_cwd(cli_dir.as_deref());
    let control_state = control::ControlState::default();

    let builder = builder.plugin(tauri_plugin_clipboard_manager::init());
    #[cfg(target_os = "macos")]
    let builder = builder
        .menu(app_menu::build)
        .on_menu_event(app_menu::handle_event);
    builder
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_autostart::Builder::new().build())
        .plugin(tauri_plugin_store::Builder::new().build())
        .plugin(tauri_plugin_os::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_opener::init())
        // One async scheme multiplexes every terminal's output; see pty/hub.rs.
        .register_asynchronous_uri_scheme_protocol("terax-pty", pty::stream_protocol)
        .register_asynchronous_uri_scheme_protocol("asset", fs::assets::serve)
        .on_page_load(|webview, payload| {
            if matches!(payload.event(), tauri::webview::PageLoadEvent::Started) {
                if let Some(state) = webview.try_state::<modules::mcp_client::runtime::McpManager>() { state.cancel_window(webview.window().label()); }
                if let Some(grants) = webview.try_state::<fs::assets::AssetGrants>() { grants.revoke_owner(webview.label()); }
                if let Some(state) = webview.try_state::<net::AiStreamState>() {
                    state.cancel_window(webview.window().label());
                }
                if let Some(state) = webview.try_state::<shell::ShellState>() {
                    state.cancel_window(webview.window().label());
                }
                if let Some(state) = webview.try_state::<pty::PtyState>() {
                    state.reset_webview(webview.label());
                }
                // The reloaded page has no decision UI until it announces one.
                if let Some(state) = webview.try_state::<control::ControlState>() {
                    state
                        .hooks()
                        .broker
                        .set_window_ready(webview.label(), false);
                }
            }
        })
        .manage(pty::PtyState::default())
        .manage(net::AiStreamState::default())
        .manage(modules::ai_sessions::SessionFilesState::default())
        .manage(modules::window_presentation::WindowPresentationState::default())
        .manage(control_state)
        .manage(shell::ShellState::default())
        .manage(secrets::SecretsState::default())
        .manage(fs::watch::FsWatchState::default())
        .manage(fs::assets::AssetGrants::default())
        .manage(git::watch::GitWatchState::default())
        .manage(history::HistoryState::default())
        .manage(lsp::LspState::default())
        .manage(fs::supersede::Supersede::default())
        .manage(fs::index::FileIndexState::default())
        .manage({
            let registry = workspace::WorkspaceRegistry::default();
            workspace::bootstrap_registry(&registry);
            if let Some(ref launch_dir) = cli_dir {
                let _ = registry.authorize(launch_dir);
            }
            registry
        })
        .manage(LaunchDir(Mutex::new(cli_dir)))
        .manage(LaunchFiles(Mutex::new(launch.files)))
        .manage(modules::model_downloads::ModelDownloadsState::default())
        .manage(modules::mcp_client::runtime::McpManager::default())

}
pub fn initialize(app: &mut tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    let state=app.state::<control::ControlState>().inner().clone();
    if let Err(error)=control::start(app.handle().clone(),state) { log::warn!("workbench control: {error}"); }
    Ok(())
}

pub fn handles(command: &str) -> bool { matches!(command, "utility_install_cancel" | "utility_install" | "utility_inventory" | "utility_run" | "archive_list" | "archive_create" | "archive_extract" | "pty_open" | "pty_write" | "pty_stream_open" | "pty_set_presentation" | "pty_diagnostics" | "pty_resize" | "pty_close" | "pty_close_all" | "pty_has_foreground_process" | "pty_has_foreground_job" | "pty_shell_name" | "pty_list_shells" | "list_subdirs" | "fs_read_dir" | "fs_read_file" | "fs_write_file" | "fs_stat" | "fs_canonicalize" | "fs_create_file" | "fs_create_dir" | "fs_rename" | "fs_move" | "fs_delete" | "fs_delete_batch" | "fs_copy" | "fs_clipboard_paths" | "fs_watch_add" | "fs_watch_remove" | "lsp_detect" | "lsp_host_pid" | "lsp_resolve_root" | "lsp_spawn" | "lsp_send" | "lsp_kill" | "fs_search" | "fs_list_files" | "fs_grep" | "fs_grep_interactive" | "fs_glob" | "git_resolve_repo" | "git_panel_snapshot" | "git_status" | "git_diff" | "git_diff_content" | "git_stage" | "git_unstage" | "git_discard" | "git_commit" | "git_fetch" | "git_pull_ff_only" | "git_push" | "git_log" | "git_show_commit" | "git_commit_files" | "git_commit_file_diff" | "git_remote_url" | "git_list_branches" | "git_checkout_branch" | "shell_run_command" | "shell_run_cancel" | "shell_session_open" | "shell_session_run" | "shell_session_close" | "shell_bg_spawn" | "shell_bg_logs" | "shell_bg_kill" | "shell_bg_list" | "wsl_list_distros" | "wsl_default_distro" | "wsl_home" | "workspace_authorize" | "workspace_current_dir" | "control_frontend_ready" | "control_respond" | "get_launch_dir" | "get_launch_files" | "open_settings_window" | "agent_enable_hooks" | "agent_hooks_status" | "secrets_get" | "secrets_set" | "secrets_delete" | "secrets_get_all" | "ollama_catalog" | "lm_catalog" | "lm_models" | "workspace_locations" | "lm_ping" | "ai_http_stream" | "ai_http_cancel" | "ai_http_ack" | "ai_session_load" | "ai_session_save" | "ai_session_delete" | "ai_session_finish_migration" | "ai_session_prune_backup" | "history_suggest" | "history_commands" | "history_record" | "history_list" | "window_backdrop_kind" | "window_set_backdrop" | "window_presentation_state" | "model_bundle_descriptors" | "model_fetch_repo_info" | "model_match_bundles" | "model_disk_preflight" | "model_list_installed" | "model_delete" | "model_download_cancel" | "model_download_start" | "mcp_config_get" | "mcp_config_save" | "mcp_status" | "mcp_connect" | "mcp_disconnect" | "mcp_tools" | "mcp_tool_info" | "mcp_call_tool" | "mcp_cancel" | "mcp_config_validate" | "pty_set_colors" | "pty_session_shell" | "ai_read_file" | "ai_read_dir" | "ai_grep" | "ai_glob" | "asset_grant" | "asset_revoke" | "fs_read_project_memory" | "fs_warm_index" | "git_merge_base" | "git_diff_summary" | "git_diff_file" | "git_apply_hunks" | "git_worktree_list" | "git_worktree_add" | "task_project_config" | "task_worktree_list" | "task_worktree_create" | "task_worktree_prepare_archive" | "task_worktree_archive" | "task_worktree_restore" | "task_worktree_disk_usage" | "git_worktree_remove" | "git_worktree_prune" | "git_checkpoint_create" | "git_checkpoint_list" | "git_checkpoint_restore" | "git_checkpoint_prune" | "git_branch_create" | "git_branch_rename" | "git_upstream_info" | "git_watch" | "git_unwatch" | "agent_decisions_ready" | "agent_decision_respond" | "agent_disable_hooks" | "agent_hooks_plan" | "agent_hooks_apply" | "bench_config" | "bench_window" | "bench_exit") }
pub fn handle(invoke: tauri::ipc::Invoke<tauri::Wry>) -> bool {
let handler: fn(tauri::ipc::Invoke<tauri::Wry>) -> bool = tauri::generate_handler![
            modules::utility_install::utility_install,
            modules::utility_install::utility_install_cancel,
            modules::utilities::utility_inventory,
            modules::utilities::utility_run,
            modules::archives::archive_list,
            modules::archives::archive_create,
            modules::archives::archive_extract,
            pty::pty_open,
            pty::pty_write,
            pty::pty_stream_open,
            pty::pty_set_presentation,
            pty::pty_set_colors,
            pty::pty_diagnostics,
            pty::pty_resize,
            pty::pty_close,
            pty::pty_close_all,
            pty::pty_has_foreground_process,
            pty::pty_has_foreground_job,
            pty::pty_shell_name,
            pty::pty_session_shell,
            pty::pty_list_shells,
            fs::tree::list_subdirs,
            fs::tree::fs_read_dir,
            fs::file::fs_read_file,
            fs::ai::ai_read_file,
            fs::ai::ai_read_dir,
            fs::ai::ai_grep,
            fs::ai::ai_glob,
            fs::assets::asset_grant,
            fs::assets::asset_revoke,
            fs::project_memory::fs_read_project_memory,
            fs::file::fs_write_file,
            fs::file::fs_stat,
            fs::file::fs_canonicalize,
            fs::mutate::fs_create_file,
            fs::mutate::fs_create_dir,
            fs::mutate::fs_rename,
            fs::mutate::fs_move,
            fs::mutate::fs_delete,
            fs::mutate::fs_delete_batch,
            fs::copy::fs_copy,
            fs::clipboard::fs_clipboard_paths,
            fs::watch::fs_watch_add,
            fs::watch::fs_watch_remove,
            lsp::lsp_detect,
            lsp::lsp_host_pid,
            lsp::lsp_resolve_root,
            lsp::lsp_spawn,
            lsp::lsp_send,
            lsp::lsp_kill,
            fs::search::fs_search,
            fs::search::fs_warm_index,
            fs::search::fs_list_files,
            fs::grep::fs_grep,
            fs::grep::fs_grep_interactive,
            fs::grep::fs_glob,
            git::commands::git_resolve_repo,
            git::commands::git_panel_snapshot,
            git::commands::git_status,
            git::commands::git_diff,
            git::commands::git_diff_content,
            git::commands::git_stage,
            git::commands::git_unstage,
            git::commands::git_discard,
            git::commands::git_commit,
            git::commands::git_fetch,
            git::commands::git_pull_ff_only,
            git::commands::git_push,
            git::commands::git_log,
            git::commands::git_show_commit,
            git::commands::git_commit_files,
            git::commands::git_commit_file_diff,
            git::commands::git_remote_url,
            git::commands::git_list_branches,
            git::commands::git_checkout_branch,
            git::commands_review::git_merge_base,
            git::commands_review::git_diff_summary,
            git::commands_review::git_diff_file,
            git::commands_review::git_apply_hunks,
            git::commands_review::git_worktree_list,
            git::commands_review::git_worktree_add,
            git::commands_tasks::task_project_config,
            git::commands_tasks::task_worktree_list,
            git::commands_tasks::task_worktree_create,
            git::commands_tasks::task_worktree_prepare_archive,
            git::commands_tasks::task_worktree_archive,
            git::commands_tasks::task_worktree_restore,
            git::commands_tasks::task_worktree_disk_usage,
            git::commands_review::git_worktree_remove,
            git::commands_review::git_worktree_prune,
            git::commands_review::git_checkpoint_create,
            git::commands_review::git_checkpoint_list,
            git::commands_review::git_checkpoint_restore,
            git::commands_review::git_checkpoint_prune,
            git::commands_review::git_branch_create,
            git::commands_review::git_branch_rename,
            git::commands_review::git_upstream_info,
            git::commands_review::git_watch,
            git::commands_review::git_unwatch,
            shell::shell_run_command,
            shell::shell_run_cancel,
            shell::shell_session_open,
            shell::shell_session_run,
            shell::shell_session_close,
            shell::shell_bg_spawn,
            shell::shell_bg_logs,
            shell::shell_bg_kill,
            shell::shell_bg_list,
            workspace::wsl_list_distros,
            workspace::wsl_default_distro,
            workspace::ipc::wsl_home,
            workspace::workspace_authorize,
            workspace::workspace_current_dir,
            control::control_frontend_ready,
            control::control_respond,
            modules::agent_events::agent_decisions_ready,
            modules::agent_events::agent_decision_respond,
            get_launch_dir,
            get_launch_files,
            open_settings_window,
            agent::agent_enable_hooks,
            agent::agent_disable_hooks,
            agent::agent_hooks_plan,
            agent::agent_hooks_apply,
            agent::agent_hooks_status,
            secrets::secrets_get,
            secrets::secrets_set,
            secrets::secrets_delete,
            secrets::secrets_get_all,
            net::ollama_catalog,
            net::lm_catalog,
            net::lm_models,
            workspace::workspace_locations,
            net::lm_ping,
            net::ai_http_stream,
            net::ai_http_cancel,
            net::ai_http_ack,
            modules::ai_sessions::ai_session_load,
            modules::ai_sessions::ai_session_save,
            modules::ai_sessions::ai_session_delete,
            modules::ai_sessions::ai_session_finish_migration,
            modules::ai_sessions::ai_session_prune_backup,
            history::history_suggest,
            history::history_commands,
            history::history_record,
            history::history_list,
            vibrancy::window_backdrop_kind,
            vibrancy::window_set_backdrop,
            modules::window_presentation::window_presentation_state,
            modules::model_downloads::model_bundle_descriptors,
            modules::model_downloads::model_fetch_repo_info,
            modules::model_downloads::model_match_bundles,
            modules::model_downloads::model_disk_preflight,
            modules::model_downloads::model_list_installed,
            modules::model_downloads::model_delete,
            modules::model_downloads::model_download_cancel,
            modules::model_downloads::model_download_start,
            modules::mcp_client::commands::mcp_config_get,
            modules::mcp_client::commands::mcp_config_validate,
            modules::mcp_client::commands::mcp_config_save,
            modules::mcp_client::commands::mcp_status,
            modules::mcp_client::commands::mcp_connect,
            modules::mcp_client::commands::mcp_disconnect,
            modules::mcp_client::commands::mcp_tools,
            modules::mcp_client::commands::mcp_tool_info,
            modules::mcp_client::commands::mcp_call_tool,
            modules::mcp_client::commands::mcp_cancel,

            #[cfg(feature = "bench")]
            modules::bench::bench_config,
            #[cfg(feature = "bench")]
            modules::bench::bench_window,
            #[cfg(feature = "bench")]
            modules::bench::bench_exit,
        ];
handler(invoke)
}
pub fn event(app: &tauri::AppHandle, event: &tauri::RunEvent) {
            match event {
                tauri::RunEvent::WindowEvent {
                    label,
                    event: tauri::WindowEvent::Destroyed,
                    ..
                } => {
                    if let Some(state) = app.try_state::<modules::mcp_client::runtime::McpManager>() { state.cancel_window(&label); }
                    if let Some(grants) = app.try_state::<fs::assets::AssetGrants>() { grants.revoke_owner(&label); }
                    if let Some(state) = app.try_state::<net::AiStreamState>() {
                        state.cancel_window(&label);
                    }
                    if let Some(state) = app.try_state::<shell::ShellState>() {
                        state.cancel_window(&label);
                    }
                    if let Some(state) = app.try_state::<pty::PtyState>() {
                        state.destroy_webview(&label);
                    }
                }
                // Servers exit on stdin EOF, but destructors are not guaranteed
                // on process exit; kill explicitly.
                tauri::RunEvent::Exit => {
                    modules::utility_install::shutdown();
                    if let Some(state) = app.try_state::<modules::mcp_client::runtime::McpManager>() { state.shutdown(); }
                    #[cfg(target_os = "macos")]
                    modules::window_presentation::macos::uninstall();
                    if let Some(state) = app.try_state::<lsp::LspState>() {
                        state.kill_all();
                    }
                    if let Some(state) = app.try_state::<shell::ShellState>() {
                        state.kill_all();
                    }
                    if let Some(state) = app.try_state::<control::ControlState>() {
                        state.shutdown();
                    }
                }
                // macOS delivers "Open With" files here, not as argv (cold and
                // warm start, several at once). Seed the drain-once state and
                // emit; canonicalize so the /tmp -> /private/tmp symlink can't
                // defeat openFileTab's path dedupe against a CLI launch.
                #[cfg(target_os = "macos")]
                tauri::RunEvent::Opened { urls } => {
                    let entries = urls
                        .iter()
                        .filter_map(|u| u.to_file_path().ok())
                        .filter_map(|p| std::fs::canonicalize(p).ok())
                        .filter(|p| p.is_file())
                        .map(LaunchEntry::File)
                        .collect();
                    let target = resolve_launch_target(entries);
                    if target.files.is_empty() {
                        return;
                    }
                    if let Some(dir) = &target.dir {
                        if let Some(registry) = app.try_state::<workspace::WorkspaceRegistry>() {
                            let _ = registry.authorize(dir);
                        }
                        if let Some(state) = app.try_state::<LaunchDir>() {
                            *state.0.lock().expect("LaunchDir mutex poisoned") = Some(dir.clone());
                        }
                    }
                    if let Some(state) = app.try_state::<LaunchFiles>() {
                        *state.0.lock().expect("LaunchFiles mutex poisoned") = target.files.clone();
                    }
                    let _ = app.emit("terax:open-file", target.files);
                }
                _ => {}
            }
}
