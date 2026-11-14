#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
mod bridge;
mod attachments;
mod native_ui;
use attachments::import_attachment;
#[cfg(windows)]
mod job;
use bridge::Bridge;
use std::sync::Mutex;
use tauri::Emitter;
use serde_json::{json, Value};
use tauri::{
    tray::TrayIconBuilder,
    Manager, WebviewUrl, WebviewWindowBuilder,
};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, ShortcutState};

fn qa_profile_identifier(profile: Option<&str>) -> Result<String, &'static str> {
    let base = "com.mrcrous.jarvis.qa";
    match profile {
        None => Ok(base.into()),
        Some(value) if value.len() == 32 && value.bytes().all(|c| c.is_ascii_hexdigit()) =>
            Ok(format!("{base}.{value}")),
        Some(_) => Err("JARVIS_QA_PROFILE must contain exactly 32 hexadecimal characters"),
    }
}

#[cfg(test)]
mod qa_profile_tests {
    use super::qa_profile_identifier;

    #[test]
    fn existing_qa_profile_remains_the_default() {
        assert_eq!(qa_profile_identifier(None).unwrap(), "com.mrcrous.jarvis.qa");
    }

    #[test]
    fn a_valid_uuid_suffix_owns_a_distinct_profile() {
        let profile = "0123456789abcdef0123456789abcdef";
        assert_eq!(qa_profile_identifier(Some(profile)).unwrap(),
            format!("com.mrcrous.jarvis.qa.{profile}"));
    }

    #[test]
    fn malformed_profile_suffixes_are_rejected() {
        for profile in ["", "abc", "../0123456789abcdef0123456789ab", "0123456789abcdef0123456789abcdeg"] {
            assert!(qa_profile_identifier(Some(profile)).is_err());
        }
    }
}

#[derive(Default)]
struct QuitState {
    next_id: u64,
    pending: Option<u64>,
    authorized: bool,
}

fn request_quit(app: &tauri::AppHandle) {
    let id = {
        let state = app.state::<Mutex<QuitState>>();
        let Ok(mut state) = state.lock() else { return; };
        if let Some(id) = state.pending { id } else {
            state.next_id += 1;
            let id = state.next_id;
            state.pending = Some(id);
            id
        }
    };
    if let Some(window) = app.get_webview_window("main") {
        if std::env::var("JARVIS_DESKTOP_TEST").is_err() {
            let _ = window.unminimize(); let _ = window.show(); let _ = window.set_focus();
        }
        if let Err(error) = window.emit("jarvis:quit-requested", id) {
            eprintln!("Could not request guarded quit: {error}");
        }
    }
}

#[tauri::command]
fn reveal_quit_window(window: tauri::WebviewWindow) -> Result<(), String> {
    if window.label() != "main" { return Err("Quit dialogs belong to the main window".into()); }
    if std::env::var("JARVIS_DESKTOP_TEST").is_ok() { return Ok(()); }
    window.unminimize().map_err(|error| error.to_string())?;
    window.show().map_err(|error| error.to_string())?;
    window.set_focus().map_err(|error| error.to_string())
}
#[tauri::command]
fn request_app_quit(window: tauri::WebviewWindow, app: tauri::AppHandle) -> Result<(), String> {
    if window.label() != "main" { return Err("Quit requests are restricted to the main window".into()); }
    request_quit(&app);
    Ok(())
}

#[tauri::command]
fn model_download_quit_snapshot(window: tauri::WebviewWindow, app: tauri::AppHandle) -> Result<Vec<u64>, String> {
    if window.label() != "main" { return Err("Download quit checks belong to the main window".into()); }
    app.state::<jarvis_workbench_core::modules::model_downloads::ModelDownloadsState>().quit_snapshot()
}

#[tauri::command]
fn complete_quit(window: tauri::WebviewWindow, app: tauri::AppHandle, id: u64, approved: bool, downloads: Vec<u64>) -> Result<(), String> {
    if window.label() != "main" { return Err("Quit authorization is restricted to the main window".into()); }
    {
        let state = app.state::<Mutex<QuitState>>();
        let mut state = state.lock().map_err(|_| "Quit protection is unavailable")?;
        if state.pending != Some(id) { return Err("Quit request has expired".into()); }
        state.pending = None;
        if approved {
            app.state::<jarvis_workbench_core::modules::model_downloads::ModelDownloadsState>().authorize_quit(&downloads)?;
        }
        state.authorized = approved;
    }
    if approved { app.exit(0); }
    Ok(())
}
#[tauri::command]
async fn backend(
    method: String,
    params: Value,
    state: tauri::State<'_, Bridge>,
    app: tauri::AppHandle,
) -> Result<Value, Value> {
    let locale = if method == "settings.locale" { params.get("locale").and_then(Value::as_str).map(str::to_owned) } else { None };
    let result = state.request(method, params).await?;
    if let Some(locale) = locale {
        app.state::<native_ui::NativeUi>().apply(&app, &locale).map_err(native_ui::locale_error)?;
    }
    Ok(result)
}

#[tauri::command]
async fn open_external_url(window: tauri::WebviewWindow, app: tauri::AppHandle, url: String) -> Result<(), String> {
    use tauri_plugin_opener::OpenerExt;
    if window.label() != "main" || url.len() > 8192 { return Err("Link is not allowed".into()); }
    let parsed = tauri::Url::parse(&url).map_err(|_| "Invalid link")?;
    if !matches!(parsed.scheme(), "https" | "http") || parsed.host_str().is_none() || !parsed.username().is_empty() || parsed.password().is_some() {
        return Err("Only HTTP/HTTPS links without credentials are allowed".into());
    }
    if std::env::var("JARVIS_DESKTOP_TEST").is_ok() { return Err("External opening is disabled in QA".into()); }
    app.opener().open_url(parsed.to_string(), None::<&str>).map_err(|e| e.to_string())
}

#[tauri::command]
async fn show_orb(app: tauri::AppHandle) -> Result<(), String> {
    if let Some(window) = app.get_webview_window("orb") {
        window.show().map_err(|e| e.to_string())?;
        return Ok(());
    }
    WebviewWindowBuilder::new(&app, "orb", WebviewUrl::App("index.html?orb=1".into()))
        .title(app.state::<native_ui::NativeUi>().orb_title())
        .inner_size(280.0, 330.0)
        .min_inner_size(240.0, 300.0)
        .decorations(false)
        .transparent(true)
        .visible(std::env::var("JARVIS_DESKTOP_TEST").is_err())
        .always_on_top(true)
        .build()
        .map_err(|e| e.to_string())?;
    Ok(())
}
#[tauri::command]
async fn fullscreen(window: tauri::WebviewWindow, enabled: Option<bool>) -> Result<bool, String> {
    if window.label() != "main" {
        return Err("Fullscreen is available only in the main window".into());
    }
    let enabled = enabled.unwrap_or(!window.is_fullscreen().map_err(|e| e.to_string())?);
    window.set_fullscreen(enabled).map_err(|e| e.to_string())?;
    Ok(enabled)
}
fn action(app: &tauri::AppHandle, action: &str) {
    let bridge = app.state::<Bridge>().inner().clone();
    let action = action.to_string();
    tauri::async_runtime::spawn(async move {
        let _ = bridge
            .request("control".into(), json!({"action":action}))
            .await;
    });
}
#[cfg(any(windows, test))]
mod notification_hook {
    use std::ffi::OsString;

    #[derive(Debug, PartialEq, Eq)]
    pub enum Agent { Claude, Codex, Gemini, Pi }
    impl Agent {
        pub fn as_str(&self) -> &'static str {
            match self { Self::Claude => "claude", Self::Codex => "codex", Self::Gemini => "gemini", Self::Pi => "pi" }
        }
    }
    #[derive(Debug, PartialEq, Eq)]
    pub enum Event { Working, Attention, Finished }
    impl Event {
        pub fn as_str(&self) -> &'static str {
            match self { Self::Working => "working", Self::Attention => "attention", Self::Finished => "finished" }
        }
    }
    #[derive(Debug, PartialEq, Eq)]
    pub enum Mode { Gui, Notify(Option<(Agent, Event)>) }

    /// Only an exact typed notification may emit a terminal marker; OS arguments need not be Unicode.
    pub fn parse(args: impl IntoIterator<Item = OsString>) -> Mode {
        let mut args = args.into_iter();
        let _executable = args.next();
        if args.next().as_deref() != Some(std::ffi::OsStr::new("__terax_notify")) { return Mode::Gui; }
        let agent = args.next();
        let event = args.next();
        if args.next().is_some() { return Mode::Notify(None); }
        let agent = match agent.as_ref().and_then(|value| value.to_str()) {
            Some("claude") => Agent::Claude, Some("codex") => Agent::Codex,
            Some("gemini") => Agent::Gemini, Some("pi") => Agent::Pi,
            _ => return Mode::Notify(None),
        };
        let event = match event.as_ref().and_then(|value| value.to_str()) {
            Some("working") => Event::Working, Some("attention") => Event::Attention,
            Some("finished") => Event::Finished, _ => return Mode::Notify(None),
        };
        Mode::Notify(Some((agent, event)))
    }

    #[cfg(test)]
    mod tests {
        use super::*;
        fn args(values: &[&str]) -> Vec<OsString> { values.iter().map(OsString::from).collect() }
        #[test]
        fn normal_hooks_keep_the_existing_wire_values() {
            for agent in ["claude", "codex", "gemini", "pi"] {
                for event in ["working", "attention", "finished"] {
                    let Mode::Notify(Some((parsed_agent, parsed_event))) = parse(args(&["jarvis", "__terax_notify", agent, event])) else { panic!("valid hook rejected") };
                    assert_eq!(parsed_agent.as_str(), agent); assert_eq!(parsed_event.as_str(), event);
                }
            }
        }
        #[test]
        fn unknown_missing_extra_and_control_arguments_cannot_emit_markers() {
            for values in [vec!["jarvis", "__terax_notify"], vec!["jarvis", "__terax_notify", "other", "working"],
                vec!["jarvis", "__terax_notify", "codex", "other"], vec!["jarvis", "__terax_notify", "codex", "finished", "extra"],
                vec!["jarvis", "__terax_notify", "codex\x1b]0;injected\x07", "working"], vec!["jarvis", "__terax_notify", "codex", "working\n"]] {
                assert_eq!(parse(args(&values)), Mode::Notify(None));
            }
        }
        #[test]
        fn ordinary_startup_remains_gui() { assert_eq!(parse(args(&["jarvis"])), Mode::Gui); assert_eq!(parse(args(&["jarvis", "--other"])), Mode::Gui); }
        #[cfg(windows)]
        #[test]
        fn non_unicode_windows_arguments_do_not_panic_or_emit() {
            use std::os::windows::ffi::OsStringExt;
            let invalid = OsString::from_wide(&[0xd800]);
            let mut values = args(&["jarvis", "__terax_notify", "codex", "working"]);
            values[2] = invalid.clone(); assert_eq!(parse(values), Mode::Notify(None));
            assert_eq!(parse(vec![invalid]), Mode::Gui);
        }
    }
}

fn main() {
    #[cfg(windows)]
    {
        // argv[0] is discarded; only validated Agent/Event enums reach static marker output.
        // Reviewed false positive: https://doc.rust-lang.org/std/env/fn.args_os.html
        // nosemgrep: rust.lang.security.args-os.args-os
        if let notification_hook::Mode::Notify(notification) = notification_hook::parse(std::env::args_os()) {
            if let Some((agent, event)) = notification {
                jarvis_workbench_core::modules::agent::emit_conout_marker(agent.as_str(), event.as_str());
            }
            use std::io::Write;
            let _=std::io::stdout().write_all(b"{}");
            return;
        }
    }
    // Resolve the installation before configure() constructs native managers or
    // writes its default workspace. setup() runs after those side effects.
    let caller_cwd = std::env::current_dir().ok();
    let mut context = tauri::generate_context!();
    let resource = tauri::utils::platform::resource_dir(
        context.package_info(), &tauri::utils::Env::default(),
    ).ok();
    let root = bridge::initialize_repository_from_resource(resource)
        .expect("Installed Jarvis Python backend was not found");
    std::env::set_current_dir(&root).expect("Could not open the Jarvis installation directory");
    jarvis_workbench_core::modules::utilities::configure_env();
    jarvis_workbench_core::modules::runtime::install();
    let mut builder = jarvis_workbench_core::configure_with_launch_cwd(tauri::Builder::default(), caller_cwd);
    if std::env::var("JARVIS_DESKTOP_TEST").is_err() {
        builder = builder.plugin(tauri_plugin_single_instance::init(|app, _, _| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
        }));
    }
    if std::env::var("JARVIS_DESKTOP_TEST").is_ok() {
        let profile = std::env::var("JARVIS_QA_PROFILE")
            .map(Some)
            .unwrap_or_else(|error| match error {
                std::env::VarError::NotPresent => None,
                std::env::VarError::NotUnicode(_) => panic!("JARVIS_QA_PROFILE must be valid Unicode"),
            });
        context.config_mut().identifier = qa_profile_identifier(profile.as_deref())
            .expect("Invalid isolated QA profile");
        for window in &mut context.config_mut().app.windows {
            window.visible = false;
        }
    }
    let app = builder
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(|app, shortcut, event| {
                    if event.state() != ShortcutState::Pressed {
                        return;
                    }
                    let key = shortcut.to_string();
                    let command = match key.as_str() {
                        "F4" => "mute",
                        "F6" => "voice",
                        "F7" => "toggle_pause",
                        "F8" => "listen",
                        "F9" => "speech_stop",
                        _ => return,
                    };
                    action(app, command);
                })
                .build(),
        )
        .invoke_handler(|invoke| {
            if invoke.message.webview_ref().label() == "main" && jarvis_workbench_core::handles(invoke.message.command()) {
                jarvis_workbench_core::handle(invoke)
            } else {
                let handler: fn(tauri::ipc::Invoke<tauri::Wry>)->bool = tauri::generate_handler![reveal_quit_window, request_app_quit, model_download_quit_snapshot, complete_quit, backend, show_orb, fullscreen, open_external_url, import_attachment, native_ui::native_menu_labels];
                handler(invoke)
            }
        })
        .setup(|app| {
            app.manage(Mutex::new(QuitState::default()));
            let root = bridge::initialize_repository(app.handle()).map_err(std::io::Error::other)?;
            // Native managers and the Python bridge share the same pinned root,
            // including direct portable launches from an unrelated directory.
            std::env::set_current_dir(&root)?;
            jarvis_workbench_core::initialize(app)?;
            app.manage(Bridge::spawn(app.handle()).map_err(std::io::Error::other)?);
            let (native_ui, menu) = native_ui::NativeUi::new(app.handle())?;
            app.manage(native_ui);
            if std::env::var("JARVIS_DESKTOP_TEST").is_err() {
                TrayIconBuilder::new()
                    .icon(tauri::include_image!("icons/icon.png"))
                    .tooltip("Jarvis")
                    .menu(&menu)
                    .on_menu_event(|app, event| match event.id.as_ref() {
                        "show" => {
                            if let Some(window) = app.get_webview_window("main") {
                                if window.is_visible().unwrap_or(false) {
                                    let _ = window.hide();
                                } else {
                                    let _ = window.show();
                                    let _ = window.set_focus();
                                }
                            }
                        }
                        "mic" => action(app, "mute"),
                        "pause" => action(app, "toggle_pause"),
                        "stop" => action(app, "speech_stop"),
                        "quit" => request_quit(app),
                        _ => {}
                    })
                    .build(app)?;
                for shortcut in ["F4", "F6", "F7", "F8", "F9"] {
                    if let Err(error) = app.global_shortcut().register(shortcut) {
                        eprintln!("Shortcut {shortcut} is unavailable: {error}")
                    }
                }
            } else if let Some(window) = app.get_webview_window("main") {
                let _ = window.hide();
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                // Own QA windows use ordinary close for deterministic fixture cleanup.
                if window.label() == "main" && std::env::var("JARVIS_DESKTOP_TEST").is_ok() {
                    let quit = window.state::<Mutex<QuitState>>();
                    if let Ok(mut state) = quit.lock() { state.authorized = true; };
                }
                if window.label() == "main" && std::env::var("JARVIS_DESKTOP_TEST").is_err() {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .build(context)
        .expect("Could not start Jarvis");
    app.run(|app, event| {
        if let tauri::RunEvent::ExitRequested { api, .. } = &event {
            let authorized = app.state::<Mutex<QuitState>>().lock()
                .map(|state| state.authorized).unwrap_or(false);
            if !authorized { api.prevent_exit(); request_quit(app); return; }
        }
        jarvis_workbench_core::event(app, &event);
        if let tauri::RunEvent::Exit = event {
            app.state::<Bridge>().shutdown();
        }
    });
}
