use serde_json::Value;
use std::sync::{LazyLock, Mutex};
use tauri::{menu::{Menu, MenuItem}, AppHandle, Manager};

const LABELS: [(&str, &str); 5] = [
    ("show", "Show / hide"),
    ("mic", "Microphone (F4)"),
    ("pause", "Pause / resume (F7)"),
    ("stop", "Stop speech (F9)"),
    ("quit", "Quit Jarvis"),
];
static EN: LazyLock<Value> = LazyLock::new(|| serde_json::from_str(include_str!("../../src/locales/en.json")).expect("valid English UI catalog"));
static RU: LazyLock<Value> = LazyLock::new(|| serde_json::from_str(include_str!("../../src/locales/ru.json")).expect("valid Russian UI catalog"));
fn text<'a>(locale: &str, key: &'a str) -> &'a str {
    let catalog = if locale == "ru" { &*RU } else { &*EN };
    catalog.get(key).and_then(Value::as_str).unwrap_or(key)
}

pub fn locale_error(error: String) -> Value {
    serde_json::json!({"source":"jarvis", "message":"Could not update the native interface: {0}", "values":[error]})
}

pub struct NativeUi {
    items: Vec<MenuItem<tauri::Wry>>,
    locale: Mutex<String>,
}
impl NativeUi {
    pub fn new(app: &AppHandle) -> tauri::Result<(Self, Menu<tauri::Wry>)> {
        let items = LABELS.iter().map(|(id, key)| MenuItem::with_id(app, *id, text("en", key), true, None::<&str>)).collect::<tauri::Result<Vec<_>>>()?;
        let menu = Menu::new(app)?;
        for item in &items { menu.append(item)?; }
        Ok((Self { items, locale: Mutex::new("en".into()) }, menu))
    }
    pub fn apply(&self, app: &AppHandle, locale: &str) -> Result<(), String> {
        if !matches!(locale, "en" | "ru") { return Err("Unsupported interface language".into()); }
        for (item, (_, key)) in self.items.iter().zip(LABELS) {
            item.set_text(text(locale, key)).map_err(|error| error.to_string())?;
        }
        if let Some(orb) = app.get_webview_window("orb") {
            orb.set_title(text(locale, "Jarvis — voice")).map_err(|error| error.to_string())?;
        }
        *self.locale.lock().map_err(|_| "Native locale lock failed")? = locale.into();
        Ok(())
    }
    pub fn orb_title(&self) -> String {
        let locale = self.locale.lock().unwrap_or_else(|error| error.into_inner());
        text(&locale, "Jarvis — voice").into()
    }
    pub fn labels(&self) -> tauri::Result<Vec<String>> {
        self.items.iter().map(MenuItem::text).collect()
    }
}
#[tauri::command]
pub fn native_menu_labels(window: tauri::WebviewWindow, state: tauri::State<'_, NativeUi>) -> Result<Vec<String>, String> {
    if window.label() != "main" { return Err("Native menu is available only from the main window".into()); }
    state.labels().map_err(|error| error.to_string())
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn native_locale_errors_preserve_raw_details_in_owned_envelope() {
        let detail = "raw OS error {0} — ошибка";
        let error = locale_error(detail.into());
        assert_eq!(error["source"], "jarvis");
        assert_eq!(error["values"][0], detail);
        assert_ne!(text("ru", "Could not update the native interface: {0}"), text("en", "Could not update the native interface: {0}"));
    }
    #[test]
    fn native_labels_use_shared_catalogs_and_keep_shortcut_hints() {
        for (_, key) in LABELS {
            assert_eq!(text("en", key), key);
            assert_ne!(text("ru", key), key);
        }
        assert_eq!(text("en", "Jarvis — voice"), "Jarvis — voice");
        assert_eq!(text("ru", "Jarvis — voice"), "Jarvis — голос");
        assert_eq!(text("unknown", "Quit Jarvis"), "Quit Jarvis");
    }
}