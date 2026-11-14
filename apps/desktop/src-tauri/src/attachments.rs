use base64::{engine::general_purpose::STANDARD, Engine};
use std::{
    io::Write,
    path::{Path, PathBuf},
};
const MAX_BYTES: usize = 12 * 1024 * 1024;

pub(crate) fn store_attachment(root: &Path, name: &str, data: &str) -> Result<PathBuf, String> {
    if name.is_empty()
        || name.len() > 255
        || name
            .chars()
            .any(|c| c.is_control() || matches!(c, '/' | '\\' | ':' | '<' | '>' | '"'))
    {
        return Err("Invalid attachment name".into());
    }
    let lower = name.to_lowercase();
    if lower.starts_with(".env")
        || [
            "api_keys.json",
            "bootstrap-key.json",
            "google_token.json",
            "secrets.json",
            "secrets.yaml",
            "secrets.yml",
            "credentials.json",
            "id_rsa",
            "id_ed25519",
            "service-account",
        ]
        .iter()
        .any(|prefix| lower.starts_with(prefix))
    {
        return Err("Credential files cannot be attached".into());
    }
    let extension = if lower == "dockerfile" {
        "txt"
    } else {
        Path::new(&lower)
            .extension()
            .and_then(|s| s.to_str())
            .unwrap_or("")
    };
    if ![
        "sh", "zsh", "bash", "config", "conf", "gif", "bmp", "txt", "md", "json", "yaml", "yml",
        "toml", "py", "js", "jsx", "ts", "tsx", "rs", "go", "java", "c", "cpp", "h", "hpp", "html",
        "css", "csv", "log", "ini", "pdf", "docx", "png", "jpg", "jpeg", "webp", "mp3", "wav",
        "m4a", "mp4", "mov",
    ]
    .contains(&extension)
    {
        return Err("Unsupported attachment type".into());
    }
    if data.len() > MAX_BYTES.div_ceil(3) * 4 {
        return Err("Attachment exceeds 12 MiB".into());
    }
    let bytes = STANDARD
        .decode(data)
        .map_err(|_| "Invalid attachment data")?;
    if bytes.is_empty() || bytes.len() > MAX_BYTES {
        return Err("Attachment must contain 1 byte to 12 MiB".into());
    }
    let directory = root.join(".cache/attachments");
    std::fs::create_dir_all(&directory).map_err(|e| e.to_string())?;
    let root = root.canonicalize().map_err(|e| e.to_string())?;
    let directory = directory.canonicalize().map_err(|e| e.to_string())?;
    if !directory.starts_with(&root) {
        return Err("Attachment storage is outside the project".into());
    }
    let mut file = tempfile::Builder::new()
        .prefix("attachment-")
        .suffix(&format!(".{extension}"))
        .tempfile_in(directory)
        .map_err(|e| e.to_string())?;
    file.write_all(&bytes).map_err(|e| e.to_string())?;
    let (_, path) = file.keep().map_err(|e| e.to_string())?;
    Ok(path)
}

#[tauri::command]
pub async fn import_attachment(
    window: tauri::WebviewWindow,
    name: String,
    data: String,
) -> Result<serde_json::Value, String> {
    if window.label() != "main" {
        return Err("Attachments require the main application".into());
    }
    let root = crate::bridge::repository()?;
    let path = tauri::async_runtime::spawn_blocking(move || store_attachment(&root, &name, &data))
        .await
        .map_err(|e| e.to_string())??;
    Ok(serde_json::json!({"path":path}))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn imported_files_are_unique_local_and_bounded() {
        let root = tempfile::tempdir().unwrap();
        let a = store_attachment(root.path(), "note.txt", "aGVsbG8=").unwrap();
        let b = store_attachment(root.path(), "note.txt", "aGVsbG8=").unwrap();
        assert_ne!(a, b);
        assert_eq!(std::fs::read(a).unwrap(), b"hello");
        for name in [
            "../private.txt",
            "C:\\secret.txt",
            ".env",
            "api_keys.json",
            "bad.exe",
        ] {
            assert!(store_attachment(root.path(), name, "aGVsbG8=").is_err());
        }
        assert!(store_attachment(root.path(), "note.txt", "?").is_err());
        assert!(store_attachment(root.path(), "note.txt", "").is_err());
        assert!(store_attachment(root.path(), "secrets.json.txt", "aGVsbG8=").is_err());
        assert!(store_attachment(
            root.path(),
            "note.txt",
            &"A".repeat(MAX_BYTES.div_ceil(3) * 4 + 4)
        )
        .is_err());
    }
}
