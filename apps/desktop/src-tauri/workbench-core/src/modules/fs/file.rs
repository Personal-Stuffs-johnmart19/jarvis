use std::path::Path;
use std::time::UNIX_EPOCH;
use std::{
    fs,
    io::{Read, Write},
};

use serde::Serialize;
use tauri::Emitter;
use tempfile::NamedTempFile;

use super::blocking;
use super::index::FileIndexState;
use super::mutate::note_changed;
use crate::modules::workspace::{resolve_path, WorkspaceEnv};

const MAX_READ_BYTES: u64 = 10 * 1024 * 1024; // 10 MB
/// Ceiling for explicit "open anyway"; mirrored as FORCE_READ_LIMIT in useDocument.ts.
const FORCE_MAX_READ_BYTES: u64 = 50 * 1024 * 1024;
const BINARY_SNIFF_BYTES: usize = 8 * 1024;

#[derive(Serialize)]
#[serde(tag = "kind", rename_all = "lowercase")]
pub enum ReadResult {
    /// Returned only by an explicit allow_missing snapshot request.
    Missing,
    Text {
        content: String,
        size: u64,
        mtime: u64,
    },
    Binary {
        size: u64,
    },
    /// File exceeds MAX_READ_BYTES. UI decides whether to offer "open anyway".
    TooLarge {
        size: u64,
        limit: u64,
    },
}

#[derive(Serialize)]
#[serde(rename_all = "lowercase")]
pub enum StatKind {
    File,
    Dir,
    Symlink,
}

#[derive(Serialize)]
pub struct FileStat {
    pub size: u64,
    pub mtime: u64,
    pub kind: StatKind,
}

fn mtime_millis(meta: &fs::Metadata) -> u64 {
    meta.modified()
        .ok()
        .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

#[tauri::command]
pub async fn fs_read_file(
    path: String,
    workspace: Option<WorkspaceEnv>,
    force: Option<bool>,
    allow_missing: Option<bool>,
) -> Result<ReadResult, String> {
    blocking(move || {
        let workspace = WorkspaceEnv::from_option(workspace);
        read_file_sync(
            &resolve_path(&path, &workspace),
            force.unwrap_or(false),
            allow_missing.unwrap_or(false),
        )
    })
    .await
}

fn read_file_sync(p: &Path, force: bool, allow_missing: bool) -> Result<ReadResult, String> {
    let before = match std::fs::metadata(p) {
        Ok(meta) => meta,
        Err(e) if allow_missing && e.kind() == std::io::ErrorKind::NotFound => {
            return Ok(ReadResult::Missing);
        }
        Err(e) => {
            log::debug!("fs_read_file stat({}) failed: {e}", p.display());
            return Err(e.to_string());
        }
    };
    if !before.is_file() {
        return Err("only regular files can be opened in the editor".into());
    }
    let mut options = fs::OpenOptions::new();
    options.read(true);
    // A FIFO swapped in after the metadata check must not block open().
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NONBLOCK);
    }
    let file = options.open(p).map_err(|e| e.to_string())?;
    let meta = file.metadata().map_err(|e| e.to_string())?;
    if !meta.is_file() {
        return Err("only regular files can be opened in the editor".into());
    }

    let size = meta.len();
    let limit = if force {
        FORCE_MAX_READ_BYTES
    } else {
        MAX_READ_BYTES
    };
    if size > limit {
        return Ok(ReadResult::TooLarge { size, limit });
    }

    let bytes = read_bounded(file, limit).map_err(|e| {
        log::debug!("fs_read_file read({}) failed: {e}", p.display());
        e.to_string()
    })?;
    let size = bytes.len() as u64;
    if size > limit {
        return Ok(ReadResult::TooLarge { size, limit });
    }

    // Null-byte sniff on the first chunk. Not perfect (misses UTF-16 BOM
    // cases) but catches the common "this is a PNG" mistake cheaply.
    let sniff_len = bytes.len().min(BINARY_SNIFF_BYTES);
    if bytes[..sniff_len].contains(&0) {
        return Ok(ReadResult::Binary { size });
    }

    match String::from_utf8(bytes) {
        Ok(content) => Ok(ReadResult::Text {
            content,
            size,
            mtime: mtime_millis(&meta),
        }),
        Err(_) => Ok(ReadResult::Binary { size }),
    }
}

fn read_bounded(reader: impl Read, limit: u64) -> std::io::Result<Vec<u8>> {
    let mut bytes = Vec::new();
    reader.take(limit + 1).read_to_end(&mut bytes)?;
    Ok(bytes)
}

#[derive(Serialize, Clone)]
struct FileWrittenEvent {
    path: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    source: Option<String>,
}

#[cfg(test)]
mod snapshot_tests {
    use super::*;

    #[test]
    fn missing_is_opt_in_and_serializes_as_a_typed_result() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("missing");
        assert!(read_file_sync(&path, false, false).is_err());
        let snapshot = read_file_sync(&path, false, true).unwrap();
        assert!(matches!(snapshot, ReadResult::Missing));
        assert_eq!(
            serde_json::to_value(snapshot).unwrap(),
            serde_json::json!({"kind": "missing"})
        );
    }

    #[test]
    fn snapshot_preserves_full_text_binary_and_size_limits() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("data");
        fs::write(&path, "complete\nUTF-8 text: Україна").unwrap();
        assert!(
            matches!(read_file_sync(&path, false, true).unwrap(), ReadResult::Text { content, .. } if content == "complete\nUTF-8 text: Україна")
        );
        fs::write(&path, b"binary\0data").unwrap();
        assert!(matches!(
            read_file_sync(&path, false, true).unwrap(),
            ReadResult::Binary { .. }
        ));
        fs::File::create(&path)
            .unwrap()
            .set_len(MAX_READ_BYTES + 1)
            .unwrap();
        assert!(matches!(
            read_file_sync(&path, false, true).unwrap(),
            ReadResult::TooLarge {
                limit: MAX_READ_BYTES,
                ..
            }
        ));
    }

    #[test]
    fn existing_unreadable_target_is_an_error_not_missing() {
        let dir = tempfile::tempdir().unwrap();
        // metadata succeeds, but reading a directory as file data must fail.
        assert!(read_file_sync(dir.path(), false, true).is_err());
        assert!(read_file_sync(dir.path(), false, false).is_err());
    }

    #[cfg(windows)]
    #[test]
    fn sharing_denied_read_is_an_error_not_missing() {
        use std::os::windows::fs::OpenOptionsExt;
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("data");
        fs::write(&path, b"original").unwrap();
        let _lock = fs::OpenOptions::new()
            .read(true)
            .share_mode(0)
            .open(&path)
            .unwrap();
        assert!(read_file_sync(&path, false, true).is_err());
        assert!(read_file_sync(&path, false, false).is_err());
    }
}

/// Stage beside the resolved referent; never replace a symlink itself.
fn write_atomic(target: &Path, content: &[u8]) -> std::io::Result<()> {
    let (target, original) = match fs::symlink_metadata(target) {
        Ok(_) => {
            let resolved = fs::canonicalize(target)?;
            let metadata = fs::metadata(&resolved)?;
            if !metadata.is_file() {
                return Err(std::io::Error::new(
                    std::io::ErrorKind::InvalidInput,
                    "target is not a regular file",
                ));
            }
            if metadata.permissions().readonly() {
                return Err(std::io::Error::new(
                    std::io::ErrorKind::PermissionDenied,
                    "target is read-only",
                ));
            }
            (resolved, Some(metadata))
        }
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => (target.to_path_buf(), None),
        Err(e) => return Err(e),
    };
    let parent = target.parent().ok_or_else(|| {
        std::io::Error::new(std::io::ErrorKind::InvalidInput, "path has no parent")
    })?;
    let mut tmp = NamedTempFile::new_in(parent)?;
    tmp.as_file_mut().write_all(content)?;
    #[cfg(unix)]
    if let Some(metadata) = &original {
        tmp.as_file().set_permissions(metadata.permissions())?;
    }
    tmp.as_file_mut().sync_all()?;
    if original.is_some() {
        publish_existing(tmp, &target)?;
    } else {
        // A target created since the initial check belongs to someone else.
        tmp.persist_noclobber(&target).map_err(|e| e.error)?;
    }
    Ok(())
}

#[cfg(not(windows))]
fn publish_existing(tmp: NamedTempFile, target: &Path) -> std::io::Result<()> {
    tmp.persist(target).map_err(|e| e.error)?;
    Ok(())
}

#[cfg(windows)]
fn publish_existing(tmp: NamedTempFile, target: &Path) -> std::io::Result<()> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Storage::FileSystem::ReplaceFileW;
    let wide = |p: &Path| {
        p.as_os_str()
            .encode_wide()
            .chain(Some(0))
            .collect::<Vec<u16>>()
    };
    // Keep a unique backup on the same volume until ReplaceFile's outcome is known.
    let backup_dir = tempfile::Builder::new()
        .prefix(".jarvis-backup-")
        .tempdir_in(target.parent().unwrap())?;
    let backup = backup_dir.path().join("original");
    let target_w = wide(target);
    // ReplaceFile needs exclusive access to the staged file. Close the handle
    // while retaining TempPath cleanup ownership until publication completes.
    let staged = tmp.into_temp_path();
    let staged_w = wide(&staged);
    let backup_w = wide(&backup);
    // Flags 0 requires the original DACL to be merged successfully.
    // SAFETY: these owned, NUL-terminated UTF-16 vectors remain live for this
    // synchronous call. Flags 0 preserve ACL merge errors; reserved pointers
    // are NULL as required. The closed staged handle is owned by TempPath.
    // nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage
    let replaced = unsafe {
        ReplaceFileW(
            target_w.as_ptr(),
            staged_w.as_ptr(),
            backup_w.as_ptr(),
            0,
            std::ptr::null(),
            std::ptr::null(),
        )
    };
    if replaced != 0 {
        if let Err(error) = backup_dir.close() {
            // Publication succeeded; report cleanup separately rather than
            // claiming the original still occupies the destination.
            log::warn!("atomic replacement succeeded but backup cleanup failed: {error}");
        }
        return Ok(());
    }
    let error = std::io::Error::last_os_error();
    recover_failed_replace(target, backup_dir, error)
}

#[cfg(windows)]
fn recover_failed_replace(
    target: &Path,
    backup_dir: tempfile::TempDir,
    error: std::io::Error,
) -> std::io::Result<()> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Storage::FileSystem::{MoveFileExW, MOVEFILE_REPLACE_EXISTING};
    let backup = backup_dir.path().join("original");
    let target_w: Vec<u16> = target.as_os_str().encode_wide().chain(Some(0)).collect();
    let backup_w: Vec<u16> = backup.as_os_str().encode_wide().chain(Some(0)).collect();
    let backup_exists = match backup.try_exists() {
        Ok(exists) => exists,
        Err(inspect) => {
            let retained = backup_dir.keep();
            return Err(std::io::Error::new(error.kind(), format!("replacement failed: {error}; cannot inspect backup: {inspect}; recovery directory retained at {}", retained.display())));
        }
    };
    if backup_exists {
        // SAFETY: both owned UTF-16 path vectors are NUL-terminated and live
        // through this synchronous rename. The source is our retained backup;
        // MOVEFILE_REPLACE_EXISTING restores it to the original target path.
        // nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage
        if unsafe {
            MoveFileExW(
                backup_w.as_ptr(),
                target_w.as_ptr(),
                MOVEFILE_REPLACE_EXISTING,
            )
        } == 0
        {
            let rollback = std::io::Error::last_os_error();
            let retained = backup_dir.keep();
            return Err(std::io::Error::new(error.kind(), format!("replacement failed: {error}; rollback failed: {rollback}; original retained at {}", retained.join("original").display())));
        }
        return Err(std::io::Error::new(
            error.kind(),
            format!("replacement failed: {error}; original restored"),
        ));
    }
    Err(error)
}

#[cfg(test)]
mod atomic_publication_tests {
    use super::*;

    #[test]
    fn new_and_existing_files_publish_exact_bytes() {
        let dir = tempfile::tempdir().unwrap();
        let target = dir.path().join("data");
        write_atomic(&target, b"first\0bytes").unwrap();
        assert_eq!(fs::read(&target).unwrap(), b"first\0bytes");
        write_atomic(&target, b"second\xffbytes").unwrap();
        assert_eq!(fs::read(&target).unwrap(), b"second\xffbytes");
        assert_eq!(fs::read_dir(dir.path()).unwrap().count(), 1);
    }

    #[test]
    fn directory_target_is_unchanged_on_failure() {
        let dir = tempfile::tempdir().unwrap();
        let target = dir.path().join("folder");
        fs::create_dir(&target).unwrap();
        fs::write(target.join("keep"), b"original").unwrap();
        assert!(write_atomic(&target, b"replacement").is_err());
        assert_eq!(fs::read(target.join("keep")).unwrap(), b"original");
    }

    #[cfg(unix)]
    #[test]
    fn preserves_mode_and_symlink_referent_and_rejects_dangling_link() {
        use std::os::unix::fs::{symlink, PermissionsExt};
        let dir = tempfile::tempdir().unwrap();
        let target = dir.path().join("data");
        let link = dir.path().join("link");
        fs::write(&target, b"old").unwrap();
        fs::set_permissions(&target, fs::Permissions::from_mode(0o640)).unwrap();
        symlink(&target, &link).unwrap();
        write_atomic(&link, b"new").unwrap();
        assert!(fs::symlink_metadata(&link)
            .unwrap()
            .file_type()
            .is_symlink());
        assert_eq!(fs::read(&target).unwrap(), b"new");
        assert_eq!(
            fs::metadata(&target).unwrap().permissions().mode() & 0o777,
            0o640
        );
        fs::remove_file(&target).unwrap();
        assert!(write_atomic(&link, b"new").is_err());
        assert!(!target.exists());
        assert!(fs::symlink_metadata(&link)
            .unwrap()
            .file_type()
            .is_symlink());
    }

    #[cfg(windows)]
    #[test]
    fn locked_target_preserves_original_bytes_and_removes_staging() {
        use std::os::windows::fs::OpenOptionsExt;
        let dir = tempfile::tempdir().unwrap();
        let target = dir.path().join("data");
        fs::write(&target, b"original").unwrap();
        let lock = fs::OpenOptions::new()
            .read(true)
            .share_mode(1)
            .open(&target)
            .unwrap();
        assert!(write_atomic(&target, b"replacement").is_err());
        assert_eq!(fs::read(&target).unwrap(), b"original");
        assert_eq!(fs::read_dir(dir.path()).unwrap().count(), 1);
        drop(lock);
    }

    #[cfg(windows)]
    #[test]
    fn windows_symlink_referent_is_preserved_and_dangling_link_rejected() {
        let dir = tempfile::tempdir().unwrap();
        let target = dir.path().join("data");
        let link = dir.path().join("link");
        fs::write(&target, b"old").unwrap();
        if let Err(error) = std::os::windows::fs::symlink_file(&target, &link) {
            if error.raw_os_error() == Some(1314) {
                eprintln!("symlink fixture not exercised: Windows symlink privilege unavailable");
                return;
            }
            panic!("symlink fixture failed: {error}");
        }
        write_atomic(&link, b"new").unwrap();
        assert!(fs::symlink_metadata(&link)
            .unwrap()
            .file_type()
            .is_symlink());
        assert_eq!(fs::read(&target).unwrap(), b"new");
        fs::remove_file(&target).unwrap();
        assert!(write_atomic(&link, b"replacement").is_err());
        assert!(!target.exists());
        assert!(fs::symlink_metadata(&link)
            .unwrap()
            .file_type()
            .is_symlink());
    }

    #[cfg(windows)]
    #[test]
    fn windows_replacement_preserves_protected_dacl() {
        use std::os::windows::ffi::OsStrExt;
        use windows_sys::Win32::Security::{
            GetFileSecurityW, SetFileSecurityW, SetSecurityDescriptorControl,
            DACL_SECURITY_INFORMATION, PROTECTED_DACL_SECURITY_INFORMATION, SE_DACL_PROTECTED,
        };
        fn dacl(path: &[u16]) -> Vec<u8> {
            let mut size = 0;
            // SAFETY: path is the live NUL-terminated fixture path. A NULL
            // descriptor with length 0 requests its required size; size is a
            // valid writable DWORD, checked before allocating the real buffer.
            // nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage
            unsafe {
                GetFileSecurityW(
                    path.as_ptr(),
                    DACL_SECURITY_INFORMATION,
                    std::ptr::null_mut(),
                    0,
                    &mut size,
                );
            }
            assert!(size > 0);
            let mut descriptor = vec![0u8; size as usize];
            assert_ne!(
                unsafe {
                    GetFileSecurityW(
                        path.as_ptr(),
                        DACL_SECURITY_INFORMATION,
                        descriptor.as_mut_ptr().cast(),
                        size,
                        &mut size,
                    )
                },
                0
            );
            descriptor
        }
        let dir = tempfile::tempdir().unwrap();
        let target = dir.path().join("data");
        fs::write(&target, b"old").unwrap();
        let path: Vec<u16> = target.as_os_str().encode_wide().chain(Some(0)).collect();
        let mut descriptor = dacl(&path);
        // Make the fixture ACL explicitly owned by this file, unlike the
        // parent's inherited ACL that a fresh tempfile would receive.
        let acl_offset = u32::from_le_bytes(descriptor[16..20].try_into().unwrap()) as usize;
        let ace_count = u16::from_le_bytes(
            descriptor[acl_offset + 4..acl_offset + 6]
                .try_into()
                .unwrap(),
        );
        let mut ace_offset = acl_offset + 8;
        for _ in 0..ace_count {
            descriptor[ace_offset + 1] &= !0x10; // INHERITED_ACE
            ace_offset += u16::from_le_bytes(
                descriptor[ace_offset + 2..ace_offset + 4]
                    .try_into()
                    .unwrap(),
            ) as usize;
        }
        assert_ne!(
            unsafe {
                SetSecurityDescriptorControl(
                    descriptor.as_mut_ptr().cast(),
                    SE_DACL_PROTECTED,
                    SE_DACL_PROTECTED,
                )
            },
            0
        );
        assert_ne!(
            unsafe {
                SetFileSecurityW(
                    path.as_ptr(),
                    DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
                    descriptor.as_mut_ptr().cast(),
                )
            },
            0
        );
        let original = dacl(&path);
        assert_ne!(
            u16::from_le_bytes([original[2], original[3]]) & SE_DACL_PROTECTED,
            0
        );
        write_atomic(&target, b"new").unwrap();
        assert_eq!(fs::read(&target).unwrap(), b"new");
        let replaced = dacl(&path);
        // Windows may update AUTO_INHERITED bookkeeping. The actual ACL and
        // its inheritance protection must remain identical.
        let acl = |descriptor: &[u8]| {
            let offset = u32::from_le_bytes(descriptor[16..20].try_into().unwrap()) as usize;
            let size =
                u16::from_le_bytes(descriptor[offset + 2..offset + 4].try_into().unwrap()) as usize;
            descriptor[offset..offset + size].to_vec()
        };
        assert_eq!(acl(&replaced), acl(&original));
        assert_eq!(
            u16::from_le_bytes([replaced[2], replaced[3]]) & SE_DACL_PROTECTED,
            SE_DACL_PROTECTED
        );
    }

    #[cfg(windows)]
    #[test]
    fn failed_replace_restores_backup_bytes() {
        let dir = tempfile::tempdir().unwrap();
        let target = dir.path().join("data");
        fs::write(&target, b"partial replacement").unwrap();
        let backup = tempfile::tempdir_in(dir.path()).unwrap();
        fs::write(backup.path().join("original"), b"original bytes").unwrap();
        let error =
            recover_failed_replace(&target, backup, std::io::Error::other("fixture failure"))
                .unwrap_err();
        assert!(error.to_string().contains("original restored"));
        assert_eq!(fs::read(&target).unwrap(), b"original bytes");
        assert_eq!(fs::read_dir(dir.path()).unwrap().count(), 1);
    }

    #[cfg(windows)]
    #[test]
    fn failed_rollback_retains_original_and_reports_recovery_path() {
        use std::os::windows::fs::OpenOptionsExt;
        let dir = tempfile::tempdir().unwrap();
        let target = dir.path().join("data");
        fs::write(&target, b"partial replacement").unwrap();
        let backup = tempfile::tempdir_in(dir.path()).unwrap();
        let backup_path = backup.path().join("original");
        fs::write(&backup_path, b"original bytes").unwrap();
        let lock = fs::OpenOptions::new()
            .read(true)
            .share_mode(1)
            .open(&target)
            .unwrap();
        let error =
            recover_failed_replace(&target, backup, std::io::Error::other("fixture failure"))
                .unwrap_err();
        assert!(error.to_string().contains("rollback failed"));
        assert!(error
            .to_string()
            .contains(&backup_path.display().to_string()));
        assert_eq!(fs::read(&backup_path).unwrap(), b"original bytes");
        assert_eq!(fs::read(&target).unwrap(), b"partial replacement");
        drop(lock);
    }
}

/// Returns the new mtime so the editor can track disk state for conflict
/// detection without a follow-up stat.
#[tauri::command]
pub async fn fs_write_file(
    path: String,
    content: String,
    workspace: Option<WorkspaceEnv>,
    source: Option<String>,
    app: tauri::AppHandle,
    index: tauri::State<'_, FileIndexState>,
) -> Result<u64, String> {
    let index = index.inner().clone();
    let (path, mtime) = blocking(move || {
        let workspace = WorkspaceEnv::from_option(workspace);
        let target = resolve_path(&path, &workspace);
        let original = fs::metadata(&target).ok();
        write_atomic(&target, content.as_bytes()).map_err(|e| {
            log::warn!("fs_write_file({}) failed: {e}", target.display());
            e.to_string()
        })?;

        if original.is_none() {
            note_changed(&index, &target);
        }
        let mtime = fs::metadata(&target).map(|m| mtime_millis(&m)).unwrap_or(0);
        Ok((path, mtime))
    })
    .await?;
    let _ = app.emit("fs:file-written", FileWrittenEvent { path, source });

    Ok(mtime)
}

#[tauri::command]
pub async fn fs_canonicalize(
    path: String,
    workspace: Option<WorkspaceEnv>,
    allow_missing: Option<bool>,
    local_only: Option<bool>,
) -> Result<String, String> {
    validate_link_path(&path, local_only)?;
    blocking(move || {
        let workspace = WorkspaceEnv::from_option(workspace);
        #[cfg(windows)]
        if local_only == Some(true) {
            let canon =
                super::local_file::canonicalize(&super::local_file::open(&path, &workspace)?)?;
            return match &workspace {
                WorkspaceEnv::Wsl { distro } => canonical_wsl_path(&canon, distro),
                WorkspaceEnv::Local => Ok(canon),
            };
        }
        let p = resolve_path(&path, &workspace);
        let canon =
            canonicalize_target(&p, allow_missing.unwrap_or(false)).map_err(|e| e.to_string())?;
        super::canonical_workspace_path(&super::to_canon(&canon), &workspace)
    })
    .await
}

/// Resolve aliases even when several final components have not been created.
/// Only missing entries may be peeled off; denied access and broken aliases
/// cannot establish a safe target and must remain errors.
fn canonicalize_target(path: &Path, allow_missing: bool) -> std::io::Result<std::path::PathBuf> {
    let mut ancestor = path;
    let mut missing = Vec::new();
    loop {
        match fs::canonicalize(ancestor) {
            Ok(mut canonical) => {
                if !missing.is_empty() && !fs::metadata(&canonical)?.is_dir() {
                    return Err(std::io::Error::new(
                        std::io::ErrorKind::NotADirectory,
                        "existing ancestor is not a directory",
                    ));
                }
                for component in missing.into_iter().rev() {
                    canonical.push(component);
                }
                return Ok(canonical);
            }
            Err(error) if allow_missing && error.kind() == std::io::ErrorKind::NotFound => {
                // Windows may normalize a missing `name/..` ancestor during
                // canonicalization. Never carry unresolved parent traversal
                // into the missing-component reconstruction on any platform.
                if ancestor
                    .components()
                    .any(|part| part == std::path::Component::ParentDir)
                {
                    return Err(error);
                }
                // A dangling symlink is not a missing directory. Appending its
                // literal name would lose the unresolved destination.
                match fs::symlink_metadata(ancestor) {
                    Ok(_) => return Err(error),
                    Err(meta_error) if meta_error.kind() == std::io::ErrorKind::NotFound => {}
                    Err(meta_error) => return Err(meta_error),
                }
                let component = ancestor.file_name().ok_or(error)?;
                missing.push(component.to_os_string());
                ancestor = ancestor.parent().ok_or_else(|| {
                    std::io::Error::new(
                        std::io::ErrorKind::InvalidInput,
                        "path has no existing ancestor",
                    )
                })?;
                // Relative leaf paths have an empty parent, meaning the cwd.
                if ancestor.as_os_str().is_empty() {
                    ancestor = Path::new(".");
                }
            }
            Err(error) => return Err(error),
        }
    }
}

#[cfg(test)]
mod canonical_target_tests {
    use super::*;

    #[test]
    fn recursive_missing_target_resolves_existing_alias() {
        let dir = tempfile::tempdir().unwrap();
        let protected = dir.path().join(".ssh");
        let alias = dir.path().join("innocent");
        fs::create_dir(&protected).unwrap();
        #[cfg(unix)]
        std::os::unix::fs::symlink(&protected, &alias).unwrap();
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            let output = std::process::Command::new("cmd")
                .args(["/C", "mklink", "/J"])
                .arg(&alias)
                .arg(&protected)
                .creation_flags(0x08000000)
                .output()
                .unwrap();
            assert!(
                output.status.success(),
                "junction fixture failed: {output:?}"
            );
        }
        let target = alias.join("new").join("more").join("file.txt");
        assert_eq!(
            canonicalize_target(&target, true).unwrap(),
            fs::canonicalize(&protected)
                .unwrap()
                .join("new/more/file.txt")
        );
        assert!(canonicalize_target(&target, false).is_err());
        assert!(!protected.join("new").exists());
    }

    #[test]
    fn existing_targets_and_recursive_creates_keep_canonical_identity() {
        let dir = tempfile::tempdir().unwrap();
        let existing = dir.path().join("file.txt");
        fs::write(&existing, "unchanged").unwrap();
        assert_eq!(
            canonicalize_target(&existing, true).unwrap(),
            fs::canonicalize(&existing).unwrap()
        );
        let new = dir.path().join("one/two/new.txt");
        assert_eq!(
            canonicalize_target(&new, true).unwrap(),
            fs::canonicalize(dir.path())
                .unwrap()
                .join("one/two/new.txt")
        );
        assert!(canonicalize_target(&existing.join("child"), true).is_err());
    }

    #[test]
    fn unresolved_parent_traversal_is_rejected() {
        let dir = tempfile::tempdir().unwrap();
        assert!(canonicalize_target(&dir.path().join("missing/../file"), true).is_err());
    }

    #[cfg(unix)]
    #[test]
    fn dangling_alias_is_rejected() {
        let dir = tempfile::tempdir().unwrap();
        let alias = dir.path().join("alias");
        std::os::unix::fs::symlink(dir.path().join(".ssh/missing"), &alias).unwrap();
        assert!(canonicalize_target(&alias.join("new/file"), true).is_err());
    }
}

#[tauri::command]
pub async fn fs_stat(
    path: String,
    workspace: Option<WorkspaceEnv>,
    local_only: Option<bool>,
) -> Result<FileStat, String> {
    validate_link_path(&path, local_only)?;
    blocking(move || {
        #[cfg(windows)]
        if local_only == Some(true) {
            let file = super::local_file::open(&path, &WorkspaceEnv::from_option(workspace))?;
            let meta = file.metadata().map_err(|e| e.to_string())?;
            if !meta.is_file() && !meta.is_dir() {
                return Err("only regular files and directories can be opened".into());
            }
            return Ok(FileStat {
                size: meta.len(),
                mtime: mtime_millis(&meta),
                kind: if meta.is_dir() {
                    StatKind::Dir
                } else {
                    StatKind::File
                },
            });
        }
        stat_path(path, workspace)
    })
    .await
}

#[cfg(any(windows, test))]
fn canonical_wsl_path(path: &str, distro: &str) -> Result<String, String> {
    for host in ["wsl.localhost", "wsl$"] {
        let prefix = format!("//{host}/{distro}");
        if path
            .get(..prefix.len())
            .is_some_and(|p| p.eq_ignore_ascii_case(&prefix))
        {
            let tail = &path[prefix.len()..];
            if tail.is_empty() {
                return Ok("/".into());
            }
            if tail.starts_with('/') {
                return Ok(tail.into());
            }
        }
    }
    let bytes = path.as_bytes();
    if bytes.len() >= 3 && bytes[0].is_ascii_alphabetic() && &bytes[1..3] == b":/" {
        return Ok(format!(
            "/mnt/{}/{}",
            (bytes[0] as char).to_ascii_lowercase(),
            &path[3..]
        ));
    }
    Err("canonical path is outside the workspace's WSL namespace".into())
}

fn validate_link_path(path: &str, local_only: Option<bool>) -> Result<(), String> {
    if local_only == Some(true)
        && path
            .as_bytes()
            .get(..2)
            .is_some_and(|prefix| prefix.iter().all(|byte| matches!(byte, b'/' | b'\\')))
    {
        return Err("network and device paths are not local file links".into());
    }
    Ok(())
}

fn stat_path(path: String, workspace: Option<WorkspaceEnv>) -> Result<FileStat, String> {
    let workspace = WorkspaceEnv::from_option(workspace);
    let p = resolve_path(&path, &workspace);
    let meta = std::fs::metadata(&p).map_err(|e| e.to_string())?;
    if !meta.is_file() && !meta.is_dir() {
        return Err("only regular files and directories can be opened".into());
    }
    // fs::metadata follows symlinks, so the link check needs symlink_metadata.
    let kind = if std::fs::symlink_metadata(&p)
        .map(|m| m.file_type().is_symlink())
        .unwrap_or(false)
    {
        StatKind::Symlink
    } else if meta.is_dir() {
        StatKind::Dir
    } else {
        StatKind::File
    };
    Ok(FileStat {
        size: meta.len(),
        mtime: mtime_millis(&meta),
        kind,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn file_reads_stop_at_one_byte_over_the_limit() {
        // Even a source whose metadata understated its size cannot grow the
        // read past the sentinel byte used to report TooLarge.
        let bytes = read_bounded(std::io::repeat(b'x').take(1000), 32).unwrap();
        assert_eq!(bytes.len(), 33);
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn devices_and_fifos_never_reach_editor_reads() {
        let dir = tempfile::tempdir().unwrap();
        let fifo = dir.path().join("pipe");
        let name = std::ffi::CString::new(fifo.as_os_str().as_encoded_bytes()).unwrap();
        assert_eq!(unsafe { libc::mkfifo(name.as_ptr(), 0o600) }, 0);
        let linked_device = dir.path().join("zero-link");
        std::os::unix::fs::symlink("/dev/zero", &linked_device).unwrap();
        for path in [
            Path::new("/dev/zero"),
            Path::new("/dev/null"),
            &fifo,
            &linked_device,
        ] {
            assert!(read_file_sync(path, false, false)
                .err()
                .unwrap()
                .contains("regular"));
            assert!(read_file_sync(path, true, false)
                .err()
                .unwrap()
                .contains("regular"));
            assert!(fs_stat(path.to_string_lossy().into_owned(), None, None)
                .await
                .err()
                .unwrap()
                .contains("regular"));
        }
    }

    #[test]
    fn canonical_wsl_paths_keep_the_distro_namespace() {
        assert_eq!(
            canonical_wsl_path("//wsl.localhost/Ubuntu/home/me/target", "Ubuntu").unwrap(),
            "/home/me/target"
        );
        assert_eq!(
            canonical_wsl_path("//wsl$/Ubuntu/home/Target", "Ubuntu").unwrap(),
            "/home/Target"
        );
        assert_eq!(
            canonical_wsl_path("C:/work/target", "Ubuntu").unwrap(),
            "/mnt/c/work/target"
        );
        assert_eq!(
            canonical_wsl_path("//wsl.localhost/Ubuntu", "Ubuntu").unwrap(),
            "/"
        );
        assert!(canonical_wsl_path("//wsl.localhost/Ubuntu-other/file", "Ubuntu").is_err());
        assert!(canonical_wsl_path("//remote/share/file", "Ubuntu").is_err());
    }

    #[cfg(windows)]
    #[tokio::test]
    async fn local_file_links_reject_leaf_and_parent_reparse_points() {
        use std::os::windows::process::CommandExt;
        let dir = tempfile::tempdir().unwrap();
        let target = dir.path().join("target");
        std::fs::create_dir(&target).unwrap();
        std::fs::write(target.join("file"), "safe").unwrap();
        let local = dir.path().join("local-link");
        // NTFS junctions exercise the same reparse guard without requiring
        // the host's optional symlink privilege or Developer Mode.
        for path in [&target, &local] {
            assert!(!path.to_str().unwrap().contains(['%', '"', '\r', '\n']));
        }
        let created = std::process::Command::new("cmd.exe")
            .args(["/D", "/V:OFF", "/C"])
            .raw_arg(format!("mklink /J \"{}\" \"{}\"", local.display(), target.display()))
            .output().unwrap();
        assert!(created.status.success(), "{}", String::from_utf8_lossy(&created.stderr));
        for path in [&local, &local.join("file")] {
            let path = super::super::to_canon(path);
            assert!(fs_stat(path.clone(), None, Some(true))
                .await
                .err()
                .unwrap()
                .contains("reparse"));
            assert!(fs_canonicalize(path, None, None, Some(true))
                .await
                .err()
                .unwrap()
                .contains("reparse"));
        }
        let plain = super::super::to_canon(target.join("file"));
        assert_eq!(
            fs_stat(plain.clone(), None, Some(true)).await.unwrap().size,
            4
        );
        assert_eq!(
            fs_canonicalize(plain.clone(), None, None, Some(true))
                .await
                .unwrap(),
            super::super::to_canon(std::fs::canonicalize(&plain).unwrap())
        );
    }

    #[tokio::test]
    async fn local_file_links_reject_network_paths_before_filesystem_access() {
        for path in [
            r"\\server\share\file",
            "//server/share/file",
            r"/\server/share/file",
            r"\/server\share\file",
            r"\\?\C:\file",
            r"\\.\pipe\name",
            r"/\?\UNC\server\share",
            r"\/.\pipe\name",
        ] {
            let expected = "network and device paths are not local file links";
            assert_eq!(
                fs_stat(path.into(), None, Some(true)).await.err().unwrap(),
                expected
            );
            assert_eq!(
                fs_canonicalize(path.into(), None, None, Some(true))
                    .await
                    .unwrap_err(),
                expected
            );
            assert!(validate_link_path(path, None).is_ok());
        }
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn link_parent_segments_resolve_after_symlinks() {
        let dir = tempfile::tempdir().unwrap();
        let workspace = dir.path().join("workspace");
        let external = dir.path().join("external");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(external.join("build")).unwrap();
        std::fs::write(external.join("main.ts"), "outside").unwrap();
        std::fs::write(workspace.join("main.ts"), "inside").unwrap();
        std::os::unix::fs::symlink(external.join("build"), workspace.join("build")).unwrap();
        let path = workspace
            .join("build/../main.ts")
            .to_string_lossy()
            .into_owned();
        assert_eq!(
            fs_stat(path.clone(), None, Some(true)).await.unwrap().size,
            7
        );
        assert_eq!(
            fs_canonicalize(path, None, None, Some(true)).await.unwrap(),
            super::super::to_canon(std::fs::canonicalize(external.join("main.ts")).unwrap())
        );
    }

    #[test]
    fn read_file_classifies_utf8_as_text() {
        let dir = tempfile::tempdir().unwrap();
        let f = dir.path().join("a.txt");
        std::fs::write(&f, b"hello world").unwrap();
        match read_file_sync(&f, false, false).unwrap() {
            ReadResult::Text {
                content,
                size,
                mtime,
            } => {
                assert_eq!(content, "hello world");
                assert_eq!(size, 11);
                assert!(mtime > 0);
            }
            _ => panic!("expected text"),
        }
    }

    #[test]
    fn read_file_detects_binary_via_null_byte() {
        let dir = tempfile::tempdir().unwrap();
        let f = dir.path().join("a.bin");
        std::fs::write(&f, b"PNG\0\x89image").unwrap();
        assert!(matches!(
            read_file_sync(&f, false, false).unwrap(),
            ReadResult::Binary { .. }
        ));
    }

    #[test]
    fn read_file_detects_binary_via_invalid_utf8() {
        let dir = tempfile::tempdir().unwrap();
        let f = dir.path().join("a.bin");
        // Invalid UTF-8 with no null byte: must still classify as binary.
        std::fs::write(&f, [0xff, 0xfe, 0xfd, 0xfc]).unwrap();
        assert!(matches!(
            read_file_sync(&f, false, false).unwrap(),
            ReadResult::Binary { .. }
        ));
    }

    #[test]
    fn force_lifts_the_default_size_limit() {
        let dir = tempfile::tempdir().unwrap();
        let f = dir.path().join("big.txt");
        std::fs::write(&f, vec![b'a'; (MAX_READ_BYTES + 1) as usize]).unwrap();
        assert!(matches!(
            read_file_sync(&f, false, false).unwrap(),
            ReadResult::TooLarge { .. }
        ));
        assert!(matches!(
            read_file_sync(&f, true, false).unwrap(),
            ReadResult::Text { .. }
        ));
    }

    #[test]
    fn overwrites_existing_target() {
        let dir = tempfile::tempdir().unwrap();
        let target = dir.path().join("note.txt");
        std::fs::write(&target, b"old").unwrap();
        write_atomic(&target, b"new").unwrap();
        assert_eq!(std::fs::read(&target).unwrap(), b"new");
    }

    #[cfg(unix)]
    #[test]
    fn does_not_follow_legacy_staging_symlink() {
        use std::os::unix::fs::symlink;
        let dir = tempfile::tempdir().unwrap();
        let outside = dir.path().join("outside.txt");
        std::fs::write(&outside, b"untouched").unwrap();

        let target = dir.path().join("note.txt");
        // Pre-stage a symlink at the legacy deterministic staging path.
        let legacy = dir.path().join(".note.txt.terax.tmp");
        symlink(&outside, &legacy).unwrap();

        write_atomic(&target, b"payload").unwrap();

        assert_eq!(std::fs::read(&target).unwrap(), b"payload");
        // The pre-staged symlink target must not have been written through.
        assert_eq!(std::fs::read(&outside).unwrap(), b"untouched");
    }
}
