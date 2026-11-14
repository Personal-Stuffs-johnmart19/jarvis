pub mod ai;
pub mod assets;
pub mod clipboard;
pub mod copy;
pub mod file;
pub mod grep;
pub mod index;
#[cfg(windows)]
mod local_file;
pub mod mutate;
pub mod project_memory;
pub(crate) mod read_policy;
pub mod search;
pub mod supersede;
pub mod tree;
pub mod watch;

use std::path::Path;

use crate::modules::workspace::WorkspaceEnv;

/// Runs blocking filesystem work off the async workers and the main thread.
/// Tauri runs a non-async command inline in the IPC handler (the main thread on
/// macOS) and the async runtime has two workers, so every command that touches
/// the disk goes through here.
pub(crate) async fn blocking<T, F>(work: F) -> Result<T, String>
where
    T: Send + 'static,
    F: FnOnce() -> Result<T, String> + Send + 'static,
{
    tauri::async_runtime::spawn_blocking(work)
        .await
        .map_err(|e| e.to_string())?
}

/// How a path under a search root is shown to the frontend. WSL roots keep the
/// caller's spelling so the result stays a valid path inside the distro.
pub(crate) fn display_path(
    path: &Path,
    root_path: &Path,
    root_display: &str,
    workspace: &WorkspaceEnv,
) -> String {
    if workspace.is_wsl() {
        if let Ok(rel) = path.strip_prefix(root_path) {
            let rel = to_canon(rel);
            return if rel.is_empty() {
                root_display.to_string()
            } else if root_display.ends_with('/') {
                format!("{root_display}{rel}")
            } else {
                format!("{root_display}/{rel}")
            };
        }
    }
    to_canon(path)
}

/// The single canonical-to-display conversion: forward slashes, Windows
/// verbatim `\\?\` prefix stripped. Route every such conversion through here.
pub fn to_canon(p: impl AsRef<Path>) -> String {
    let s = p.as_ref().to_string_lossy();
    #[cfg(windows)]
    {
        strip_verbatim(&s)
    }
    #[cfg(not(windows))]
    {
        // Backslashes are legal in Unix filenames; never rewrite them.
        s.into_owned()
    }
}

// Pure so it stays unit-testable on any host. `\\?\C:\x` -> `C:/x`.
#[cfg_attr(not(windows), allow(dead_code))]
fn strip_verbatim(s: &str) -> String {
    let stripped = if let Some(rest) = s.strip_prefix(r"\\?\UNC\") {
        format!(r"\\{rest}")
    } else if let Some(rest) = s.strip_prefix(r"\\?\") {
        rest.to_string()
    } else {
        s.to_string()
    };
    stripped.replace('\\', "/")
}

#[cfg(test)]
mod tests {
    use super::strip_verbatim;
    use proptest::prelude::*;

    #[test]
    fn strips_drive_verbatim_prefix() {
        assert_eq!(strip_verbatim(r"\\?\C:\Users\foo"), "C:/Users/foo");
    }

    #[test]
    fn rewrites_verbatim_unc_to_share_path() {
        assert_eq!(
            strip_verbatim(r"\\?\UNC\server\share\dir"),
            "//server/share/dir"
        );
    }

    #[test]
    fn passes_through_plain_windows_path() {
        assert_eq!(strip_verbatim(r"C:\Users\foo"), "C:/Users/foo");
    }

    #[test]
    fn leaves_forward_slash_path_unchanged() {
        assert_eq!(strip_verbatim("C:/Users/foo"), "C:/Users/foo");
    }

    #[test]
    fn handles_drive_root() {
        assert_eq!(strip_verbatim(r"\\?\C:\"), "C:/");
    }

    proptest! {
        #[test]
        fn strip_verbatim_never_leaves_backslashes_or_prefix(s in r"[A-Za-z0-9\\/: .]{0,40}") {
            let out = strip_verbatim(&s);
            prop_assert!(!out.contains('\\'));
            prop_assert!(!out.starts_with(r"\\?\"));
        }

        #[test]
        fn strip_verbatim_is_idempotent(s in r"[A-Za-z0-9\\/: .]{0,40}") {
            let once = strip_verbatim(&s);
            prop_assert_eq!(strip_verbatim(&once), once);
        }

        #[test]
        fn strip_verbatim_on_plain_input_equals_slash_swap(s in r"[A-Za-z0-9\\/: .]{0,40}") {
            prop_assume!(!s.starts_with(r"\\?\"));
            prop_assert_eq!(strip_verbatim(&s), s.replace('\\', "/"));
        }

        #[test]
        fn strip_verbatim_drive_root_is_preserved(
            drive in r"[A-Z]",
            tail in r"[A-Za-z0-9\\/ .]{0,40}",
        ) {
            let input = format!(r"\\?\{drive}:\{tail}");
            let out = strip_verbatim(&input);
            let expected = format!("{drive}:/");
            prop_assert!(out.starts_with(&expected));
        }

        #[test]
        fn strip_verbatim_unc_becomes_double_slash(tail in r"[A-Za-z0-9\\/ .]{0,40}") {
            let input = format!(r"\\?\UNC\{tail}");
            let out = strip_verbatim(&input);
            prop_assert!(out.starts_with("//"));
            prop_assert!(!out.starts_with(r"\\?\"));
        }
    }
}

/// Keep canonical paths in the caller's namespace so subsequent IPC does not map WSL twice.
pub(crate) fn canonical_workspace_path(path: &str, workspace: &WorkspaceEnv) -> Result<String, String> {
    let WorkspaceEnv::Wsl { distro } = workspace else { return Ok(path.to_owned()) };
    let normalized = path.replace('\\', "/");
    for host in ["//wsl.localhost/", "//wsl$/"] {
        if normalized.to_ascii_lowercase().starts_with(host) {
            let rest = &normalized[host.len()..];
            let (actual, tail) = rest.split_once('/').unwrap_or((rest, ""));
            if !actual.eq_ignore_ascii_case(distro) { return Err("Canonical path belongs to another WSL distribution".into()); }
            return Ok(format!("/{tail}"));
        }
    }
    if normalized.as_bytes().get(1) == Some(&b':') && normalized.as_bytes().first().is_some_and(u8::is_ascii_alphabetic) {
        return Ok(format!("/mnt/{}{}", normalized[..1].to_ascii_lowercase(), &normalized[2..]));
    }
    Ok(path.to_owned())
}

#[cfg(test)]
mod canonical_workspace_tests {
    use super::*;
    #[cfg(windows)]
    #[tokio::test]
    async fn drvfs_canonicalize_then_read_round_trip() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("editor.xml");
        std::fs::write(&file, "<resources>round-trip</resources>").unwrap();
        let env = WorkspaceEnv::Wsl { distro: "Ubuntu".into() };
        let linux_path = canonical_workspace_path(&to_canon(&file), &env).unwrap();
        let canonical = super::file::fs_canonicalize(linux_path, Some(WorkspaceEnv::Wsl { distro: "Ubuntu".into() }), None, None).await.unwrap();
        let read = super::file::fs_read_file(canonical, Some(env), None, None).await.unwrap();
        match read { super::file::ReadResult::Text {content, ..} => assert_eq!(content, "<resources>round-trip</resources>"), _ => panic!("Expected text") }
    }
    #[test]
    fn canonical_wsl_paths_stay_in_linux_namespace() {
        let env = WorkspaceEnv::Wsl { distro: "Ubuntu".into() };
        assert_eq!(canonical_workspace_path("//wsl.localhost/Ubuntu/home/user/file.xml", &env).unwrap(), "/home/user/file.xml");
        assert_eq!(canonical_workspace_path("//wsl$/Ubuntu/home/user", &env).unwrap(), "/home/user");
        assert_eq!(canonical_workspace_path("D:/project/file.xml", &env).unwrap(), "/mnt/d/project/file.xml");
        assert!(canonical_workspace_path("//wsl$/Other/home/user", &env).is_err());
        assert_eq!(canonical_workspace_path("D:/file.xml", &WorkspaceEnv::Local).unwrap(), "D:/file.xml");
    }
}
