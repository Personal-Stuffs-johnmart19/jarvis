//! Bounded ZIP operations. Never follow archive links or restore Unix permissions.
use super::{
    fs::{blocking, to_canon},
    workspace::{resolve_path, WorkspaceEnv, WorkspaceRegistry},
};
use serde::Serialize;
use std::{
    collections::HashSet,
    fs::{self, File},
    io::{self, Read},
    path::{Component, Path, PathBuf},
};
use tauri::Manager;
use zip::{write::SimpleFileOptions, CompressionMethod, ZipArchive, ZipWriter};
const MAX_ENTRIES: usize = 10000;
const MAX_TOTAL: u64 = 512 * 1024 * 1024;

fn allowed(path: &Path) -> bool {
    // Archive members are relative, but the shared directory rules compare
    // slash-prefixed components. Keep the same credential policy for both
    // archive members and absolute source/destination paths.
    let Some(spelling) = path.to_str() else { return false; };
    let policy_path = if path.is_absolute() { path.to_path_buf() } else {
        PathBuf::from(format!("/{}", spelling.replace('\\', "/")))
    };
    if super::fs::read_policy::readable(&policy_path).is_err() {
        return false;
    }
    path.components().all(|part| {
        let value = part.as_os_str().to_string_lossy().to_lowercase();
        ![
            ".git",
            ".ssh",
            ".aws",
            ".azure",
            ".kube",
            ".gnupg",
            ".codex",
            ".config",
            ".docker",
            "credentials",
            ".credentials",
        ]
        .contains(&value.as_str())
            && !value.starts_with("credentials")
            && !value.starts_with("service-account")
            && !value.starts_with("service_account")
            && !value.ends_with(".dpapi")
            && !value.starts_with(".env")
            && ![
                "api_keys.json",
                "bootstrap-key.json",
                "google_token.json",
                ".npmrc",
                ".pypirc",
                ".netrc",
                "_netrc",
                "secrets.json",
                "secrets.yaml",
                "secrets.yml",
            ]
            .contains(&value.as_str())
            && !["id_rsa", "id_ed25519", "id_dsa", "id_ecdsa"]
                .iter()
                .any(|p| value.starts_with(p))
            && !["pem", "key", "p12", "pfx", "keystore"]
                .iter()
                .any(|ext| value.ends_with(&format!(".{ext}")))
    })
}
fn member(name: &str) -> Result<PathBuf, String> {
    if name.is_empty()
        || name.len() > 1024
        || name.contains('\\')
        || name.contains(':')
        || name.starts_with('/')
        || name.chars().any(char::is_control)
    {
        return Err("Unsafe ZIP member name".into());
    }
    let path = Path::new(name.trim_end_matches('/'));
    if path
        .components()
        .any(|c| !matches!(c, Component::Normal(_)))
    {
        return Err("ZIP paths must be relative without traversal".into());
    }
    // Components normalize './'; inspect the spelling too, before joining.
    for part in name.trim_end_matches('/').split('/') {
        let lower = part.to_lowercase();
        let stem = lower.split('.').next().unwrap_or("");
        if part.is_empty()
            || part == "."
            || part == ".."
            || part.ends_with(['.', ' '])
            || ["con", "prn", "aux", "nul"].contains(&stem)
            || (stem.len() == 4
                && (stem.starts_with("com") || stem.starts_with("lpt"))
                && stem.as_bytes()[3].is_ascii_digit())
        {
            return Err("ZIP member aliases or reserved names are not allowed".into());
        }
    }
    if !allowed(path) {
        return Err("ZIP contains a protected credential or metadata path".into());
    }
    Ok(path.to_path_buf())
}
fn existing(
    registry: &WorkspaceRegistry,
    path: &str,
    workspace: &WorkspaceEnv,
) -> Result<PathBuf, String> {
    let path = fs::canonicalize(resolve_path(path, workspace)).map_err(|e| e.to_string())?;
    if !registry.is_authorized(&path) || !allowed(&path) {
        return Err("Path is protected or outside the authorized workspace".into());
    }
    Ok(path)
}
fn target(
    registry: &WorkspaceRegistry,
    path: &str,
    workspace: &WorkspaceEnv,
) -> Result<PathBuf, String> {
    let raw = resolve_path(path, workspace);
    let parent = fs::canonicalize(raw.parent().ok_or("Destination parent is required")?)
        .map_err(|e| e.to_string())?;
    let tail = raw.file_name().ok_or("Destination name is required")?;
    member(&tail.to_string_lossy())?;
    let result = parent.join(tail);
    if !registry.is_authorized(&parent) || !allowed(&result) {
        return Err("Destination is protected or outside the workspace".into());
    }
    if result.try_exists().map_err(|e| e.to_string())? || fs::symlink_metadata(&result).is_ok() {
        return Err("Destination already exists; choose a new path".into());
    }
    Ok(result)
}
#[derive(Serialize)]
pub struct Entry {
    pub name: String,
    pub size: u64,
    pub directory: bool,
}
fn plan(archive: &mut ZipArchive<File>) -> Result<Vec<Entry>, String> {
    if archive.len() > MAX_ENTRIES {
        return Err("ZIP exceeds 10000 entries".into());
    }
    let mut entries = vec![];
    let mut names = HashSet::new();
    let mut total = 0u64;
    for i in 0..archive.len() {
        let file = archive.by_index(i).map_err(|e| e.to_string())?;
        let name = file.name().map_err(|e| e.to_string())?.into_owned();
        member(&name)?;
        if file.is_symlink()
            || file.encrypted()
            || file
                .unix_mode()
                .is_some_and(|m| ![0, 0o100000, 0o040000].contains(&(m & 0o170000)))
        {
            return Err("ZIP links, special files and encryption are unsupported".into());
        }
        if !names.insert(name.trim_end_matches('/').to_lowercase()) {
            return Err("Duplicate or case-alias ZIP members are not allowed".into());
        }
        total = total.checked_add(file.size()).ok_or("ZIP size overflow")?;
        if total > MAX_TOTAL {
            return Err("ZIP exceeds 512 MiB expanded size".into());
        }
        entries.push(Entry {
            name,
            size: file.size(),
            directory: file.is_dir(),
        });
    }
    Ok(entries)
}
fn open(path: &Path) -> Result<ZipArchive<File>, String> {
    let file = super::fs::ai::open_read_file(path)?;
    open_container(file, path)
}
fn open_container(file: File, planned: &Path) -> Result<ZipArchive<File>, String> {
    validate_opened_source(&file, planned)?;
    if file.metadata().map_err(|e| e.to_string())?.len() > MAX_TOTAL + 8 * 1024 * 1024 {
        return Err("ZIP input exceeds the size limit".into());
    }
    ZipArchive::new(file).map_err(|e| e.to_string())
}
#[derive(Serialize)]
pub struct Listing {
    pub entries: Vec<Entry>,
    pub total_entries: usize,
    pub expanded_bytes: u64,
    pub truncated: bool,
}
#[tauri::command]
pub async fn archive_list(
    app: tauri::AppHandle,
    path: String,
    workspace: Option<WorkspaceEnv>,
) -> Result<Listing, String> {
    blocking(move || {
        let path = existing(
            &app.state::<WorkspaceRegistry>(),
            &path,
            &WorkspaceEnv::from_option(workspace),
        )?;
        let entries = plan(&mut open(&path)?)?;
        let total_entries = entries.len();
        let expanded_bytes = entries.iter().map(|e| e.size).sum();
        Ok(Listing {
            entries: entries.into_iter().take(200).collect(),
            total_entries,
            expanded_bytes,
            truncated: total_entries > 200,
        })
    })
    .await
}
fn extract(path: &Path, destination: &Path) -> Result<usize, String> {
    let mut archive = open(path)?;
    let entries = plan(&mut archive)?;
    let stage = tempfile::Builder::new()
        .prefix(".jarvis-unzip-")
        .tempdir_in(destination.parent().ok_or("Destination parent required")?)
        .map_err(|e| e.to_string())?;
    for (i, entry) in entries.iter().enumerate() {
        let output = stage.path().join(member(&entry.name)?);
        if entry.directory {
            fs::create_dir_all(&output).map_err(|e| e.to_string())?;
            continue;
        }
        fs::create_dir_all(output.parent().ok_or("Invalid ZIP path")?)
            .map_err(|e| e.to_string())?;
        let mut target = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(output)
            .map_err(|e| e.to_string())?;
        let input = archive.by_index(i).map_err(|e| e.to_string())?;
        let written =
            io::copy(&mut input.take(entry.size + 1), &mut target).map_err(|e| e.to_string())?;
        if written != entry.size {
            return Err("ZIP member size does not match its metadata".into());
        }
    }
    if destination.try_exists().map_err(|e| e.to_string())? {
        return Err("Destination appeared during extraction".into());
    }
    fs::rename(stage.path(), destination).map_err(|e| e.to_string())?;
    let _ = stage.keep();
    Ok(entries.len())
}
#[tauri::command]
pub async fn archive_extract(
    app: tauri::AppHandle,
    path: String,
    destination: String,
    workspace: Option<WorkspaceEnv>,
) -> Result<serde_json::Value, String> {
    blocking(move || {
        let workspace = WorkspaceEnv::from_option(workspace);
        let registry = app.state::<WorkspaceRegistry>();
        let path = existing(&registry, &path, &workspace)?;
        let destination = target(&registry, &destination, &workspace)?;
        let count = extract(&path, &destination)?;
        Ok(serde_json::json!({"path":to_canon(destination),"entries":count}))
    })
    .await
}
struct Source {
    path: PathBuf,
    name: String,
    directory: bool,
    size: u64,
}
fn create(sources: &[PathBuf], destination: &Path) -> Result<(usize, usize), String> {
    let mut files = vec![];
    let mut names = HashSet::new();
    let mut size = 0u64;
    let mut skipped = 0usize;
    for source in sources {
        let source = fs::canonicalize(source).map_err(|e| e.to_string())?;
        let base = source
            .file_name()
            .ok_or("Do not archive a drive root")?
            .to_string_lossy()
            .into_owned();
        let mut walker = ignore::WalkBuilder::new(&source);
        walker
            .hidden(false)
            .git_ignore(false)
            .git_global(false)
            .git_exclude(false)
            .parents(false)
            .follow_links(false);
        walker.filter_entry(|entry| allowed(entry.path()));
        for entry in walker.build() {
            let entry = entry.map_err(|e| e.to_string())?;
            if entry.file_type().is_some_and(|t| t.is_symlink()) {
                skipped += 1;
                continue;
            }
            let canonical = fs::canonicalize(entry.path()).map_err(|e| e.to_string())?;
            if !canonical.starts_with(&source) || !allowed(&canonical) {
                return Err("Source changed or escaped the selected path".into());
            }
            let metadata = entry.metadata().map_err(|e| e.to_string())?;
            if !metadata.is_file() && !metadata.is_dir() {
                return Err("Special files cannot be archived".into());
            }
            let relative = entry
                .path()
                .strip_prefix(&source)
                .map_err(|e| e.to_string())?;
            let name = if relative.as_os_str().is_empty() {
                base.clone()
            } else {
                format!("{base}/{}", to_canon(relative))
            };
            member(&name)?;
            if !names.insert(name.to_lowercase()) {
                return Err("Duplicate archive entry".into());
            }
            size = size
                .checked_add(if metadata.is_file() {
                    metadata.len()
                } else {
                    0
                })
                .ok_or("Archive size overflow")?;
            if files.len() >= MAX_ENTRIES || size > MAX_TOTAL {
                return Err("Archive exceeds 10000 entries or 512 MiB".into());
            }
            files.push(Source {
                path: canonical,
                name,
                directory: metadata.is_dir(),
                size: metadata.len(),
            });
        }
    }
    let file =
        tempfile::NamedTempFile::new_in(destination.parent().ok_or("Destination parent required")?)
            .map_err(|e| e.to_string())?;
    let mut zip = ZipWriter::new(file);
    let options = SimpleFileOptions::default()
        .compression_method(CompressionMethod::Deflated)
        .unix_permissions(0o600);
    for source in &files {
        if source.directory {
            zip.add_directory(format!("{}/", source.name), options)
                .map_err(|e| e.to_string())?;
        } else {
            if fs::symlink_metadata(&source.path)
                .map_err(|e| e.to_string())?
                .file_type()
                .is_symlink()
            {
                return Err("Source became a symbolic link".into());
            }
            zip.start_file(&source.name, options)
                .map_err(|e| e.to_string())?;
            let input = super::fs::ai::open_read_file(&source.path)?;
            validate_opened_source(&input, &source.path)?;
            let count =
                io::copy(&mut input.take(source.size + 1), &mut zip).map_err(|e| e.to_string())?;
            if count != source.size {
                return Err("Source changed while archiving".into());
            }
        }
    }
    zip.finish()
        .map_err(|e| e.to_string())?
        .persist_noclobber(destination)
        .map_err(|e| e.to_string())?;
    Ok((files.len(), skipped))
}
fn validate_opened_source(file: &File, planned: &Path) -> Result<(), String> {
    let opened = super::fs::ai::opened_path(file)?;
    if !allowed(&opened) || to_canon(&opened) != to_canon(planned)
        || !file.metadata().map_err(|e| e.to_string())?.is_file()
    {
        return Err("Opened archive source changed or became protected".into());
    }
    Ok(())
}

#[tauri::command]
pub async fn archive_create(
    app: tauri::AppHandle,
    sources: Vec<String>,
    destination: String,
    workspace: Option<WorkspaceEnv>,
) -> Result<serde_json::Value, String> {
    blocking(move || {
        if sources.is_empty() || sources.len() > 100 {
            return Err("Select 1–100 archive sources".into());
        }
        let workspace = WorkspaceEnv::from_option(workspace);
        let registry = app.state::<WorkspaceRegistry>();
        let sources = sources
            .iter()
            .map(|s| existing(&registry, s, &workspace))
            .collect::<Result<Vec<_>, _>>()?;
        let destination = target(&registry, &destination, &workspace)?;
        let (count, skipped) = create(&sources, &destination)?;
        Ok(
            serde_json::json!({"path": to_canon(destination), "entries": count,
            "skipped_links": skipped, "protected_paths_excluded": true}),
        )
    })
    .await
}
#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    #[test]
    fn traversal_aliases_and_secrets_are_rejected() {
        for name in [
            "../escape",
            "/abs",
            "C:/escape",
            "a\\b",
            "a/../b",
            "a/./b",
            ".env",
            ".git/config",
            "NUL.txt",
            "trailing. ",
        ] {
            assert!(member(name).is_err(), "{name}");
        }
    }
    #[test]
    fn shared_credentials_and_opened_source_substitution_are_refused() {
        for path in [
            "config/bridge_token.txt", "config./bridge_token.json.bak",
            "data/network/proxy_cache.json", "data/network./network_preferences.json",
            "api_keys.json.backup", "bootstrap-key.json-copy", "store.dpapi.bak",
            ".cache/terax/control.json", "nested/.codex/auth.json",
        ] {
            assert!(member(path).is_err(), "{path}");
        }
        for path in ["config/bridge_token.ts", "src/proxy_cache.json", "notes.txt"] {
            assert!(member(path).is_ok(), "{path}");
        }
        let dir = tempfile::tempdir().unwrap();
        let planned = dir.path().join("source.txt");
        let substituted = dir.path().join("other.txt");
        fs::write(&planned, "planned").unwrap();
        fs::write(&substituted, "different").unwrap();
        let planned = fs::canonicalize(planned).unwrap();
        assert!(validate_opened_source(&File::open(&planned).unwrap(), &planned).is_ok());
        assert!(validate_opened_source(&File::open(substituted).unwrap(), &planned).is_err());
        let opened = File::open(&planned).unwrap();
        fs::rename(&planned, dir.path().join("moved.txt")).unwrap();
        fs::write(&planned, "replacement at the old spelling").unwrap();
        assert!(validate_opened_source(&opened, &planned).is_err());
    }

    #[test]
    fn zip_container_identity_is_checked_before_listing_or_extraction() {
        let root = tempfile::tempdir().unwrap();
        // Handlers canonicalize selected paths before these private helpers.
        let root_path = fs::canonicalize(root.path()).unwrap();
        let path = root_path.as_path().join("input.zip");
        let mut writer = ZipWriter::new(File::create(&path).unwrap());
        writer.start_file("owned.txt", SimpleFileOptions::default()).unwrap();
        writer.write_all(b"owned fixture").unwrap();
        writer.finish().unwrap();
        let path = fs::canonicalize(&path).unwrap();
        assert_eq!(plan(&mut open(&path).unwrap()).unwrap().len(), 1);
        let file = super::super::fs::ai::open_read_file(&path).unwrap();
        fs::rename(&path, root_path.as_path().join("moved.zip")).unwrap();
        fs::copy(root_path.as_path().join("moved.zip"), &path).unwrap();
        assert!(open_container(file, &path).is_err());
        let protected = root_path.as_path().join("config/bridge_token.txt");
        fs::create_dir(protected.parent().unwrap()).unwrap();
        fs::copy(&path, &protected).unwrap();
        assert!(open(&protected).is_err());
        let output = root_path.as_path().join("output");
        assert!(extract(&protected, &output).is_err());
        assert!(!output.exists());
    }

    #[test]
    fn roundtrip_and_no_overwrite() {
        let root = tempfile::tempdir().unwrap();
        // Handlers canonicalize selected paths before these private helpers.
        let root_path = fs::canonicalize(root.path()).unwrap();
        let input = root_path.as_path().join("input");
        fs::create_dir(&input).unwrap();
        fs::write(input.join("hello.txt"), "hello").unwrap();
        let zip = root_path.as_path().join("test.zip");
        create(&[input], &zip).unwrap();
        assert_eq!(plan(&mut open(&zip).unwrap()).unwrap().len(), 2);
        let output = root_path.as_path().join("output");
        extract(&zip, &output).unwrap();
        assert_eq!(
            fs::read_to_string(output.join("input/hello.txt")).unwrap(),
            "hello"
        );
        assert!(extract(&zip, &output).is_err());
    }
    #[test]
    fn symlink_and_case_alias_zip_are_rejected() {
        let root = tempfile::tempdir().unwrap();
        // Handlers canonicalize selected paths before these private helpers.
        let root_path = fs::canonicalize(root.path()).unwrap();
        let path = root_path.as_path().join("links.zip");
        let mut zip = ZipWriter::new(File::create(&path).unwrap());
        zip.add_symlink("link", "../outside", SimpleFileOptions::default())
            .unwrap();
        zip.finish().unwrap();
        assert!(plan(&mut open(&path).unwrap()).is_err());
        let path = root_path.as_path().join("aliases.zip");
        let mut zip = ZipWriter::new(File::create(&path).unwrap());
        zip.start_file("a.txt", SimpleFileOptions::default())
            .unwrap();
        zip.start_file("A.txt", SimpleFileOptions::default())
            .unwrap();
        zip.finish().unwrap();
        assert!(plan(&mut open(&path).unwrap()).is_err());
    }

    #[test]
    fn bad_zip_leaves_no_destination() {
        let root = tempfile::tempdir().unwrap();
        // Handlers canonicalize selected paths before these private helpers.
        let root_path = fs::canonicalize(root.path()).unwrap();
        let path = root_path.as_path().join("bad.zip");
        let mut zip = ZipWriter::new(File::create(&path).unwrap());
        zip.start_file("../escape.txt", SimpleFileOptions::default())
            .unwrap();
        zip.write_all(b"bad").unwrap();
        zip.finish().unwrap();
        let output = root_path.as_path().join("output");
        assert!(extract(&path, &output).is_err());
        assert!(!output.exists());
        assert!(!root_path.as_path().join("escape.txt").exists());
    }
}
