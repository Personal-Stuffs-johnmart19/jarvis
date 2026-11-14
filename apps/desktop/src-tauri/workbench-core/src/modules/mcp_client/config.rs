//! Model Context Protocol (MCP) server configuration and native policy management.
//!
//! Handles bounded configuration persistence, secret isolation (keyring references only),
//! strict URL/executable/path validation, and atomic storage for `mcp-servers.json`.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};

/// Maximum allowable size of the `mcp-servers.json` file in bytes (1 MiB).
pub const MAX_CONFIG_FILE_BYTES: usize = 1024 * 1024;
/// Maximum number of MCP servers allowed in configuration.
pub const MAX_SERVERS_COUNT: usize = 128;
/// Maximum length of an identifier/name.
pub const MAX_ID_LEN: usize = 128;
/// Maximum number of arguments for stdio transport.
pub const MAX_ARGS_COUNT: usize = 128;
/// Maximum length of a single argument.
pub const MAX_ARG_LEN: usize = 4096;
/// Current supported MCP configuration schema version.
pub const CURRENT_CONFIG_VERSION: u32 = 1;

/// Execution / tool invocation policy.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum McpPolicy {
    Ask,
    Allow,
    Deny,
}

impl Default for McpPolicy {
    fn default() -> Self {
        McpPolicy::Ask
    }
}

/// Resolved policy decision result.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum McpPolicyDecision {
    Allow,
    Ask,
    Deny,
}

/// Keyring secret reference. Raw secret tokens/passwords must never be stored directly in config.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct KeyringSecretRef {
    pub service: String,
    pub account: String,
}

impl KeyringSecretRef {
    pub fn new(service: impl Into<String>, account: impl Into<String>) -> Result<Self, String> {
        let service = service.into();
        let account = account.into();
        if service.is_empty() || service.len() > MAX_ID_LEN {
            return Err("Keyring service name length is invalid".into());
        }
        if account.is_empty() || account.len() > MAX_ID_LEN {
            return Err("Keyring account name length is invalid".into());
        }
        if service.chars().any(char::is_control) || account.chars().any(char::is_control) {
            return Err("Keyring reference must not contain control characters".into());
        }
        Ok(Self { service, account })
    }
}

/// Stdio transport configuration.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct McpStdioConfig {
    /// Absolute or verified command/executable path.
    pub command: String,
    /// Arguments passed directly to the process argv without shell concatenation.
    #[serde(default)]
    pub args: Vec<String>,
    /// Optional working directory. Must be an absolute existing directory.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub cwd: Option<PathBuf>,
    /// Environment variable names referencing Keyring secrets.
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub secret_env: HashMap<String, KeyringSecretRef>,
}

/// HTTP (SSE / Streamable) transport configuration.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct McpHttpConfig {
    /// Endpoint URL. Must be HTTPS unless loopback (127.0.0.1, ::1, localhost).
    pub url: String,
    /// HTTP headers with secret values referenced via Keyring.
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub secret_headers: HashMap<String, KeyringSecretRef>,
}

/// Transport kind for an MCP server.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "lowercase")]
pub enum McpServerTransport {
    Stdio(McpStdioConfig),
    Http(McpHttpConfig),
}

/// Configuration for a single MCP server.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct McpServerConfig {
    /// Stable server identifier (e.g. "github", "postgres").
    pub id: String,
    /// Human-readable display name.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
    /// Whether the server is enabled. Disabled servers are denied by default.
    #[serde(default)]
    pub enabled: bool,
    /// Default execution policy for this server.
    #[serde(default)]
    pub default_policy: McpPolicy,
    /// Per-tool policy overrides.
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub tool_policies: HashMap<String, McpPolicy>,
    /// Transport definition (stdio or HTTP).
    pub transport: McpServerTransport,
}

/// Top-level root structure stored in `mcp-servers.json`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct McpConfig {
    /// Schema version for forward/backward compatibility checks.
    pub version: u32,
    /// Configured servers.
    #[serde(default)]
    pub servers: Vec<McpServerConfig>,
}

impl Default for McpConfig {
    fn default() -> Self {
        Self {
            version: CURRENT_CONFIG_VERSION,
            servers: Vec::new(),
        }
    }
}

impl McpConfig {
    /// Validate all bounds, security rules, and consistency checks across the configuration.
    pub fn validate(&self) -> Result<(), String> {
        if self.version != CURRENT_CONFIG_VERSION {
            return Err(format!(
                "Unsupported MCP configuration version: {} (expected {})",
                self.version, CURRENT_CONFIG_VERSION
            ));
        }

        if self.servers.len() > MAX_SERVERS_COUNT {
            return Err(format!(
                "Server count exceeds maximum allowed limit ({}/{})",
                self.servers.len(),
                MAX_SERVERS_COUNT
            ));
        }

        let mut seen_ids = std::collections::HashSet::new();
        for server in &self.servers {
            server.validate()?;
            if !seen_ids.insert(server.id.clone()) {
                return Err(format!("Duplicate MCP server id: '{}'", server.id));
            }
        }

        Ok(())
    }

    /// Resolve policy decision for a given tool on a server.
    ///
    /// Precedence order:
    /// 1. Server disabled -> Deny
    /// 2. Per-tool override policy (if present)
    /// 3. Server default_policy
    ///
    /// Malicious tool annotations cannot downgrade policy below these settings.
    pub fn resolve_policy(&self, server_id: &str, tool_name: &str) -> McpPolicyDecision {
        let server = match self.servers.iter().find(|s| s.id == server_id) {
            Some(s) => s,
            None => return McpPolicyDecision::Deny,
        };

        if !server.enabled {
            return McpPolicyDecision::Deny;
        }

        let policy = server
            .tool_policies
            .get(tool_name)
            .copied()
            .unwrap_or(server.default_policy);

        match policy {
            McpPolicy::Allow => McpPolicyDecision::Allow,
            McpPolicy::Ask => McpPolicyDecision::Ask,
            McpPolicy::Deny => McpPolicyDecision::Deny,
        }
    }

    /// Import a server configuration, ensuring safe defaults:
    /// - `enabled` is forced to `false` by default on import.
    /// - Duplicates are rejected.
    pub fn import_server(&mut self, mut server: McpServerConfig) -> Result<(), String> {
        server.enabled = false;
        server.validate()?;

        if self.servers.iter().any(|s| s.id == server.id) {
            return Err(format!("Server with ID '{}' already exists", server.id));
        }
        if self.servers.len() >= MAX_SERVERS_COUNT {
            return Err("Maximum server limit reached".into());
        }

        self.servers.push(server);
        Ok(())
    }
}

impl McpServerConfig {
    /// Validate individual server constraints.
    pub fn validate(&self) -> Result<(), String> {
        validate_identifier(&self.id, "Server ID")?;
        if let Some(name) = &self.name {
            if name.is_empty() || name.len() > MAX_ID_LEN || name.chars().any(char::is_control) {
                return Err("Server display name is invalid or too long".into());
            }
        }

        for tool_name in self.tool_policies.keys() {
            validate_tool_name(tool_name)?;
        }

        match &self.transport {
            McpServerTransport::Stdio(stdio) => validate_stdio(stdio)?,
            McpServerTransport::Http(http) => validate_http(http)?,
        }

        Ok(())
    }
}

fn validate_identifier(id: &str, field: &str) -> Result<(), String> {
    if id.is_empty() || id.len() > MAX_ID_LEN {
        return Err(format!("{} length must be between 1 and {}", field, MAX_ID_LEN));
    }
    // Must be alphanumeric or hyphen/underscore/dot, no control characters or path traversal.
    if !id.chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_' || c == '.') {
        return Err(format!(
            "{} '{}' contains invalid characters. Only alphanumeric, '-', '_', '.' allowed",
            field, id
        ));
    }
    if id == "." || id == ".." {
        return Err(format!("{} cannot be '.' or '..'", field));
    }
    Ok(())
}

fn validate_tool_name(name: &str) -> Result<(), String> {
    if name.is_empty() || name.len() > MAX_ID_LEN {
        return Err(format!("Tool name length must be between 1 and {}", MAX_ID_LEN));
    }
    if !name.chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_' || c == '.') {
        return Err(format!("Tool name '{}' contains invalid characters", name));
    }
    Ok(())
}

fn validate_stdio(stdio: &McpStdioConfig) -> Result<(), String> {
    if stdio.command.trim().is_empty() {
        return Err("Stdio command cannot be empty".into());
    }
    if stdio.command.len() > MAX_ARG_LEN {
        return Err("Stdio command path is too long".into());
    }
    // Reject shell meta-characters that suggest concatenation attempts
    if stdio.command.chars().any(|c| matches!(c, ';' | '&' | '|' | '`' | '$' | '\n' | '\r')) {
        return Err("Stdio command contains shell meta-characters; shell concatenation disallowed".into());
    }

    if stdio.args.len() > MAX_ARGS_COUNT {
        return Err(format!("Stdio argument count exceeds maximum ({})", MAX_ARGS_COUNT));
    }
    for arg in &stdio.args {
        if arg.len() > MAX_ARG_LEN {
            return Err("Stdio argument exceeds maximum allowed length".into());
        }
        if arg.chars().any(char::is_control) {
            return Err("Stdio argument contains invalid control characters".into());
        }
    }

    if let Some(cwd) = &stdio.cwd {
        if !cwd.is_absolute() {
            return Err(format!("Stdio cwd must be an absolute path: {:?}", cwd));
        }
        if !cwd.is_dir() {
            return Err(format!("Stdio cwd does not exist or is not a directory: {:?}", cwd));
        }
        validate_storage_path(cwd)?;
    }

    if stdio.secret_env.len() > MAX_ARGS_COUNT {
        return Err("Too many secret environment entries".into());
    }
    for (env_k, env_v) in &stdio.secret_env {
        if env_k.is_empty() || env_k.len() > MAX_ID_LEN || !env_k.chars().all(|c| c.is_ascii_alphanumeric() || c == '_') || env_k.as_bytes()[0].is_ascii_digit() {
            return Err(format!("Invalid secret env key '{}'", env_k));
        }
        KeyringSecretRef::new(&env_v.service, &env_v.account)?;
    }

    Ok(())
}

fn validate_http(http: &McpHttpConfig) -> Result<(), String> {
    if http.url.len() > 4096 || http.url.chars().any(char::is_control) {
        return Err("Invalid HTTP transport URL length or characters".into());
    }
    let url = reqwest::Url::parse(&http.url).map_err(|_| "Invalid HTTP transport URL")?;
    if !url.username().is_empty() || url.password().is_some() {
        return Err("HTTP transport URL must not contain embedded user credentials".into());
    }
    let host = url.host_str().ok_or("HTTP transport URL requires a host")?;
    let host = host.trim_matches(['[', ']']);
    let loopback = host.eq_ignore_ascii_case("localhost")
        || host.parse::<std::net::IpAddr>().is_ok_and(|ip| ip.is_loopback());
    if url.scheme() != "https" && !(url.scheme() == "http" && loopback) {
        return Err("HTTP requires HTTPS except on loopback addresses".into());
    }
    if url.fragment().is_some() {
        return Err("HTTP transport URL must not contain a fragment".into());
    }
    for (key, _) in url.query_pairs() {
        let key = key.to_ascii_lowercase();
        if ["token", "secret", "key", "password", "auth"].iter().any(|name| key.contains(name)) {
            return Err("HTTP transport URL query must not contain credentials; use keyring headers".into());
        }
    }
    if http.secret_headers.len() > MAX_ARGS_COUNT {
        return Err("Too many secret headers".into());
    }
    for (name, secret) in &http.secret_headers {
        reqwest::header::HeaderName::from_bytes(name.as_bytes()).map_err(|_| "Invalid secret header name")?;
        KeyringSecretRef::new(&secret.service, &secret.account)?;
    }
    Ok(())
}

fn validate_storage_path(path: &Path) -> Result<(), String> {
    for entry in path.ancestors() {
        match fs::symlink_metadata(entry) {
            Ok(meta) => {
                #[cfg(windows)]
                let reparse = {
                    use std::os::windows::fs::MetadataExt;
                    meta.file_attributes() & 0x400 != 0
                };
                #[cfg(not(windows))]
                let reparse = false;
                if meta.file_type().is_symlink() || reparse {
                    return Err("MCP configuration cannot cross symlinks or junctions".into());
                }
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {},
            Err(error) => return Err(format!("Cannot inspect MCP configuration path: {error}")),
        }
    }
    Ok(())
}

/// Determine managed file path for `mcp-servers.json`.
/// Uses `JARVIS_DESKTOP_TEST` or app data root if configured.
pub fn get_config_file_path(base_dir: &Path) -> PathBuf {
    base_dir.join("mcp-servers.json")
}

/// Load and parse MCP configuration from a path with size bounds and symlink rejection.
pub fn load_config_from_file(path: &Path) -> Result<McpConfig, String> {
    validate_storage_path(path)?;
    if !path.exists() {
        return Ok(McpConfig::default());
    }

    // Refuse symlinks or junctions
    let sym_meta = fs::symlink_metadata(path)
        .map_err(|e| format!("Failed to inspect config metadata: {}", e))?;
    if sym_meta.file_type().is_symlink() {
        return Err("Configuration file cannot be a symlink or junction".into());
    }

    let file_len = sym_meta.len();
    if file_len > MAX_CONFIG_FILE_BYTES as u64 {
        return Err(format!(
            "Configuration file size ({} bytes) exceeds maximum allowable size ({} bytes)",
            file_len, MAX_CONFIG_FILE_BYTES
        ));
    }

    let bytes = fs::read(path).map_err(|e| format!("Failed to read MCP config: {}", e))?;
    let config: McpConfig = serde_json::from_slice(&bytes)
        .map_err(|e| format!("Failed to parse MCP config JSON: {}", e))?;

    config.validate()?;
    Ok(config)
}

/// Atomically write MCP configuration to the specified destination path.
/// Refuses symlinks/junctions, writes to a sibling temp file, syncs, and renames.
/// Preserves original file untouched on any failure.
pub fn save_config_atomic(path: &Path, config: &McpConfig) -> Result<(), String> {
    config.validate()?;
    validate_storage_path(path)?;

    if let Some(parent) = path.parent() {
        if !parent.exists() {
            fs::create_dir_all(parent).map_err(|e| format!("Failed to create parent dir: {}", e))?;
        }
    }

    // If destination exists, verify it is not a symlink/junction
    if path.exists() {
        let meta = fs::symlink_metadata(path)
            .map_err(|e| format!("Failed to inspect target file metadata: {}", e))?;
        if meta.file_type().is_symlink() {
            return Err("Destination configuration path cannot be a symlink or junction".into());
        }
    }

    let serialized = serde_json::to_vec_pretty(config)
        .map_err(|e| format!("Failed to serialize MCP config: {}", e))?;

    if serialized.len() > MAX_CONFIG_FILE_BYTES {
        return Err("Serialized MCP configuration exceeds file size limits".into());
    }

    let tmp_path = path.with_extension(format!("tmp.{}", std::process::id()));

    // Ensure temp file doesn't collide
    let mut file = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&tmp_path)
        .map_err(|e| format!("Failed to open temp config file for writing: {}", e))?;

    if let Err(e) = file.write_all(&serialized) {
        let _ = fs::remove_file(&tmp_path);
        return Err(format!("Failed to write config content: {}", e));
    }

    if let Err(e) = file.sync_all() {
        let _ = fs::remove_file(&tmp_path);
        return Err(format!("Failed to sync config to disk: {}", e));
    }
    drop(file);

    if let Err(e) = fs::rename(&tmp_path, path) {
        let _ = fs::remove_file(&tmp_path);
        return Err(format!("Failed to atomically replace config file: {}", e));
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn test_roundtrip_valid_config() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("mcp-servers.json");

        let mut config = McpConfig::default();
        config.servers.push(McpServerConfig {
            id: "filesystem-srv".into(),
            name: Some("Local Filesystem".into()),
            enabled: true,
            default_policy: McpPolicy::Ask,
            tool_policies: HashMap::from([("read_file".into(), McpPolicy::Allow)]),
            transport: McpServerTransport::Stdio(McpStdioConfig {
                command: "npx".into(),
                args: vec!["-y".into(), "@modelcontextprotocol/server-filesystem".into()],
                cwd: None,
                secret_env: HashMap::from([(
                    "API_KEY".into(),
                    KeyringSecretRef::new("app.jarvis.workbench", "fs_key").unwrap(),
                )]),
            }),
        });

        save_config_atomic(&path, &config).expect("save should succeed");
        let loaded = load_config_from_file(&path).expect("load should succeed");
        assert_eq!(config, loaded);
    }

    #[test]
    fn test_policy_resolution() {
        let mut config = McpConfig::default();
        config.servers.push(McpServerConfig {
            id: "test-srv".into(),
            name: None,
            enabled: true,
            default_policy: McpPolicy::Ask,
            tool_policies: HashMap::from([
                ("tool_allow".into(), McpPolicy::Allow),
                ("tool_deny".into(), McpPolicy::Deny),
            ]),
            transport: McpServerTransport::Http(McpHttpConfig {
                url: "https://api.example.com/mcp".into(),
                secret_headers: HashMap::new(),
            }),
        });

        assert_eq!(
            config.resolve_policy("test-srv", "tool_allow"),
            McpPolicyDecision::Allow
        );
        assert_eq!(
            config.resolve_policy("test-srv", "tool_deny"),
            McpPolicyDecision::Deny
        );
        assert_eq!(
            config.resolve_policy("test-srv", "tool_unspecified"),
            McpPolicyDecision::Ask
        );

        // Disabling server forces Deny
        config.servers[0].enabled = false;
        assert_eq!(
            config.resolve_policy("test-srv", "tool_allow"),
            McpPolicyDecision::Deny
        );

        // Unknown server yields Deny
        assert_eq!(
            config.resolve_policy("unknown-srv", "tool_allow"),
            McpPolicyDecision::Deny
        );
    }

    #[test]
    fn test_duplicate_server_id_rejected() {
        let mut config = McpConfig::default();
        let srv = McpServerConfig {
            id: "dup-id".into(),
            name: None,
            enabled: false,
            default_policy: McpPolicy::Deny,
            tool_policies: HashMap::new(),
            transport: McpServerTransport::Http(McpHttpConfig {
                url: "https://example.com/mcp".into(),
                secret_headers: HashMap::new(),
            }),
        };
        config.servers.push(srv.clone());
        config.servers.push(srv);

        assert!(config.validate().is_err());
    }

    #[test]
    fn test_import_server_safe_defaults() {
        let mut config = McpConfig::default();
        let srv = McpServerConfig {
            id: "new-import".into(),
            name: None,
            enabled: true, // Should be forced to false
            default_policy: McpPolicy::Ask,
            tool_policies: HashMap::new(),
            transport: McpServerTransport::Http(McpHttpConfig {
                url: "https://example.com/mcp".into(),
                secret_headers: HashMap::new(),
            }),
        };

        config.import_server(srv.clone()).unwrap();
        assert_eq!(config.servers.len(), 1);
        assert!(!config.servers[0].enabled);

        // Duplicate import rejected
        assert!(config.import_server(srv).is_err());
    }

    #[test]
    fn test_invalid_urls() {
        // Plain HTTP on non-loopback is disallowed
        let config_http = McpServerConfig {
            id: "insecure-http".into(),
            name: None,
            enabled: false,
            default_policy: McpPolicy::Deny,
            tool_policies: HashMap::new(),
            transport: McpServerTransport::Http(McpHttpConfig {
                url: "http://remote-server.com/mcp".into(),
                secret_headers: HashMap::new(),
            }),
        };
        assert!(config_http.validate().is_err());

        // Embedded credentials rejected
        let config_cred = McpServerConfig {
            id: "cred-url".into(),
            name: None,
            enabled: false,
            default_policy: McpPolicy::Deny,
            tool_policies: HashMap::new(),
            transport: McpServerTransport::Http(McpHttpConfig {
                url: "https://user:pass@example.com/mcp".into(),
                secret_headers: HashMap::new(),
            }),
        };
        assert!(config_cred.validate().is_err());

        // Secret token in query rejected
        let config_query = McpServerConfig {
            id: "query-token".into(),
            name: None,
            enabled: false,
            default_policy: McpPolicy::Deny,
            tool_policies: HashMap::new(),
            transport: McpServerTransport::Http(McpHttpConfig {
                url: "https://example.com/mcp?token=xyz123".into(),
                secret_headers: HashMap::new(),
            }),
        };
        assert!(config_query.validate().is_err());

        // Valid loopback plain HTTP allowed
        let config_loopback = McpServerConfig {
            id: "local-http".into(),
            name: None,
            enabled: false,
            default_policy: McpPolicy::Deny,
            tool_policies: HashMap::new(),
            transport: McpServerTransport::Http(McpHttpConfig {
                url: "http://127.0.0.1:8000/sse".into(),
                secret_headers: HashMap::new(),
            }),
        };
        assert!(config_loopback.validate().is_ok());
    }

    #[test]
    fn test_invalid_stdio() {
        // Shell concatenation rejected
        let config_shell = McpServerConfig {
            id: "shell-injection".into(),
            name: None,
            enabled: false,
            default_policy: McpPolicy::Deny,
            tool_policies: HashMap::new(),
            transport: McpServerTransport::Stdio(McpStdioConfig {
                command: "node; rm -rf /".into(),
                args: vec![],
                cwd: None,
                secret_env: HashMap::new(),
            }),
        };
        assert!(config_shell.validate().is_err());

        // Relative cwd rejected
        let config_rel_cwd = McpServerConfig {
            id: "rel-cwd".into(),
            name: None,
            enabled: false,
            default_policy: McpPolicy::Deny,
            tool_policies: HashMap::new(),
            transport: McpServerTransport::Stdio(McpStdioConfig {
                command: "node".into(),
                args: vec![],
                cwd: Some(PathBuf::from("relative/dir")),
                secret_env: HashMap::new(),
            }),
        };
        assert!(config_rel_cwd.validate().is_err());
    }

    #[test]
    fn test_version_mismatch_rejected() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("mcp-servers.json");

        let raw_json = r#"{"version": 999, "servers": []}"#;
        fs::write(&path, raw_json).unwrap();

        let res = load_config_from_file(&path);
        assert!(res.is_err());
        assert!(res.unwrap_err().contains("Unsupported MCP configuration version"));
    }
}

#[cfg(test)]
mod boundary_tests {
    use super::*;
    #[test]
    fn ipv6_and_encoded_secret_query_are_parsed_as_urls() {
        let url = |value: &str| McpHttpConfig { url: value.into(), secret_headers: HashMap::new() };
        assert!(validate_http(&url("http://[::1]:1234/mcp")).is_ok());
        assert!(validate_http(&url("http://127.0.0.2:1234/mcp")).is_ok());
        assert!(validate_http(&url("http://localhost.evil.invalid/mcp")).is_err());
        assert!(validate_http(&url("https://example.com/mcp?%74oken=secret")).is_err());
        assert!(validate_http(&url("https://example.com/mcp?version=1")).is_ok());
        assert!(validate_http(&url("https://example.com/mcp#token")).is_err());
        assert!(validate_http(&url("https:///mcp")).is_ok()); // URL parser normalizes this to a host.
    }
    #[test]
    fn atomic_overwrite_and_failed_validation_preserve_saved_config() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("mcp-servers.json");
        let config = McpConfig::default();
        save_config_atomic(&path, &config).unwrap();
        save_config_atomic(&path, &config).unwrap();
        let original = fs::read(&path).unwrap();
        let mut invalid = config;
        invalid.version = 999;
        assert!(save_config_atomic(&path, &invalid).is_err());
        assert_eq!(fs::read(&path).unwrap(), original);
    }
}
