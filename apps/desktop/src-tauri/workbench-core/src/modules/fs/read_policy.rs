use regex::RegexSetBuilder;
use serde::Deserialize;
use std::path::Path;
use std::sync::OnceLock;

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct PathPolicy {
    secret_basenames: Vec<String>,
    protected_dirs: Vec<String>,
    #[serde(default)]
    secret_path_patterns: Vec<String>,
}

pub(crate) fn readable(path: &Path) -> Result<(), String> {
    if let Some(cache) = dirs::cache_dir() {
        deny_runtime_directory(path, &cache.join("terax"))?;
    }
    static POLICY: OnceLock<(regex::RegexSet, regex::RegexSet, Vec<String>)> = OnceLock::new();
    let (patterns, path_patterns, directories) = POLICY.get_or_init(|| {
        let policy: PathPolicy = serde_json::from_str(include_str!(
            "../../../../../workbench/src/modules/ai/lib/pathPolicy.json"
        ))
        .expect("valid path policy");
        (
            RegexSetBuilder::new(policy.secret_basenames)
                .case_insensitive(true)
                .build()
                .expect("valid secret patterns"),
            RegexSetBuilder::new(policy.secret_path_patterns)
                .case_insensitive(true)
                .build()
                .expect("valid secret path patterns"),
            policy.protected_dirs,
        )
    });
    let path = path.to_str().ok_or("refused non-UTF-8 AI path")?;
    if path.is_empty() || path.chars().any(|c| c < ' ') {
        return Err("refused invalid AI path".into());
    }
    let normalized = path.replace('\\', "/");
    let basename = normalized.rsplit('/').next().unwrap_or("");
    if patterns.is_match(&basename.replace('\u{feff}', " ")) {
        return Err("refused sensitive AI file".into());
    }
    let comparison = normalized
        .split('/')
        .map(|part| {
            part.split(':')
                .next()
                .unwrap_or("")
                .trim_end_matches(|c: char| c == '.' || c == '\u{feff}' || c.is_whitespace())
                .to_lowercase()
        })
        .collect::<Vec<_>>()
        .join("/");
    if path_patterns.is_match(&comparison) {
        return Err("refused sensitive AI file".into());
    }
    let comparison = format!("{comparison}/");
    if directories
        .iter()
        .any(|dir| comparison.contains(&format!("{dir}/")))
    {
        return Err("refused protected AI directory".into());
    }
    Ok(())
}

fn inside_directory(path: &Path, directory: &Path) -> bool {
    #[cfg(windows)]
    {
        windows_inside_directory(path, directory)
    }
    #[cfg(not(windows))]
    {
        path.starts_with(directory)
    }
}

#[cfg(any(windows, test))]
fn windows_inside_directory(path: &Path, directory: &Path) -> bool {
    let (Some(path), Some(directory)) = (path.to_str(), directory.to_str()) else {
        return false;
    };
    Path::new(&super::strip_verbatim(path)).starts_with(super::strip_verbatim(directory))
}

fn deny_runtime_directory(path: &Path, directory: &Path) -> Result<(), String> {
    if inside_directory(path, directory)
        || std::fs::canonicalize(directory)
            .is_ok_and(|canonical| inside_directory(path, &canonical))
    {
        return Err("refused application credential directory".into());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn relocated_runtime_credentials_are_protected() {
        let tmp = tempfile::tempdir().unwrap();
        let relocated = tmp.path().join("custom-cache").join("terax");
        std::fs::create_dir_all(&relocated).unwrap();
        let actual = std::fs::canonicalize(&relocated).unwrap();
        assert!(deny_runtime_directory(&actual.join("control.json"), &relocated).is_err());
        assert!(deny_runtime_directory(&actual.join("control.json.tmp-abc"), &relocated).is_err());
        assert!(deny_runtime_directory(&tmp.path().join("source.ts"), &relocated).is_ok());
        #[cfg(unix)]
        {
            let alias = tmp.path().join("cache-alias");
            std::os::unix::fs::symlink(&relocated, &alias).unwrap();
            assert!(deny_runtime_directory(&actual.join("control.json"), &alias).is_err());
        }
    }

    #[test]
    fn windows_runtime_paths_match_across_verbatim_prefixes() {
        for (path, directory) in [
            (r"C:\project\state\control.json", r"\\?\C:\project\state"),
            (r"\\?\C:\project\state\control.json", r"C:\project\state"),
            (
                r"\\server\share\state\control.json",
                r"\\?\UNC\server\share\state",
            ),
        ] {
            assert!(windows_inside_directory(
                Path::new(path),
                Path::new(directory)
            ));
        }
        assert!(!windows_inside_directory(
            Path::new("C:/project/state-other/file"),
            Path::new("C:/project/state")
        ));
        assert!(!windows_inside_directory(
            Path::new("//wsl.localhost/Ubuntu/home/me/State/file"),
            Path::new("//wsl.localhost/Ubuntu/home/me/state")
        ));
    }

    #[cfg(windows)]
    #[test]
    fn a_junction_relocated_cache_is_denied_after_opened_path_normalization() {
        use std::os::windows::process::CommandExt;
        let tmp = tempfile::tempdir().unwrap();
        let target = tmp
            .path()
            .join("authorized-project")
            .join("application-state");
        let alias = tmp.path().join("relocated-cache").join("terax");
        std::fs::create_dir_all(&target).unwrap();
        std::fs::create_dir_all(alias.parent().unwrap()).unwrap();
        std::fs::write(target.join("control.json"), "SYNTHETIC_CREDENTIAL").unwrap();
        // Quote fixture paths for cmd; percent expansion cannot be quoted away.
        for path in [&alias, &target] {
            assert!(!path.to_str().unwrap().contains(['%', '"', '\r', '\n']));
        }
        let result = std::process::Command::new("cmd.exe")
            .args(["/D", "/V:OFF", "/C"])
            .raw_arg(format!(
                "mklink /J \"{}\" \"{}\"",
                alias.display(),
                target.display()
            ))
            .output()
            .unwrap();
        assert!(
            result.status.success(),
            "{}",
            String::from_utf8_lossy(&result.stderr)
        );
        let actual = std::fs::canonicalize(target.join("control.json")).unwrap();
        let opened = std::path::PathBuf::from(super::super::to_canon(actual));
        assert!(deny_runtime_directory(&opened, &alias).is_err());
        std::fs::remove_dir(alias).unwrap();
    }

    #[test]
    fn javascript_bom_whitespace_is_also_sensitive_natively() {
        for path in [
            "/repo/.env\u{feff}x",
            "/repo/.ssh\u{feff}/key",
            "/repo/secrets.json.tmp-abc",
        ] {
            assert!(readable(Path::new(path)).is_err(), "{path}");
        }
        assert!(readable(Path::new("/repo/environment.ts")).is_ok());
    }
    #[test]
    fn jarvis_credentials_and_windows_aliases_are_sensitive() {
        for name in ["bootstrap-key.json", "api_keys.json", "store.dpapi"] {
            for suffix in ["", ".", " ", ":stream", ".backup"] {
                let path = format!("/repo/{name}{suffix}");
                assert!(readable(Path::new(&path)).is_err(), "{path}");
            }
        }
        for name in ["bootstrap-key.txt", "api_keys.txt", "store.txt"] {
            assert!(readable(&Path::new("/repo").join(name)).is_ok(), "{name}");
        }
    }    #[test]
    fn canonical_credential_stores_and_parent_aliases_are_sensitive() {
        for path in [
            "/repo/google_token.json", "/repo/GOOGLE_TOKEN.JSON.backup",
            "/repo/.codex/auth.json", "/repo/.codex./auth.json",
            "/repo/data/credentials/key", "/repo/data./credentials /key",
            "/repo/config/bridge_token.txt", "/repo/config./bridge_token.json:stream",
            "/repo/config /bridge_token.txt-copy", "/repo/config:stream/bridge_token.json.bak",
            "/repo/data/network/network_preferences.json",
            "/repo/data./network./proxy_cache.json.bak",
            "/repo/data/network /proxy_cache.json:stream",
        ] {
            assert!(readable(Path::new(path)).is_err(), "{path}");
        }
        for path in [
            "/repo/network_preferences.json", "/repo/proxy_cache.json",
            "/repo/src/bridge_token.txt", "/repo/config/bridge_token.ts",
            "/repo/data/network/proxy_cache.txt", "/repo/google_token.txt",
        ] {
            assert!(readable(Path::new(path)).is_ok(), "{path}");
        }
    }
    #[test]
    fn git_credential_file_backups_share_the_model_policy() {
        for suffix in ["", ".", " ", ".backup", "-old", "_copy", ":stream", "\u{feff}.old"] {
            let path = format!("/repo/.git-credentials{suffix}");
            assert!(readable(Path::new(&path)).is_err(), "{path}");
            assert!(readable(Path::new(&path.to_uppercase())).is_err(), "{path}");
        }
        for name in ["git-credentials.md", "credentials-guide.md"] {
            assert!(readable(&Path::new("/repo").join(name)).is_ok(), "{name}");
        }
    }}
