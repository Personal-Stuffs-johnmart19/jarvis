//! Native HuggingFace model metadata and download/storage backend.
//!
//! Scope: discover repo files + immutable revision, describe explicitly
//! supported bundle layouts, download selected files into a managed directory
//! (resumable, bounded, cancellable, SHA256-verified) and delete only files
//! recorded in an owned manifest. Nothing here executes downloaded content.
//! No Tauri commands are registered yet; the Settings UI integration wires
//! [`fetch_repo_info`], [`match_bundles`], [`download_model`], [`delete_model`]
//! and [`list_installed`] in a later task.

use std::fs;
use std::io::{Read, Write};
use std::net::IpAddr;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

use reqwest::Url;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use tauri::Emitter;

#[path = "model_hardware.rs"]
mod hardware;

pub const OWNER: &str = "jarvis-model-downloads/1";
pub const MANIFEST_NAME: &str = "manifest.json";
pub const CANCELLED: &str = "cancelled";

type Res<T> = Result<T, String>;

#[derive(Clone, Debug)]
pub struct Config {
    /// Managed directory; everything installed/deleted lives below it.
    pub models_root: PathBuf,
    /// Base for both `/api/models/..` and `/{repo}/resolve/{rev}/{file}`.
    pub hub_base: String,
    /// https hosts (and their subdomains) that requests/redirects may reach.
    pub allowed_hosts: Vec<String>,
    /// Test/local-mirror switch: also permit plain http to loopback only.
    pub allow_loopback_http: bool,
    pub max_file_bytes: u64,
    pub max_metadata_bytes: usize,
    pub max_redirects: usize,
    pub connect_timeout: Duration,
    pub chunk_timeout: Duration,
}

impl Config {
    pub fn new(models_root: PathBuf) -> Self {
        Self {
            models_root,
            hub_base: "https://huggingface.co".into(),
            allowed_hosts: vec!["huggingface.co".into(), "hf.co".into()],
            allow_loopback_http: false,
            max_file_bytes: 64 * 1024 * 1024 * 1024,
            max_metadata_bytes: 8 * 1024 * 1024,
            max_redirects: 5,
            connect_timeout: Duration::from_secs(15),
            chunk_timeout: Duration::from_secs(60),
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ModelKind {
    Llm,
    Vision,
    Embedding,
    Tts,
}

impl ModelKind {
    pub fn dir(self) -> &'static str {
        match self {
            ModelKind::Llm => "llm",
            ModelKind::Vision => "vision",
            ModelKind::Embedding => "embedding",
            ModelKind::Tts => "tts",
        }
    }
    const ALL: [ModelKind; 4] = [ModelKind::Llm, ModelKind::Vision, ModelKind::Embedding, ModelKind::Tts];
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct RepoFile {
    pub path: String,
    pub size: Option<u64>,
    /// LFS SHA256 (lowercase hex) when the hub publishes one.
    pub sha256: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct RepoInfo {
    pub repo_id: String,
    /// Immutable commit SHA all downloads are pinned to.
    pub revision: String,
    pub license: Option<String>,
    pub gated: bool,
    pub files: Vec<RepoFile>,
    pub total_size: u64,
}

// ---------------------------------------------------------------- bundles

#[derive(Clone, Debug, Serialize)]
pub struct BundleDescriptor {
    pub id: &'static str,
    pub kind: ModelKind,
    pub label: &'static str,
    pub requirement: &'static str,
}

/// Explicit compatible layouts. Note: Piper ONNX is format-only; Jarvis runs audio.cpp Supertonic and Silero.
pub fn bundle_descriptors() -> Vec<BundleDescriptor> {
    vec![
        BundleDescriptor { id: "llm-gguf", kind: ModelKind::Llm, label: "GGUF language model", requirement: "one single-file .gguf or a complete numbered GGUF shard group" },
        BundleDescriptor { id: "vision-gguf-mmproj", kind: ModelKind::Vision, label: "GGUF vision-language model", requirement: "one .gguf plus one mmproj*.gguf projector" },
        BundleDescriptor { id: "embedding-gguf", kind: ModelKind::Embedding, label: "GGUF embedding model", requirement: "one single-file .gguf (not mmproj, not sharded)" },
        BundleDescriptor { id: "tts-supertonic-gguf", kind: ModelKind::Tts, label: "Supertonic GGUF voice", requirement: "single-file .gguf voice model for audio.cpp" },
        BundleDescriptor { id: "tts-piper-onnx", kind: ModelKind::Tts, label: "Piper ONNX voice (CPU)", requirement: "<name>.onnx plus matching <name>.onnx.json for the local Piper CPU runtime" },
    ]
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct BundleMatch {
    pub descriptor_id: String,
    pub kind: ModelKind,
    pub files: Vec<String>,
}

fn base_name(p: &str) -> &str {
    p.rsplit('/').next().unwrap_or(p)
}
fn is_mmproj(p: &str) -> bool {
    base_name(p).to_ascii_lowercase().contains("mmproj")
}
fn is_gguf(p: &str) -> bool {
    p.to_ascii_lowercase().ends_with(".gguf")
}
/// Split candidates are excluded from the single-file variants.
fn is_sharded(p: &str) -> bool {
    let l = p.to_ascii_lowercase();
    l.contains("-of-") && l.ends_with(".gguf")
}

/// Complete canonical split groups only; each file remains independently verified.
fn shard_groups(names: &[&str]) -> Vec<Vec<String>> {
    let mut groups = std::collections::BTreeMap::<(String, usize), Vec<(usize, String)>>::new();
    for name in names.iter().copied().filter(|n| is_gguf(n) && !is_mmproj(n)) {
        let Some(stem) = name.strip_suffix(".gguf") else { continue };
        let Some((left, total)) = stem.rsplit_once("-of-") else { continue };
        let Some((prefix, index)) = left.rsplit_once('-') else { continue };
        if index.len() != 5 || total.len() != 5 || !index.bytes().chain(total.bytes()).all(|c| c.is_ascii_digit()) { continue; }
        let (Ok(index), Ok(total)) = (index.parse::<usize>(), total.parse::<usize>()) else { continue };
        if prefix.is_empty() || total == 0 || total > 1024 || index == 0 || index > total { continue; }
        groups.entry((prefix.into(), total)).or_default().push((index, name.into()));
    }
    groups.into_iter().filter_map(|((_, total), mut rows)| {
        rows.sort();
        if rows.len() != total || rows.iter().enumerate().any(|(i, row)| row.0 != i + 1) { return None; }
        Some(rows.into_iter().map(|(_, path)| path).collect())
    }).collect()
}

/// Format-level compatibility only: `kind` is the user's declared intent, the
/// layout just proves the files form a bundle Jarvis knows how to load.
pub fn match_bundles(kind: ModelKind, files: &[RepoFile]) -> Vec<BundleMatch> {
    match_bundles_with_projector(kind, files, None)
}

fn match_bundles_with_projector(kind: ModelKind, files: &[RepoFile], projector: Option<&str>) -> Vec<BundleMatch> {
    let names: Vec<&str> = files.iter().map(|f| f.path.as_str()).collect();
    let weights = || names.iter().copied().filter(|n| is_gguf(n) && !is_mmproj(n) && !is_sharded(n));
    let mut out = Vec::new();
    if kind == ModelKind::Llm {
        for files in shard_groups(&names) {
            out.push(BundleMatch { descriptor_id: "llm-gguf".into(), kind, files });
        }
    }
    match kind {
        ModelKind::Llm | ModelKind::Embedding => {
            let id = if kind == ModelKind::Llm { "llm-gguf" } else { "embedding-gguf" };
            for w in weights() {
                out.push(BundleMatch { descriptor_id: id.into(), kind, files: vec![w.into()] });
            }
        }
        ModelKind::Vision => {
            let mut proj: Vec<&str> = names.iter().copied().filter(|n| is_gguf(n) && is_mmproj(n) && !is_sharded(n)).collect();
            proj.sort();
            proj.dedup();
            let chosen = match projector {
                Some(path) => proj.iter().find(|candidate| **candidate == path),
                None if proj.len() == 1 => proj.first(),
                _ => None,
            };
            if let Some(p) = chosen {
                for w in weights() {
                    out.push(BundleMatch { descriptor_id: "vision-gguf-mmproj".into(), kind, files: vec![w.into(), (*p).into()] });
                }
            }
        }
        ModelKind::Tts => {
            for w in weights() {
                if w.to_ascii_lowercase().contains("supertonic") {
                    out.push(BundleMatch { descriptor_id: "tts-supertonic-gguf".into(), kind, files: vec![w.into()] });
                }
            }
            for n in names.iter().copied().filter(|n| n.to_ascii_lowercase().ends_with(".onnx")) {
                let cfg = format!("{n}.json");
                if names.contains(&cfg.as_str()) {
                    out.push(BundleMatch { descriptor_id: "tts-piper-onnx".into(), kind, files: vec![n.into(), cfg] });
                }
            }
        }
    }
    out
}

// -------------------------------------------------------------- validation

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn is_hex(s: &str, len: usize) -> bool {
    s.len() == len && s.bytes().all(|b| b.is_ascii_hexdigit())
}

pub fn validate_repo_id(repo: &str) -> Res<()> {
    let ok_part = |p: &str| {
        !p.is_empty()
            && p.len() <= 96
            && p != "."
            && p != ".."
            && p.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'.' || b == b'_' || b == b'-')
    };
    let mut it = repo.split('/');
    match (it.next(), it.next(), it.next()) {
        (Some(a), Some(b), None) if ok_part(a) && ok_part(b) => Ok(()),
        _ => Err(format!("invalid repository id: {repo}")),
    }
}

fn validate_revision_name(rev: &str) -> Res<()> {
    if rev.is_empty() || rev.len() > 100 || rev.contains("..") || !rev.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'.' || b == b'_' || b == b'-') {
        return Err(format!("invalid revision: {rev}"));
    }
    Ok(())
}

fn validate_revision_sha(rev: &str) -> Res<()> {
    if is_hex(rev, 40) || is_hex(rev, 64) {
        Ok(())
    } else {
        Err("revision is not an immutable commit sha".into())
    }
}

pub fn validate_request_id(id: &str) -> Res<()> {
    if id.is_empty() || id.len() > 128 {
        return Err("invalid request id length".into());
    }
    if !id.chars().all(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.' | ':')) {
        return Err(format!("invalid characters in request id: {id}"));
    }
    Ok(())
}

const RESERVED: [&str; 22] = [
    "CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7",
    "LPT8", "LPT9",
];

/// Repository-relative file path -> safe components (no traversal, drive
/// letters, backslashes, reserved names or collisions with our own files).
pub fn validate_rel(rel: &str) -> Res<Vec<&str>> {
    if rel.is_empty() || rel.len() > 512 {
        return Err("invalid file path length".into());
    }
    let comps: Vec<&str> = rel.split('/').collect();
    for (i, c) in comps.iter().enumerate() {
        let bad = c.is_empty()
            || *c == "."
            || *c == ".."
            || c.len() > 255
            || c.chars().any(|ch| ch.is_control() || matches!(ch, '\\' | ':' | '*' | '?' | '"' | '<' | '>' | '|'))
            || c.ends_with('.')
            || c.ends_with(' ')
            || c.to_ascii_lowercase().ends_with(".part")
            || (i == 0 && comps.len() == 1 && *c == MANIFEST_NAME)
            || (i == 0 && c.eq_ignore_ascii_case(MANIFEST_NAME))
            || RESERVED.contains(&c.split('.').next().unwrap_or("").to_ascii_uppercase().as_str());
        if bad {
            return Err(format!("unsafe file path: {rel}"));
        }
    }
    Ok(comps)
}

fn is_loopback(url: &Url) -> bool {
    match url.host_str() {
        Some(h) => h == "localhost" || h.trim_matches(|c| c == '[' || c == ']').parse::<IpAddr>().map(|i| i.is_loopback()).unwrap_or(false),
        None => false,
    }
}

pub fn validate_url(url: &Url, cfg: &Config) -> Res<()> {
    if !url.username().is_empty() || url.password().is_some() {
        return Err("url credentials are not allowed".into());
    }
    let host = url.host_str().ok_or("url has no host")?;
    match url.scheme() {
        "http" if cfg.allow_loopback_http && is_loopback(url) => Ok(()),
        "https" => {
            let bare = host.trim_matches(|c| c == '[' || c == ']');
            if bare.parse::<IpAddr>().is_ok() {
                return Err(format!("ip hosts are not allowed: {host}"));
            }
            let host = host.to_ascii_lowercase();
            if cfg.allowed_hosts.iter().any(|a| host == *a || host.ends_with(&format!(".{a}"))) {
                Ok(())
            } else {
                Err(format!("host not allowed: {host}"))
            }
        }
        s => Err(format!("unsafe url scheme or host: {s}://{host}")),
    }
}

// -------------------------------------------------------------------- http

static CRYPTO: std::sync::OnceLock<()> = std::sync::OnceLock::new();

pub fn ensure_crypto_provider() {
    CRYPTO.get_or_init(|| {
        let _ = rustls::crypto::ring::default_provider().install_default();
    });
}

fn client(cfg: &Config) -> Res<reqwest::Client> {
    ensure_crypto_provider();
    let mut b = reqwest::Client::builder().redirect(reqwest::redirect::Policy::none()).connect_timeout(cfg.connect_timeout);
    if cfg.allow_loopback_http {
        b = b.no_proxy();
    }
    b.build().map_err(|e| format!("http client: {e}"))
}

/// GET with manual redirect handling so every hop is re-validated.
async fn fetch(client: &reqwest::Client, cfg: &Config, start: Url, range_from: Option<u64>, cancel: &AtomicBool) -> Res<reqwest::Response> {
    let mut url = start;
    for _ in 0..=cfg.max_redirects {
        if cancel.load(Ordering::Relaxed) {
            return Err(CANCELLED.into());
        }
        validate_url(&url, cfg)?;
        let mut rq = client.get(url.clone());
        if let Some(n) = range_from {
            rq = rq.header(reqwest::header::RANGE, format!("bytes={n}-"));
        }
        let send_fut = rq.send();
        tokio::pin!(send_fut);
        let deadline = tokio::time::Instant::now() + cfg.connect_timeout;
        let timeout_fut = tokio::time::sleep_until(deadline);
        tokio::pin!(timeout_fut);
        let resp = loop {
            if cancel.load(Ordering::Relaxed) {
                return Err(CANCELLED.into());
            }
            tokio::select! {
                biased;
                _ = &mut timeout_fut => {
                    return Err("connection/headers timed out".into());
                }
                res = &mut send_fut => {
                    match res {
                        Err(e) => return Err(format!("request failed: {e}")),
                        Ok(r) => break r,
                    }
                }
                _ = tokio::time::sleep(Duration::from_millis(25)) => {
                    if cancel.load(Ordering::Relaxed) {
                        return Err(CANCELLED.into());
                    }
                }
            }
        };
        if resp.status().is_redirection() {
            let loc = resp.headers().get(reqwest::header::LOCATION).and_then(|v| v.to_str().ok()).ok_or("redirect without location")?;
            url = url.join(loc).map_err(|e| format!("bad redirect: {e}"))?;
            continue;
        }
        return Ok(resp);
    }
    Err("too many redirects".into())
}

async fn read_body(mut resp: reqwest::Response, limit: usize, t: Duration, cancel: &AtomicBool) -> Res<Vec<u8>> {
    let mut out = Vec::new();
    loop {
        if cancel.load(Ordering::Relaxed) {
            return Err(CANCELLED.into());
        }
        let chunk_fut = resp.chunk();
        tokio::pin!(chunk_fut);
        let deadline = tokio::time::Instant::now() + t;
        let timeout_fut = tokio::time::sleep_until(deadline);
        tokio::pin!(timeout_fut);
        let chunk = loop {
            if cancel.load(Ordering::Relaxed) {
                return Err(CANCELLED.into());
            }
            tokio::select! {
                biased;
                _ = &mut timeout_fut => {
                    return Err("metadata read timed out".into());
                }
                res = &mut chunk_fut => {
                    match res {
                        Err(e) => return Err(format!("metadata read failed: {e}")),
                        Ok(c) => break c,
                    }
                }
                _ = tokio::time::sleep(Duration::from_millis(25)) => {
                    if cancel.load(Ordering::Relaxed) {
                        return Err(CANCELLED.into());
                    }
                }
            }
        };
        match chunk {
            None => break,
            Some(b) => {
                if out.len() + b.len() > limit {
                    return Err("metadata response too large".into());
                }
                out.extend_from_slice(&b);
            }
        }
    }
    Ok(out)
}

// ---------------------------------------------------------------- metadata

#[derive(Deserialize)]
struct RawLfs {
    sha256: Option<String>,
}
#[derive(Deserialize)]
struct RawSibling {
    rfilename: String,
    size: Option<u64>,
    lfs: Option<RawLfs>,
}
#[derive(Deserialize)]
struct RawInfo {
    sha: Option<String>,
    #[serde(default)]
    siblings: Vec<RawSibling>,
    #[serde(default)]
    tags: Vec<String>,
    #[serde(rename = "cardData")]
    card_data: Option<serde_json::Value>,
    #[serde(default)]
    gated: serde_json::Value,
}

pub fn parse_repo_info(repo_id: &str, body: &[u8]) -> Res<RepoInfo> {
    validate_repo_id(repo_id)?;
    let raw: RawInfo = serde_json::from_slice(body).map_err(|e| format!("invalid repository metadata: {e}"))?;
    let revision = raw.sha.unwrap_or_default().to_ascii_lowercase();
    validate_revision_sha(&revision)?;
    let license = raw
        .card_data
        .as_ref()
        .and_then(|c| c.get("license"))
        .and_then(|l| l.as_str().map(str::to_owned))
        .or_else(|| raw.tags.iter().find_map(|t| t.strip_prefix("license:").map(str::to_owned)));
    let gated = match &raw.gated {
        serde_json::Value::Bool(b) => *b,
        serde_json::Value::String(s) => !s.is_empty() && s != "false",
        _ => false,
    };
    let mut files = Vec::new();
    for s in raw.siblings {
        // Skip entries we could never store safely instead of failing the listing.
        if validate_rel(&s.rfilename).is_err() {
            continue;
        }
        let sha256 = s.lfs.and_then(|l| l.sha256).map(|h| h.to_ascii_lowercase()).filter(|h| is_hex(h, 64));
        files.push(RepoFile { path: s.rfilename, size: s.size, sha256 });
    }
    let total_size = files.iter().filter_map(|f| f.size).sum();
    Ok(RepoInfo { repo_id: repo_id.into(), revision, license, gated, files, total_size })
}

/// Small metadata query. `revision` may be a branch/tag/sha; the result is
/// always pinned to the resolved commit sha.
pub async fn fetch_repo_info_cancellable(
    cfg: &Config,
    repo_id: &str,
    revision: Option<&str>,
    cancel: &AtomicBool,
) -> Res<RepoInfo> {
    validate_repo_id(repo_id)?;
    if cancel.load(Ordering::Relaxed) {
        return Err(CANCELLED.into());
    }
    let path = match revision {
        Some(r) => {
            validate_revision_name(r)?;
            format!("{}/api/models/{repo_id}/revision/{r}?blobs=true", cfg.hub_base.trim_end_matches('/'))
        }
        None => format!("{}/api/models/{repo_id}?blobs=true", cfg.hub_base.trim_end_matches('/')),
    };
    let url = Url::parse(&path).map_err(|e| format!("bad url: {e}"))?;
    let client = client(cfg)?;
    let resp = fetch(&client, cfg, url, None, cancel).await?;
    if !resp.status().is_success() {
        return Err(format!("metadata request failed: HTTP {}", resp.status().as_u16()));
    }
    let body = read_body(resp, cfg.max_metadata_bytes, cfg.chunk_timeout, cancel).await?;
    parse_repo_info(repo_id, &body)
}

pub async fn fetch_repo_info(cfg: &Config, repo_id: &str, revision: Option<&str>) -> Res<RepoInfo> {
    let never_cancel = AtomicBool::new(false);
    fetch_repo_info_cancellable(cfg, repo_id, revision, &never_cancel).await
}

fn encode_segment(s: &str) -> String {
    let mut o = String::new();
    for b in s.bytes() {
        if b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_' | b'.' | b'~') {
            o.push(b as char);
        } else {
            o.push_str(&format!("%{b:02X}"));
        }
    }
    o
}

fn resolve_url(cfg: &Config, repo: &str, rev: &str, file: &str) -> Res<Url> {
    let enc: Vec<String> = file.split('/').map(encode_segment).collect();
    Url::parse(&format!("{}/{repo}/resolve/{rev}/{}", cfg.hub_base.trim_end_matches('/'), enc.join("/"))).map_err(|e| format!("bad url: {e}"))
}

// ----------------------------------------------------------------- storage

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct ManifestFile {
    pub path: String,
    pub size: u64,
    pub sha256: String,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Manifest {
    pub owner: String,
    pub complete: bool,
    pub repo_id: String,
    pub revision: String,
    pub kind: ModelKind,
    pub bundle_id: String,
    pub license: Option<String>,
    pub files: Vec<ManifestFile>,
}

fn repo_dir_name(repo: &str) -> String {
    repo.replace('/', "__")
}

fn refuse_symlink(path: &Path) -> Res<()> {
    match fs::symlink_metadata(path) {
        Ok(m) => {
            if m.file_type().is_symlink() {
                return Err(format!("symlink refused: {}", path.display()));
            }
            #[cfg(windows)]
            {
                use std::os::windows::fs::MetadataExt;
                const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x400;
                if (m.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT) != 0 {
                    return Err(format!("reparse point/junction refused: {}", path.display()));
                }
            }
            Ok(())
        }
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(e) => Err(format!("stat: {e}")),
    }
}

/// Create/walk `comps` below `base`, refusing symlinks, junctions, and non-directories.
fn ensure_real_dir(base: &Path, comps: &[&str]) -> Res<PathBuf> {
    refuse_symlink(base)?;
    let mut cur = base.to_path_buf();
    for c in comps {
        cur.push(c);
        match fs::symlink_metadata(&cur) {
            Ok(m) => {
                refuse_symlink(&cur)?;
                if !m.is_dir() {
                    return Err(format!("not a directory: {}", cur.display()));
                }
            }
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => fs::create_dir(&cur).map_err(|e| format!("create dir: {e}"))?,
            Err(e) => return Err(format!("stat: {e}")),
        }
    }
    Ok(cur)
}

fn model_dir_path(cfg: &Config, kind: ModelKind, repo: &str, rev: &str) -> PathBuf {
    cfg.models_root.join(kind.dir()).join(repo_dir_name(repo)).join(rev)
}

fn prepare_model_dir(cfg: &Config, kind: ModelKind, repo: &str, rev: &str) -> Res<PathBuf> {
    fs::create_dir_all(&cfg.models_root).map_err(|e| format!("create models root: {e}"))?;
    let name = repo_dir_name(repo);
    let dir = ensure_real_dir(&cfg.models_root, &[kind.dir(), &name, rev])?;
    let root = fs::canonicalize(&cfg.models_root).map_err(|e| format!("models root: {e}"))?;
    let canon = fs::canonicalize(&dir).map_err(|e| format!("model dir: {e}"))?;
    if !canon.starts_with(&root) {
        return Err("model directory escapes managed root".into());
    }
    Ok(dir)
}

/// Join a validated relative path below `dir`, refusing symlinked/junction ancestors
/// and symlinked targets. Optionally create the parent directories.
fn safe_join(dir: &Path, rel: &str, create_parents: bool) -> Res<PathBuf> {
    refuse_symlink(dir)?;
    let comps = validate_rel(rel)?;
    let (name, parents) = comps.split_last().ok_or("empty path")?;
    let parent = if create_parents {
        ensure_real_dir(dir, parents)?
    } else {
        let mut p = dir.to_path_buf();
        for c in parents {
            p.push(c);
            refuse_symlink(&p)?;
        }
        p
    };
    let target = parent.join(name);
    refuse_symlink(&target)?;
    Ok(target)
}

fn part_path(dest: &Path) -> PathBuf {
    let mut s = dest.as_os_str().to_owned();
    s.push(".part");
    PathBuf::from(s)
}

fn read_manifest(dir: &Path) -> Option<Manifest> {
    refuse_symlink(dir).ok()?;
    let p = dir.join(MANIFEST_NAME);
    let m = fs::symlink_metadata(&p).ok()?;
    if !m.is_file() || m.len() > 1024 * 1024 {
        return None;
    }
    refuse_symlink(&p).ok()?;
    let m: Manifest = serde_json::from_slice(&fs::read(&p).ok()?).ok()?;
    (m.owner == OWNER).then_some(m)
}

fn write_manifest_atomic(dir: &Path, m: &Manifest) -> Res<()> {
    refuse_symlink(dir)?;
    let tmp = dir.join(format!("{MANIFEST_NAME}.tmp"));
    let final_dest = dir.join(MANIFEST_NAME);
    refuse_symlink(&tmp)?;
    refuse_symlink(&final_dest)?;
    let data = serde_json::to_vec_pretty(m).map_err(|e| e.to_string())?;
    {
        let mut opts = fs::OpenOptions::new();
        opts.write(true).create(true).truncate(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            opts.custom_flags(libc::O_NOFOLLOW);
        }
        let mut f = opts.open(&tmp).map_err(|e| format!("manifest write: {e}"))?;
        f.write_all(&data).map_err(|e| format!("manifest write: {e}"))?;
        f.sync_all().map_err(|e| format!("manifest sync: {e}"))?;
    }
    refuse_symlink(&tmp)?;
    refuse_symlink(&final_dest)?;
    fs::rename(&tmp, &final_dest).map_err(|e| format!("manifest commit: {e}"))
}

// ---------------------------------------------------------------- download

#[derive(Clone, Debug, Serialize)]
pub struct DiskPreflight {
    /// Additional bytes, including a 1 MiB manifest reserve. Unknown sizes use
    /// the configured per-file upper bound rather than claiming an exact size.
    pub required_bytes: u64,
    pub available_bytes: u64,
    pub resumable_bytes: u64,
    pub published_sizes_known: bool,
    pub sufficient: bool,
    pub hardware: Option<hardware::MemoryPreflight>,
}

fn available_disk_bytes(path: &Path) -> Res<u64> {
    fs2::available_space(path).map_err(|error| format!("disk free space: {error}"))
}

fn disk_requirement(cfg: &Config, dir: &Path, selected: &[&RepoFile], available_bytes: u64) -> Res<DiskPreflight> {
    let mut required_bytes = 1024 * 1024u64;
    let mut resumable_bytes = 0u64;
    let mut published_sizes_known = true;
    for file in selected {
        let size = file.size.unwrap_or(cfg.max_file_bytes);
        published_sizes_known &= file.size.is_some();
        if size > cfg.max_file_bytes { return Err("file exceeds maximum allowed size".into()); }
        let partial = part_path(&safe_join(dir, &file.path, false)?);
        refuse_symlink(&partial)?;
        let existing = match fs::symlink_metadata(&partial) {
            Ok(md) if md.is_file() && md.len() <= size => md.len(),
            Ok(md) if md.is_file() => 0,
            Ok(_) => return Err("partial file is not a regular file".into()),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => 0,
            Err(e) => return Err(format!("partial file: {e}")),
        };
        resumable_bytes = resumable_bytes.checked_add(existing).ok_or("disk size overflow")?;
        required_bytes = required_bytes.checked_add(size - existing).ok_or("disk size overflow")?;
    }
    Ok(DiskPreflight { required_bytes, available_bytes, resumable_bytes, published_sizes_known, sufficient: available_bytes >= required_bytes, hardware: None })
}

pub fn disk_preflight(cfg: &Config, info: &RepoInfo, bundle: &BundleMatch) -> Res<DiskPreflight> {
    validate_repo_id(&info.repo_id)?;
    validate_revision_sha(&info.revision)?;
    if bundle.files.is_empty() { return Err("bundle has no files".into()); }
    let unique: std::collections::HashSet<_> = bundle.files.iter().collect();
    if unique.len() != bundle.files.len() { return Err("bundle contains duplicate files".into()); }
    let selected: Vec<&RepoFile> = bundle.files.iter().map(|name| {
        info.files.iter().find(|file| &file.path == name).ok_or_else(|| format!("file not in repository: {name}"))
    }).collect::<Res<_>>()?;
    let dir = model_dir_path(cfg, bundle.kind, &info.repo_id, &info.revision);
    for ancestor in dir.ancestors() {
        refuse_symlink(ancestor)?;
        if ancestor == cfg.models_root { break; }
    }
    let disk_path = dir.ancestors().find(|path| path.is_dir()).ok_or("no existing model storage ancestor")?;
    refuse_symlink(disk_path)?;
    disk_requirement(cfg, &dir, &selected, available_disk_bytes(disk_path)?)
}

#[derive(Clone, Debug, Serialize)]
pub struct DownloadProgress {
    pub file: String,
    pub file_index: usize,
    pub file_count: usize,
    pub downloaded: u64,
    /// Sum of published sizes (0 when unknown).
    pub total: u64,
}

fn hash_existing(path: &Path, hasher: &mut Sha256) -> Res<()> {
    let mut f = fs::File::open(path).map_err(|e| format!("open partial: {e}"))?;
    let mut buf = vec![0u8; 1 << 20];
    loop {
        let n = f.read(&mut buf).map_err(|e| format!("read partial: {e}"))?;
        if n == 0 {
            return Ok(());
        }
        hasher.update(&buf[..n]);
    }
}

#[allow(clippy::too_many_arguments)]
async fn download_file(
    client: &reqwest::Client,
    cfg: &Config,
    url: Url,
    dest: &Path,
    expected_size: Option<u64>,
    expected_sha: Option<&str>,
    cancel: &AtomicBool,
    on_bytes: &mut (dyn FnMut(u64) + Send),
) -> Res<(u64, String)> {
    let part = part_path(dest);
    let mut existing = match fs::symlink_metadata(&part) {
        Ok(m) if m.is_file() => m.len(),
        Ok(_) => return Err("partial file is not a regular file".into()),
        Err(_) => 0,
    };
    if expected_size.is_some_and(|s| existing > s) {
        let _ = fs::remove_file(&part);
        existing = 0;
    }
    let mut hasher = Sha256::new();
    if existing > 0 {
        hash_existing(&part, &mut hasher)?;
    }
    let mut written = existing;
    let complete_already = expected_size == Some(existing) && existing > 0;

    if !complete_already {
        if cancel.load(Ordering::Relaxed) {
            return Err(CANCELLED.into());
        }
        let resp = fetch(client, cfg, url, (existing > 0).then_some(existing), cancel).await?;
        let status = resp.status().as_u16();
        let mut resume = false;
        match status {
            206 if existing > 0 => {
                let ok = resp
                    .headers()
                    .get(reqwest::header::CONTENT_RANGE)
                    .and_then(|v| v.to_str().ok())
                    .is_some_and(|v| v.starts_with(&format!("bytes {existing}-")));
                if !ok {
                    return Err("server returned an unexpected content range".into());
                }
                resume = true;
            }
            200 => {}
            416 => {
                let _ = fs::remove_file(&part);
                return Err("range not satisfiable; partial discarded, retry".into());
            }
            s => return Err(format!("download failed: HTTP {s}")),
        }
        if !resume {
            existing = 0;
            written = 0;
            hasher = Sha256::new();
        }
        if let Some(len) = resp.content_length() {
            let total = existing + len;
            if total > cfg.max_file_bytes {
                return Err("file exceeds maximum allowed size".into());
            }
            if expected_size.is_some_and(|s| s != total) {
                return Err("server size differs from published size".into());
            }
        }
        let mut opts = fs::OpenOptions::new();
        opts.write(true).create(true);
        if resume {
            opts.append(true);
        } else {
            opts.truncate(true);
        }
        let mut file = opts.open(&part).map_err(|e| format!("open partial: {e}"))?;
        let limit = expected_size.unwrap_or(cfg.max_file_bytes).min(cfg.max_file_bytes);
        let mut resp = resp;
        on_bytes(written);
        loop {
            if cancel.load(Ordering::Relaxed) {
                let _ = file.flush();
                return Err(CANCELLED.into());
            }
            let chunk_fut = resp.chunk();
            tokio::pin!(chunk_fut);
            let deadline = tokio::time::Instant::now() + cfg.chunk_timeout;
            let timeout_fut = tokio::time::sleep_until(deadline);
            tokio::pin!(timeout_fut);
            let chunk = loop {
                if cancel.load(Ordering::Relaxed) {
                    let _ = file.flush();
                    return Err(CANCELLED.into());
                }
                tokio::select! {
                    biased;
                    _ = &mut timeout_fut => {
                        let _ = file.flush();
                        return Err("download stalled".into());
                    }
                    res = &mut chunk_fut => {
                        match res {
                            Err(e) => {
                                let _ = file.flush();
                                return Err(format!("download interrupted: {e}"));
                            }
                            Ok(c) => break c,
                        }
                    }
                    _ = tokio::time::sleep(Duration::from_millis(25)) => {
                        if cancel.load(Ordering::Relaxed) {
                            let _ = file.flush();
                            return Err(CANCELLED.into());
                        }
                    }
                }
            };
            match chunk {
                None => break,
                Some(b) => {
                    written += b.len() as u64;
                    if written > limit {
                        drop(file);
                        let _ = fs::remove_file(&part);
                        return Err("download exceeded expected size".into());
                    }
                    hasher.update(&b);
                    file.write_all(&b).map_err(|e| format!("write: {e}"))?;
                    on_bytes(written);
                }
            }
        }
        file.flush().map_err(|e| format!("flush: {e}"))?;
        file.sync_all().map_err(|e| format!("sync: {e}"))?;
    }

    if expected_size.is_some_and(|s| s != written) {
        let _ = fs::remove_file(&part);
        return Err("downloaded size does not match published size".into());
    }
    let digest = hex(&hasher.finalize());
    if let Some(want) = expected_sha {
        if !want.eq_ignore_ascii_case(&digest) {
            let _ = fs::remove_file(&part);
            return Err(format!("sha256 mismatch for {}", dest.file_name().and_then(|n| n.to_str()).unwrap_or("file")));
        }
    }
    fs::rename(&part, dest).map_err(|e| format!("finalize: {e}"))?;
    Ok((written, digest))
}

/// Download one bundle into the managed directory. Re-running resumes partial
/// files; a complete manifest is written atomically only after every file is
/// verified. Cancel keeps `.part` files for resume.
pub async fn download_model(
    cfg: &Config,
    info: &RepoInfo,
    bundle: &BundleMatch,
    cancel: Arc<AtomicBool>,
    progress: &(dyn Fn(DownloadProgress) + Send + Sync),
) -> Res<Manifest> {
    validate_repo_id(&info.repo_id)?;
    validate_revision_sha(&info.revision)?;
    if bundle.files.is_empty() {
        return Err("bundle has no files".into());
    }
    let mut selected = Vec::new();
    for f in &bundle.files {
        let rf = info.files.iter().find(|x| &x.path == f).ok_or_else(|| format!("file not in repository: {f}"))?;
        validate_rel(&rf.path)?;
        selected.push(rf);
    }
    let dir = prepare_model_dir(cfg, bundle.kind, &info.repo_id, &info.revision)?;
    if let Some(m) = read_manifest(&dir) {
        if m.complete && m.repo_id == info.repo_id && m.revision == info.revision {
            let mut installed: Vec<_> = m.files.iter().map(|file| file.path.as_str()).collect();
            let mut requested: Vec<_> = bundle.files.iter().map(String::as_str).collect();
            installed.sort(); requested.sort();
            if m.bundle_id != bundle.descriptor_id || m.kind != bundle.kind || installed != requested {
                return Err("A different model variant is already installed at this revision. Unload and remove it before downloading another variant.".into());
            }
            let mut all_valid = true;
            for f in &m.files {
                let p = match safe_join(&dir, &f.path, false) {
                    Ok(p) => p,
                    Err(_) => {
                        all_valid = false;
                        break;
                    }
                };
                match fs::symlink_metadata(&p) {
                    Ok(md) if md.is_file() && md.len() == f.size => {}
                    _ => {
                        all_valid = false;
                        break;
                    }
                }
            }
            if all_valid {
                return Ok(m);
            }
        }
    }
    let disk = disk_requirement(cfg, &dir, &selected, available_disk_bytes(&dir)?)?;
    if !disk.sufficient {
        return Err(format!("insufficient disk space: need {} additional bytes; {} bytes available", disk.required_bytes, disk.available_bytes));
    }
    let client = client(cfg)?;
    let total: u64 = selected.iter().filter_map(|f| f.size).sum();
    let count = selected.len();
    let mut before = 0u64;
    let mut files = Vec::new();
    for (i, f) in selected.iter().enumerate() {
        let dest = safe_join(&dir, &f.path, true)?;
        let url = resolve_url(cfg, &info.repo_id, &info.revision, &f.path)?;
        let base = before;
        let name = f.path.clone();
        let mut cb = |n: u64| progress(DownloadProgress { file: name.clone(), file_index: i, file_count: count, downloaded: base + n, total });
        let (size, sha) = download_file(&client, cfg, url, &dest, f.size, f.sha256.as_deref(), &cancel, &mut cb).await?;
        before += size;
        files.push(ManifestFile { path: f.path.clone(), size, sha256: sha });
    }
    let manifest = Manifest {
        owner: OWNER.into(),
        complete: true,
        repo_id: info.repo_id.clone(),
        revision: info.revision.clone(),
        kind: bundle.kind,
        bundle_id: bundle.descriptor_id.clone(),
        license: info.license.clone(),
        files,
    };
    write_manifest_atomic(&dir, &manifest)?;
    Ok(manifest)
}

/// Delete only what the owned manifest lists (plus its `.part` leftovers and
/// the manifest), then prune directories that became empty. Unknown files in
/// the model directory are never touched.
pub fn delete_model(cfg: &Config, kind: ModelKind, repo_id: &str, revision: &str) -> Res<()> {
    validate_repo_id(repo_id)?;
    validate_revision_sha(revision)?;
    let dir = model_dir_path(cfg, kind, repo_id, revision);
    let name = repo_dir_name(repo_id);
    // Refuse symlinked ancestors before reading anything.
    let mut walk = cfg.models_root.clone();
    for c in [kind.dir(), name.as_str(), revision] {
        walk.push(c);
        match fs::symlink_metadata(&walk) {
            Ok(m) if m.file_type().is_symlink() => return Err(format!("symlink refused: {}", walk.display())),
            Ok(_) => {}
            Err(_) => return Err("model is not installed".into()),
        }
    }
    let m = read_manifest(&dir).ok_or("no owned manifest; refusing to delete")?;
    if m.repo_id != repo_id || m.revision != revision || m.kind != kind {
        return Err("manifest does not match requested model".into());
    }
    let mut paths = Vec::new();
    for f in &m.files {
        let target = safe_join(&dir, &f.path, false)?;
        paths.push((part_path(&target), true));
        paths.push((target, false));
    }
    for (p, optional) in &paths {
        match fs::symlink_metadata(p) {
            Ok(md) if md.is_file() => fs::remove_file(p).map_err(|e| format!("delete: {e}"))?,
            Ok(_) => return Err(format!("not a regular file: {}", p.display())),
            Err(_) if *optional => {}
            Err(_) => {}
        }
    }
    let _ = fs::remove_file(dir.join(MANIFEST_NAME));
    let _ = fs::remove_file(dir.join(format!("{MANIFEST_NAME}.tmp")));
    // Prune empty directories bottom-up, never above the kind directory.
    let mut dirs: Vec<PathBuf> = paths.iter().filter_map(|(p, _)| p.parent().map(Path::to_path_buf)).collect();
    dirs.sort_by_key(|d| std::cmp::Reverse(d.components().count()));
    dirs.dedup();
    for d in dirs {
        let mut cur = d;
        while cur.starts_with(&dir) {
            if fs::remove_dir(&cur).is_err() {
                break;
            }
            if !cur.pop() {
                break;
            }
        }
    }
    for d in [dir.clone(), dir.parent().map(Path::to_path_buf).unwrap_or_default()] {
        let _ = fs::remove_dir(&d);
    }
    Ok(())
}

/// Complete, owned installations under the managed root.
pub fn list_installed(cfg: &Config) -> Vec<Manifest> {
    let mut out = Vec::new();
    let subdirs = |p: &Path| -> Vec<PathBuf> {
        fs::read_dir(p)
            .map(|rd| rd.filter_map(|e| e.ok()).filter(|e| e.file_type().map(|t| t.is_dir() && !t.is_symlink()).unwrap_or(false)).map(|e| e.path()).collect())
            .unwrap_or_default()
    };
    for kind in ModelKind::ALL {
        let kind_dir = cfg.models_root.join(kind.dir());
        if refuse_symlink(&kind_dir).is_err() {
            continue;
        }
        for repo in subdirs(&kind_dir) {
            if refuse_symlink(&repo).is_err() {
                continue;
            }
            for rev in subdirs(&repo) {
                if refuse_symlink(&rev).is_err() {
                    continue;
                }
                if let Some(m) = read_manifest(&rev) {
                    if m.complete && m.kind == kind {
                        let mut all_valid = true;
                        for f in &m.files {
                            let p = match safe_join(&rev, &f.path, false) {
                                Ok(p) => p,
                                Err(_) => {
                                    all_valid = false;
                                    break;
                                }
                            };
                            match fs::symlink_metadata(&p) {
                                Ok(md) if md.is_file() && md.len() == f.size => {}
                                _ => {
                                    all_valid = false;
                                    break;
                                }
                            }
                        }
                        if all_valid {
                            out.push(m);
                        }
                    }
                }
            }
        }
    }
    out
}

#[derive(Clone, Default)]
pub struct ModelDownloadsState {
    pub(crate) cancel_handles: Arc<std::sync::Mutex<std::collections::HashMap<String, Arc<AtomicBool>>>>,
    pub(crate) active_downloads: Arc<std::sync::Mutex<std::collections::HashSet<String>>>,
    pub(crate) early_cancels: Arc<std::sync::Mutex<std::collections::HashSet<String>>>,
    quit_activity: Arc<std::sync::Mutex<DownloadQuitActivity>>,
}

#[derive(Default)]
struct DownloadQuitActivity {
    next_id: u64,
    active: std::collections::HashSet<u64>,
    exiting: bool,
}

impl ModelDownloadsState {
    /// Per-task identities include metadata fetches and never depend on renderer lifetime.
    pub fn quit_snapshot(&self) -> Res<Vec<u64>> {
        let activity = self.quit_activity.lock().map_err(|_| "Download activity is unavailable")?;
        let mut active: Vec<u64> = activity.active.iter().copied().collect();
        active.sort_unstable();
        Ok(active)
    }

    /// Atomically check the acknowledged tasks and prevent new work before native exit.
    pub fn authorize_quit(&self, acknowledged: &[u64]) -> Res<()> {
        let mut activity = self.quit_activity.lock().map_err(|_| "Download activity is unavailable")?;
        if !activity.active.iter().all(|id| acknowledged.contains(id)) {
            return Err("New model downloads started; review Quit again".into());
        }
        activity.exiting = true;
        Ok(())
    }

    pub fn cancel(&self, id: &str) -> bool {
        let mut found = false;
        if let Some(flag) = self.cancel_handles.lock().unwrap().get(id) {
            flag.store(true, Ordering::Relaxed);
            found = true;
        }
        if validate_request_id(id).is_ok() {
            let mut early = self.early_cancels.lock().unwrap();
            if early.len() >= 256 {
                early.clear();
            }
            early.insert(id.to_string());
            true
        } else {
            found
        }
    }
}

fn managed_models_dir() -> PathBuf {
    let base = std::env::var_os("JARVIS_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(|| std::env::current_dir().expect("application directory"));
    if std::env::var("JARVIS_DESKTOP_TEST").is_ok() {
        base.join(".cache/desktop-test/models")
    } else {
        base.join("data/models")
    }
}

pub fn default_config() -> Config {
    Config::new(managed_models_dir())
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct ModelDownloadRequest {
    pub kind: ModelKind,
    pub repo_id: String,
    pub revision: Option<String>,
    pub descriptor_id: String,
    #[serde(default)]
    pub files: Vec<String>,
    #[serde(default)]
    pub request_id: Option<String>,
}

#[derive(Clone, Serialize)]
pub struct ModelDownloadProgressEvent {
    pub task_id: String,
    pub file: String,
    pub file_index: usize,
    pub file_count: usize,
    pub downloaded: u64,
    pub total: u64,
}

#[tauri::command]
pub fn model_bundle_descriptors() -> Vec<BundleDescriptor> {
    bundle_descriptors()
}

#[tauri::command]
pub async fn model_fetch_repo_info(repo_id: String, revision: Option<String>) -> Result<RepoInfo, String> {
    let cfg = default_config();
    fetch_repo_info(&cfg, &repo_id, revision.as_deref()).await
}

#[tauri::command]
pub fn model_match_bundles(kind: ModelKind, files: Vec<RepoFile>, projector_path: Option<String>) -> Vec<BundleMatch> {
    match_bundles_with_projector(kind, &files, projector_path.as_deref())
}

#[tauri::command]
pub async fn model_disk_preflight(info: RepoInfo, bundle: BundleMatch) -> Result<DiskPreflight, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let mut result = disk_preflight(&default_config(), &info, &bundle)?;
        let sizes: Vec<Option<u64>> = bundle.files.iter().map(|name| info.files.iter().find(|file| &file.path == name).and_then(|file| file.size)).collect();
        result.hardware = Some(hardware::inspect(&sizes));
        Ok(result)
    }).await.map_err(|error| format!("model preflight: {error}"))?
}

#[tauri::command]
pub fn model_list_installed() -> Vec<Manifest> {
    let cfg = default_config();
    list_installed(&cfg)
}

#[tauri::command]
pub fn model_delete(kind: ModelKind, repo_id: String, revision: String) -> Result<(), String> {
    let cfg = default_config();
    delete_model(&cfg, kind, &repo_id, &revision)
}

#[tauri::command]
pub fn model_download_cancel(
    task_id: String,
    state: tauri::State<'_, ModelDownloadsState>,
) -> bool {
    state.cancel(&task_id)
}

struct DownloadRegistration {
    state: ModelDownloadsState,
    cancel_keys: Vec<String>,
    active_keys: Vec<String>,
    activity_id: u64,
}

impl DownloadRegistration {
    fn new(state: ModelDownloadsState) -> Res<Self> {
        let activity_id = {
            let mut activity = state.quit_activity.lock().map_err(|_| "Download activity is unavailable")?;
            if activity.exiting { return Err("Jarvis is quitting".into()); }
            // IDs cross JavaScript IPC, so remain exact safe integers.
            let id = activity.next_id.checked_add(1).filter(|id| *id <= 9_007_199_254_740_991)
                .ok_or("Download identity limit reached")?;
            activity.next_id = id;
            activity.active.insert(id);
            id
        };
        Ok(Self {
            state,
            cancel_keys: Vec::new(),
            active_keys: Vec::new(),
            activity_id,
        })
    }

    fn register_cancel(&mut self, key: String, cancel: Arc<AtomicBool>) {
        self.state.cancel_handles.lock().unwrap().insert(key.clone(), cancel);
        self.cancel_keys.push(key);
    }

    fn register_active(&mut self, key: String) -> Res<()> {
        let mut active = self.state.active_downloads.lock().unwrap();
        if active.contains(&key) {
            return Err(format!("download already in progress: {key}"));
        }
        active.insert(key.clone());
        self.active_keys.push(key);
        Ok(())
    }
}

impl Drop for DownloadRegistration {
    fn drop(&mut self) {
        // Poison stays visible to quit_snapshot/authorize_quit: never report false idle.
        if let Ok(mut activity) = self.state.quit_activity.lock() {
            activity.active.remove(&self.activity_id);
        }
        if !self.cancel_keys.is_empty() {
            let mut handles = self.state.cancel_handles.lock().unwrap();
            for k in &self.cancel_keys {
                handles.remove(k);
            }
        }
        if !self.active_keys.is_empty() {
            let mut active = self.state.active_downloads.lock().unwrap();
            for k in &self.active_keys {
                active.remove(k);
            }
        }
    }
}

#[cfg(test)]
mod quit_activity_tests {
    use super::*;

    #[test]
    fn metadata_tasks_are_tracked_until_each_registration_finishes() {
        let state = ModelDownloadsState::default();
        let first = DownloadRegistration::new(state.clone()).unwrap();
        let second = DownloadRegistration::new(state.clone()).unwrap();
        let original = state.quit_snapshot().unwrap();
        assert_eq!(original.len(), 2);
        drop(first);
        let remaining = state.quit_snapshot().unwrap();
        assert_eq!(remaining.len(), 1);
        assert!(original.contains(&remaining[0]));
        drop(second);
        assert!(state.quit_snapshot().unwrap().is_empty());
    }

    #[test]
    fn new_task_identity_vetoes_stale_quit_and_authorization_seals_registration() {
        let state = ModelDownloadsState::default();
        let first = DownloadRegistration::new(state.clone()).unwrap();
        let original = state.quit_snapshot().unwrap();
        drop(first);
        let second = DownloadRegistration::new(state.clone()).unwrap();
        assert!(state.authorize_quit(&original).is_err());
        assert!(state.authorize_quit(&state.quit_snapshot().unwrap()).is_ok());
        assert!(DownloadRegistration::new(state.clone()).is_err());
        drop(second);
    }

    #[test]
    fn poisoned_activity_never_becomes_an_idle_snapshot_or_quit_authorization() {
        let state = ModelDownloadsState::default();
        let shared = state.clone();
        let _ = std::thread::spawn(move || {
            let _lock = shared.quit_activity.lock().unwrap();
            panic!("synthetic activity failure");
        }).join();
        assert!(state.quit_snapshot().is_err());
        assert!(state.authorize_quit(&[]).is_err());
        assert!(DownloadRegistration::new(state).is_err());
    }
}

pub async fn start_download_task<F>(
    cfg: &Config,
    state: &ModelDownloadsState,
    request: ModelDownloadRequest,
    on_progress: F,
) -> Result<Manifest, String>
where
    F: Fn(ModelDownloadProgressEvent) + Send + Sync + 'static,
{
    validate_repo_id(&request.repo_id)?;
    if let Some(ref rev) = request.revision {
        validate_revision_name(rev)?;
    }

    let cancel = Arc::new(AtomicBool::new(false));
    let mut reg = DownloadRegistration::new(state.clone())?;

    if let Some(ref req_id) = request.request_id {
        validate_request_id(req_id)?;
        if reg.state.early_cancels.lock().unwrap().remove(req_id) {
            cancel.store(true, Ordering::Relaxed);
        }
        reg.register_active(req_id.clone())?;
        reg.register_cancel(req_id.clone(), cancel.clone());
    }

    if cancel.load(Ordering::Relaxed) {
        return Err(CANCELLED.into());
    }

    let info = fetch_repo_info_cancellable(cfg, &request.repo_id, request.revision.as_deref(), &cancel).await?;

    if cancel.load(Ordering::Relaxed) {
        return Err(CANCELLED.into());
    }

    let projector = request.files.iter().find(|path| is_mmproj(path));
    let matches = match_bundles_with_projector(request.kind, &info.files, projector.map(String::as_str));
    let matching_bundles: Vec<BundleMatch> = matches
        .into_iter()
        .filter(|b| b.descriptor_id == request.descriptor_id)
        .collect();

    if matching_bundles.is_empty() {
        return Err(format!("bundle not available for repo: {}", request.descriptor_id));
    }

    let bundle = if !request.files.is_empty() {
        let mut req_sorted = request.files.clone();
        req_sorted.sort();
        matching_bundles
            .into_iter()
            .find(|b| {
                let mut b_sorted = b.files.clone();
                b_sorted.sort();
                b_sorted == req_sorted
            })
            .ok_or_else(|| {
                format!(
                    "selected files do not match any available bundle for descriptor {}: {:?}",
                    request.descriptor_id, request.files
                )
            })?
    } else if matching_bundles.len() == 1 {
        matching_bundles.into_iter().next().unwrap()
    } else {
        return Err(format!(
            "multiple variants found for descriptor {}; explicit selected files required",
            request.descriptor_id
        ));
    };

    let target_id = format!("{}:{}:{}", request.kind.dir(), request.repo_id, info.revision);
    reg.register_active(target_id.clone())?;
    reg.register_cancel(target_id.clone(), cancel.clone());

    let task_id_progress = request.request_id.clone().unwrap_or_else(|| target_id.clone());
    let progress_cb = Arc::new(on_progress);
    let cb_clone = progress_cb.clone();
    let res = download_model(cfg, &info, &bundle, cancel, &move |p| {
        cb_clone(ModelDownloadProgressEvent {
            task_id: task_id_progress.clone(),
            file: p.file,
            file_index: p.file_index,
            file_count: p.file_count,
            downloaded: p.downloaded,
            total: p.total,
        });
    })
    .await;

    res
}

#[tauri::command]
pub async fn model_download_start(
    app: tauri::AppHandle,
    state: tauri::State<'_, ModelDownloadsState>,
    request: ModelDownloadRequest,
) -> Result<Manifest, String> {
    let cfg = default_config();
    let app_handle = app.clone();
    start_download_task(&cfg, state.inner(), request, move |ev| {
        let _ = app_handle.emit("model-download:progress", ev);
    })
    .await
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Read, Write};
    use std::sync::Mutex;

    #[test]
    fn disk_preflight_credits_only_valid_partial_files_and_reserves_manifest() {
        let temp = tempfile::tempdir().unwrap();
        let cfg = Config::new(temp.path().to_path_buf());
        let file = RepoFile { path: "model.gguf".into(), size: Some(100), sha256: None };
        fs::write(temp.path().join("model.gguf"), [0u8; 100]).unwrap();
        fs::write(temp.path().join("model.gguf.part"), [0u8; 40]).unwrap();
        let reserve = 1024 * 1024;
        let disk = disk_requirement(&cfg, temp.path(), &[&file], reserve + 59).unwrap();
        assert_eq!(disk.required_bytes, reserve + 60);
        assert_eq!(disk.resumable_bytes, 40);
        assert!(disk.published_sizes_known);
        assert!(!disk.sufficient);
        fs::write(temp.path().join("model.gguf.part"), [0u8; 101]).unwrap();
        assert_eq!(disk_requirement(&cfg, temp.path(), &[&file], u64::MAX).unwrap().required_bytes, reserve + 100);
    }

    #[test]
    fn disk_preflight_rejects_duplicate_selected_files_before_counting_bytes() {
        let temp = tempfile::tempdir().unwrap();
        let cfg = Config::new(temp.path().to_path_buf());
        let file = RepoFile { path: "model.gguf".into(), size: Some(100), sha256: None };
        let info = RepoInfo { repo_id: "org/model".into(), revision: "a".repeat(40), license: None, gated: false, files: vec![file.clone()], total_size: 100 };
        let bundle = BundleMatch { descriptor_id: "llm-gguf".into(), kind: ModelKind::Llm, files: vec![file.path.clone(),file.path] };
        assert!(disk_preflight(&cfg,&info,&bundle).unwrap_err().contains("duplicate"));
    }

    #[test]
    fn disk_preflight_handles_unknown_sizes_overflow_and_missing_storage() {
        let temp = tempfile::tempdir().unwrap();
        let mut cfg = Config::new(temp.path().join("not-created"));
        cfg.max_file_bytes = 1000;
        let file = RepoFile { path: "model.gguf".into(), size: None, sha256: None };
        let info = RepoInfo { repo_id: "org/model".into(), revision: "a".repeat(40), license: None, gated: false, files: vec![file.clone()], total_size: 0 };
        let bundle = BundleMatch { descriptor_id: "llm-gguf".into(), kind: ModelKind::Llm, files: vec![file.path.clone()] };
        let disk = disk_preflight(&cfg, &info, &bundle).unwrap();
        assert_eq!(disk.required_bytes, 1024 * 1024 + 1000);
        assert!(!disk.published_sizes_known);
        assert!(!cfg.models_root.exists());
        cfg.max_file_bytes = u64::MAX;
        assert!(disk_requirement(&cfg, temp.path(), &[&file], u64::MAX).unwrap_err().contains("overflow"));
    }

    fn body() -> Vec<u8> {
        (0..=255u8).cycle().take(5000).collect()
    }
    fn sha(b: &[u8]) -> String {
        hex(&Sha256::digest(b))
    }
    fn rev() -> String {
        "a".repeat(40)
    }
    fn http(status: &str, headers: &[(&str, String)], body: &[u8]) -> Vec<u8> {
        let mut h = format!("HTTP/1.1 {status}\r\nContent-Length: {}\r\nConnection: close\r\n", body.len());
        for (k, v) in headers {
            h.push_str(&format!("{k}: {v}\r\n"));
        }
        h.push_str("\r\n");
        let mut v = h.into_bytes();
        v.extend_from_slice(body);
        v
    }
    fn spawn_server<F>(handler: F) -> u16
    where
        F: Fn(&str, Option<u64>) -> Vec<u8> + Send + Sync + 'static,
    {
        spawn_raw_server(move |path, range, stream| {
            let res = handler(path, range);
            let _ = stream.write_all(&res);
        })
    }

    fn spawn_raw_server<F>(handler: F) -> u16
    where
        F: Fn(&str, Option<u64>, &mut std::net::TcpStream) + Send + Sync + 'static,
    {
        let l = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let port = l.local_addr().unwrap().port();
        let h = Arc::new(handler);
        std::thread::spawn(move || {
            for s in l.incoming() {
                let Ok(mut s) = s else { continue };
                let h = h.clone();
                std::thread::spawn(move || {
                    let mut buf = Vec::new();
                    let mut b = [0u8; 1024];
                    loop {
                        match s.read(&mut b) {
                            Ok(0) | Err(_) => break,
                            Ok(n) => {
                                buf.extend_from_slice(&b[..n]);
                                if buf.windows(4).any(|w| w == b"\r\n\r\n") {
                                    break;
                                }
                            }
                        }
                    }
                    let text = String::from_utf8_lossy(&buf).to_string();
                    let path = text.lines().next().and_then(|l| l.split(' ').nth(1)).unwrap_or("").to_string();
                    let range = text.lines().find_map(|l| {
                        let l = l.to_ascii_lowercase();
                        l.strip_prefix("range: bytes=").and_then(|r| r.trim_end_matches('-').parse().ok())
                    });
                    h(&path, range, &mut s);
                });
            }
        });
        port
    }
    fn cfg(root: &Path, port: u16) -> Config {
        let mut c = Config::new(root.to_path_buf());
        c.hub_base = format!("http://127.0.0.1:{port}");
        c.allow_loopback_http = true;
        c.chunk_timeout = Duration::from_secs(5);
        c
    }
    fn info(sha256: Option<String>) -> RepoInfo {
        RepoInfo {
            repo_id: "org/model".into(),
            revision: rev(),
            license: Some("apache-2.0".into()),
            gated: false,
            files: vec![RepoFile { path: "model.gguf".into(), size: Some(5000), sha256 }],
            total_size: 5000,
        }
    }
    fn bundle() -> BundleMatch {
        BundleMatch { descriptor_id: "llm-gguf".into(), kind: ModelKind::Llm, files: vec!["model.gguf".into()] }
    }
    fn file_path() -> String {
        format!("/org/model/resolve/{}/model.gguf", rev())
    }
    fn ranged_server(ranges: Arc<Mutex<Vec<Option<u64>>>>) -> u16 {
        let data = body();
        let fp = file_path();
        spawn_server(move |path, range| {
            if path != fp {
                return http("404 Not Found", &[], b"");
            }
            ranges.lock().unwrap().push(range);
            match range {
                Some(n) => http("206 Partial Content", &[("Content-Range", format!("bytes {n}-4999/5000"))], &data[n as usize..]),
                None => http("200 OK", &[], &data),
            }
        })
    }

    #[test]
    fn parses_metadata_with_revision_license_and_checksums() {
        let json = format!(
            r#"{{"sha":"{}","tags":["license:mit"],"gated":false,"siblings":[
            {{"rfilename":"a.gguf","size":10,"lfs":{{"sha256":"{}"}}}},
            {{"rfilename":"../evil","size":1}},{{"rfilename":"README.md","size":5}}]}}"#,
            rev(),
            "B".repeat(64)
        );
        let i = parse_repo_info("org/model", json.as_bytes()).unwrap();
        assert_eq!(i.revision, rev());
        assert_eq!(i.license.as_deref(), Some("mit"));
        assert_eq!(i.files.len(), 2);
        assert_eq!(i.files[0].sha256.as_deref(), Some("b".repeat(64).as_str()));
        assert_eq!(i.total_size, 15);
        assert!(parse_repo_info("org/model", br#"{"sha":"main","siblings":[]}"#).is_err());
        assert!(parse_repo_info("../x", b"{}").is_err());
    }

    #[tokio::test]
    async fn fetches_metadata_from_mock_hub() {
        let json = format!(r#"{{"sha":"{}","siblings":[{{"rfilename":"m.gguf","size":3}}]}}"#, rev());
        let port = spawn_server(move |p, _| if p.starts_with("/api/models/org/model") { http("200 OK", &[], json.as_bytes()) } else { http("404 Not Found", &[], b"") });
        let tmp = tempfile::tempdir().unwrap();
        let i = fetch_repo_info(&cfg(tmp.path(), port), "org/model", None).await.unwrap();
        assert_eq!(i.files[0].path, "m.gguf");
    }

    #[test]
    fn bundle_descriptors_are_explicit() {
        let f = |p: &str| RepoFile { path: p.into(), size: None, sha256: None };
        let files = vec![
            f("m-q4.gguf"),
            f("mmproj-f16.gguf"),
            f("big-00001-of-00002.gguf"),
            f("v.onnx"),
            f("v.onnx.json"),
            f("lone.onnx"),
            f("supertonic-voice.gguf"),
        ];
        assert_eq!(match_bundles(ModelKind::Llm, &files).len(), 2);
        assert_eq!(match_bundles(ModelKind::Embedding, &files)[0].files, vec!["m-q4.gguf"]);
        assert_eq!(match_bundles(ModelKind::Vision, &files)[0].files, vec!["m-q4.gguf", "mmproj-f16.gguf"]);
        let tts = match_bundles(ModelKind::Tts, &files);
        assert_eq!(tts.len(), 2);
        assert_eq!(tts[0].descriptor_id, "tts-supertonic-gguf");
        assert_eq!(tts[0].files, vec!["supertonic-voice.gguf"]);
        assert_eq!(tts[1].descriptor_id, "tts-piper-onnx");
        assert_eq!(tts[1].files, vec!["v.onnx", "v.onnx.json"]);
        assert!(match_bundles(ModelKind::Vision, &[f("m.gguf")]).is_empty());
        assert_eq!(bundle_descriptors().len(), 5);
        assert!(bundle_descriptors().iter().any(|d| d.id == "tts-piper-onnx" && d.label.contains("CPU")));
    }

    #[test]
    fn complete_shards_are_one_ordered_language_model_variant() {
        let f = |p: &str| RepoFile { path: p.into(), size: Some(3), sha256: None };
        let files = vec![f("q4/m-00002-of-00002.gguf"), f("q4/m-00001-of-00002.gguf")];
        let models = match_bundles(ModelKind::Llm, &files);
        assert_eq!(models.len(), 1);
        assert_eq!(models[0].files, vec!["q4/m-00001-of-00002.gguf", "q4/m-00002-of-00002.gguf"]);
        assert!(match_bundles(ModelKind::Llm, &files[..1]).is_empty());
        assert!(match_bundles(ModelKind::Embedding, &files).is_empty());
        assert!(match_bundles(ModelKind::Llm, &[files[0].clone(), files[0].clone()]).is_empty());
        assert!(match_bundles(ModelKind::Llm, &[f("a/m-00001-of-00002.gguf"), f("b/m-00002-of-00002.gguf")]).is_empty());
    }

    #[test]
    fn vision_projector_choice_is_explicit_and_revalidated() {
        let f = |p: &str| RepoFile { path:p.into(),size:None,sha256:None };
        let files = vec![f("base.gguf"),f("mmproj-a.gguf"),f("mmproj-b.gguf")];
        assert!(match_bundles(ModelKind::Vision, &files).is_empty());
        let chosen = match_bundles_with_projector(ModelKind::Vision, &files, Some("mmproj-b.gguf"));
        assert_eq!(chosen.len(), 1);
        assert_eq!(chosen[0].files, vec!["base.gguf","mmproj-b.gguf"]);
        assert!(match_bundles_with_projector(ModelKind::Vision, &files, Some("missing.gguf")).is_empty());
        assert!(match_bundles_with_projector(ModelKind::Vision, &[f("base.gguf"),f("mmproj-00001-of-00002.gguf")], Some("mmproj-00001-of-00002.gguf")).is_empty());
    }

    #[test]
    fn rejects_unsafe_paths_and_urls() {
        for bad in ["../x", "a/../x", "/abs", "a//b", "C:/x", "a\\b", "x.part", "manifest.json", "con.txt", "a/", ""] {
            assert!(validate_rel(bad).is_err(), "{bad}");
        }
        assert!(validate_rel("sub/model.q4.gguf").is_ok());
        let c = Config::new(PathBuf::from("x"));
        for bad in ["http://huggingface.co/a", "https://evil.example/a", "https://127.0.0.1/a", "https://u:p@huggingface.co/a", "file:///etc/passwd", "https://nothuggingface.co/a"] {
            assert!(validate_url(&Url::parse(bad).unwrap(), &c).is_err(), "{bad}");
        }
        assert!(validate_url(&Url::parse("https://cdn-lfs.hf.co/a").unwrap(), &c).is_ok());
        assert!(validate_url(&Url::parse("http://127.0.0.1:1/a").unwrap(), &c).is_err());
        assert!(validate_repo_id("a/b/c").is_err());
    }

    #[tokio::test]
    async fn downloads_verifies_writes_manifest_and_deletes_only_owned() {
        let ranges = Arc::new(Mutex::new(vec![]));
        let port = ranged_server(ranges);
        let tmp = tempfile::tempdir().unwrap();
        let c = cfg(tmp.path(), port);
        let seen = Mutex::new(Vec::new());
        let m = download_model(&c, &info(Some(sha(&body()))), &bundle(), Arc::new(AtomicBool::new(false)), &|p| seen.lock().unwrap().push(p.downloaded))
            .await
            .unwrap();
        assert!(m.complete);
        assert_eq!(m.files[0].sha256, sha(&body()));
        assert_eq!(seen.lock().unwrap().last(), Some(&5000));
        let dir = model_dir_path(&c, ModelKind::Llm, "org/model", &rev());
        assert_eq!(fs::read(dir.join("model.gguf")).unwrap(), body());
        assert!(!dir.join("model.gguf.part").exists());
        assert_eq!(list_installed(&c).len(), 1);
        // Idempotent when complete.
        assert_eq!(download_model(&c, &info(None), &bundle(), Arc::new(AtomicBool::new(false)), &|_| {}).await.unwrap(), m);

        fs::write(dir.join("user-notes.txt"), b"keep").unwrap();
        delete_model(&c, ModelKind::Llm, "org/model", &rev()).unwrap();
        assert!(!dir.join("model.gguf").exists());
        assert!(!dir.join(MANIFEST_NAME).exists());
        assert_eq!(fs::read(dir.join("user-notes.txt")).unwrap(), b"keep");
        assert!(list_installed(&c).is_empty());
    }

    #[tokio::test]
    async fn resumes_partial_download_with_range() {
        let ranges = Arc::new(Mutex::new(vec![]));
        let port = ranged_server(ranges.clone());
        let tmp = tempfile::tempdir().unwrap();
        let c = cfg(tmp.path(), port);
        let dir = prepare_model_dir(&c, ModelKind::Llm, "org/model", &rev()).unwrap();
        fs::write(dir.join("model.gguf.part"), &body()[..2000]).unwrap();
        let m = download_model(&c, &info(Some(sha(&body()))), &bundle(), Arc::new(AtomicBool::new(false)), &|_| {}).await.unwrap();
        assert_eq!(m.files[0].size, 5000);
        assert_eq!(fs::read(dir.join("model.gguf")).unwrap(), body());
        assert_eq!(ranges.lock().unwrap().as_slice(), &[Some(2000)]);
    }

    #[tokio::test]
    async fn checksum_mismatch_is_rejected_without_final_file() {
        let port = ranged_server(Arc::new(Mutex::new(vec![])));
        let tmp = tempfile::tempdir().unwrap();
        let c = cfg(tmp.path(), port);
        let err = download_model(&c, &info(Some("0".repeat(64))), &bundle(), Arc::new(AtomicBool::new(false)), &|_| {}).await.unwrap_err();
        assert!(err.contains("sha256 mismatch"));
        let dir = model_dir_path(&c, ModelKind::Llm, "org/model", &rev());
        assert!(!dir.join("model.gguf").exists());
        assert!(!dir.join("model.gguf.part").exists());
        assert!(!dir.join(MANIFEST_NAME).exists());
    }

    #[tokio::test]
    async fn rejects_unsafe_redirect_and_honours_cancel() {
        let port = spawn_server(|_, _| http("302 Found", &[("Location", "http://example.com/model.gguf".into())], b""));
        let tmp = tempfile::tempdir().unwrap();
        let c = cfg(tmp.path(), port);
        let err = download_model(&c, &info(None), &bundle(), Arc::new(AtomicBool::new(false)), &|_| {}).await.unwrap_err();
        assert!(err.contains("unsafe url"), "{err}");

        let err = download_model(&c, &info(None), &bundle(), Arc::new(AtomicBool::new(true)), &|_| {}).await.unwrap_err();
        assert_eq!(err, CANCELLED);
    }

    #[tokio::test]
    async fn rejects_files_not_in_repository_or_traversing() {
        let tmp = tempfile::tempdir().unwrap();
        let c = cfg(tmp.path(), 1);
        let mut b = bundle();
        b.files = vec!["../escape.gguf".into()];
        assert!(download_model(&c, &info(None), &b, Arc::new(AtomicBool::new(false)), &|_| {}).await.is_err());
    }

    #[cfg(unix)]
    #[test]
    fn refuses_symlink_escape() {
        let tmp = tempfile::tempdir().unwrap();
        let outside = tempfile::tempdir().unwrap();
        let root = tmp.path().join("models");
        fs::create_dir_all(root.join("llm")).unwrap();
        std::os::unix::fs::symlink(outside.path(), root.join("llm").join("org__model")).unwrap();
        let c = Config::new(root);
        assert!(prepare_model_dir(&c, ModelKind::Llm, "org/model", &rev()).is_err());
        assert!(delete_model(&c, ModelKind::Llm, "org/model", &rev()).is_err());
    }

    #[tokio::test]
    async fn stalled_header_or_body_cancelled_promptly() {
        let port = spawn_server(|_, _| {
            std::thread::sleep(Duration::from_secs(10));
            http("200 OK", &[], b"hello")
        });
        let tmp = tempfile::tempdir().unwrap();
        let mut c = cfg(tmp.path(), port);
        c.connect_timeout = Duration::from_secs(5);
        let cancel = Arc::new(AtomicBool::new(false));
        let cancel_clone = cancel.clone();
        tokio::spawn(async move {
            tokio::time::sleep(Duration::from_millis(100)).await;
            cancel_clone.store(true, Ordering::Relaxed);
        });
        let start = std::time::Instant::now();
        let err = download_model(&c, &info(None), &bundle(), cancel, &|_| {}).await.unwrap_err();
        assert_eq!(err, CANCELLED);
        assert!(start.elapsed() < Duration::from_secs(3), "cancellation took too long: {:?}", start.elapsed());
    }

    #[tokio::test]
    async fn validates_manifest_files_before_skipping_download() {
        let port = ranged_server(Arc::new(Mutex::new(vec![])));
        let tmp = tempfile::tempdir().unwrap();
        let c = cfg(tmp.path(), port);
        let m = download_model(&c, &info(Some(sha(&body()))), &bundle(), Arc::new(AtomicBool::new(false)), &|_| {}).await.unwrap();
        assert!(m.complete);
        let dir = model_dir_path(&c, ModelKind::Llm, "org/model", &rev());
        assert!(dir.join("model.gguf").exists());

        // Corrupt or remove the downloaded file so complete manifest is invalid
        fs::remove_file(dir.join("model.gguf")).unwrap();
        assert!(list_installed(&c).is_empty());

        // Re-running download_model detects missing file, doesn't skip, and downloads it again
        let m2 = download_model(&c, &info(Some(sha(&body()))), &bundle(), Arc::new(AtomicBool::new(false)), &|_| {}).await.unwrap();
        assert!(m2.complete);
        assert!(dir.join("model.gguf").exists());
        assert_eq!(list_installed(&c).len(), 1);
    }

    #[tokio::test]
    async fn stalled_headers_and_body_timeout_without_cancellation() {
        // 1. Stalled headers timeout
        let port_headers = spawn_server(|_, _| {
            std::thread::sleep(Duration::from_secs(3));
            http("200 OK", &[], b"data")
        });
        let tmp = tempfile::tempdir().unwrap();
        let mut c = cfg(tmp.path(), port_headers);
        c.connect_timeout = Duration::from_millis(150);
        let start = std::time::Instant::now();
        let err = download_model(&c, &info(None), &bundle(), Arc::new(AtomicBool::new(false)), &|_| {}).await.unwrap_err();
        assert!(err.contains("timed out") || err.contains("connection/headers timed out"), "actual err: {err}");
        assert!(start.elapsed() < Duration::from_secs(2), "elapsed: {:?}", start.elapsed());

        // 2. Stalled body timeout
        let port_body = spawn_raw_server(|_, _, stream| {
            let h = "HTTP/1.1 200 OK\r\nContent-Length: 5000\r\nConnection: close\r\n\r\n";
            let _ = stream.write_all(h.as_bytes());
            let _ = stream.flush();
            std::thread::sleep(Duration::from_secs(3));
        });
        let mut c2 = cfg(tmp.path(), port_body);
        c2.chunk_timeout = Duration::from_millis(150);
        let start2 = std::time::Instant::now();
        let err2 = download_model(&c2, &info(None), &bundle(), Arc::new(AtomicBool::new(false)), &|_| {}).await.unwrap_err();
        assert!(err2.contains("download stalled"), "actual err: {err2}");
        assert!(start2.elapsed() < Duration::from_secs(2), "elapsed: {:?}", start2.elapsed());
    }

    #[tokio::test]
    async fn exact_variant_selection_revalidates_and_never_picks_ambiguous() {
        let r = rev();
        let json = format!(
            r#"{{"sha":"{r}","siblings":[
                {{"rfilename":"model-q4.gguf","size":100}},
                {{"rfilename":"model-q8.gguf","size":200}}
            ]}}"#
        );
        let q4_data = vec![42u8; 100];
        let q4_path = format!("/org/model/resolve/{r}/model-q4.gguf");
        let port = spawn_server(move |p, _| {
            if p.starts_with("/api/models/org/model") {
                http("200 OK", &[], json.as_bytes())
            } else if p == q4_path {
                http("200 OK", &[], &q4_data)
            } else {
                http("404 Not Found", &[], b"")
            }
        });
        let tmp = tempfile::tempdir().unwrap();
        let c = cfg(tmp.path(), port);
        let state = ModelDownloadsState::default();

        // Ambiguous variant without explicit files fails safely
        let req_ambiguous = ModelDownloadRequest {
            kind: ModelKind::Llm,
            repo_id: "org/model".into(),
            revision: None,
            descriptor_id: "llm-gguf".into(),
            files: vec![],
            request_id: None,
        };
        let err = start_download_task(&c, &state, req_ambiguous, |_| {}).await.unwrap_err();
        assert!(err.contains("multiple variants found"), "{err}");

        // Requesting files that do not match available variants fails
        let req_invalid = ModelDownloadRequest {
            kind: ModelKind::Llm,
            repo_id: "org/model".into(),
            revision: None,
            descriptor_id: "llm-gguf".into(),
            files: vec!["model-other.gguf".into()],
            request_id: None,
        };
        let err2 = start_download_task(&c, &state, req_invalid, |_| {}).await.unwrap_err();
        assert!(err2.contains("selected files do not match"), "{err2}");

        // Requesting exact selected files downloads the chosen variant
        let req_exact = ModelDownloadRequest {
            kind: ModelKind::Llm,
            repo_id: "org/model".into(),
            revision: None,
            descriptor_id: "llm-gguf".into(),
            files: vec!["model-q4.gguf".into()],
            request_id: None,
        };
        let manifest = start_download_task(&c, &state, req_exact, |_| {}).await.unwrap();
        assert_eq!(manifest.files.len(), 1);
        assert_eq!(manifest.files[0].path, "model-q4.gguf");
        assert_eq!(manifest.files[0].size, 100);
    }

    #[tokio::test]
    async fn selected_vision_projector_downloads_only_the_exact_pair() {
        let revision = rev();
        let json = format!(r#"{{"sha":"{revision}","siblings":[{{"rfilename":"base.gguf","size":4}},{{"rfilename":"mmproj-a.gguf","size":7}},{{"rfilename":"mmproj-b.gguf","size":7}}]}}"#);
        let port = spawn_server(move |path, _| {
            if path.starts_with("/api/models/org/model") { http("200 OK", &[], json.as_bytes()) }
            else if path.ends_with("/base.gguf") { http("200 OK", &[], b"base") }
            else if path.ends_with("/mmproj-b.gguf") { http("200 OK", &[], b"chosenB") }
            else { http("404 Not Found", &[], b"unselected projector") }
        });
        let tmp = tempfile::tempdir().unwrap();
        let config = cfg(tmp.path(), port);
        let request = ModelDownloadRequest {
            kind: ModelKind::Vision, repo_id: "org/model".into(), revision: Some(rev()),
            descriptor_id: "vision-gguf-mmproj".into(), files: vec!["base.gguf".into(),"mmproj-b.gguf".into()], request_id: None,
        };
        let result = start_download_task(&config, &ModelDownloadsState::default(), request, |_| {}).await.unwrap();
        let paths: Vec<_> = result.files.iter().map(|file|file.path.as_str()).collect();
        assert_eq!(paths, vec!["base.gguf","mmproj-b.gguf"]);
        assert_eq!(result.files[1].sha256, sha(b"chosenB"));
        let other = ModelDownloadRequest {
            kind: ModelKind::Vision, repo_id: "org/model".into(), revision: Some(rev()),
            descriptor_id: "vision-gguf-mmproj".into(), files: vec!["base.gguf".into(),"mmproj-a.gguf".into()], request_id: None,
        };
        let error = start_download_task(&config, &ModelDownloadsState::default(), other, |_| {}).await.unwrap_err();
        assert!(error.contains("different model variant is already installed"), "{error}");
        let installed = read_manifest(&model_dir_path(&config, ModelKind::Vision, "org/model", &rev())).unwrap();
        assert_eq!(installed.files[1].path, "mmproj-b.gguf");
    }

    #[tokio::test]
    async fn early_metadata_cancellation_including_stalled_body() {
        let r = rev();
        let port = spawn_raw_server(move |p, _, stream| {
            if p.starts_with("/api/models/org/model-stalled") {
                let h = "HTTP/1.1 200 OK\r\nContent-Length: 500\r\nConnection: close\r\n\r\n";
                let _ = stream.write_all(h.as_bytes());
                let _ = stream.flush();
                std::thread::sleep(Duration::from_secs(4));
            } else {
                let body = format!(r#"{{"sha":"{r}","siblings":[{{"rfilename":"m.gguf","size":10}}]}}"#);
                let res = http("200 OK", &[], body.as_bytes());
                let _ = stream.write_all(&res);
            }
        });
        let tmp = tempfile::tempdir().unwrap();
        let c = cfg(tmp.path(), port);
        let state = ModelDownloadsState::default();

        // Early cancellation before task even starts
        assert!(state.cancel("req-pre-cancel"));
        let req1 = ModelDownloadRequest {
            kind: ModelKind::Llm,
            repo_id: "org/model".into(),
            revision: None,
            descriptor_id: "llm-gguf".into(),
            files: vec![],
            request_id: Some("req-pre-cancel".into()),
        };
        let err1 = start_download_task(&c, &state, req1, |_| {}).await.unwrap_err();
        assert_eq!(err1, CANCELLED);

        // Cancellation during stalled metadata body
        let state_clone = state.clone();
        tokio::spawn(async move {
            tokio::time::sleep(Duration::from_millis(50)).await;
            state_clone.cancel("req-stalled-body");
        });
        let req2 = ModelDownloadRequest {
            kind: ModelKind::Llm,
            repo_id: "org/model-stalled".into(),
            revision: None,
            descriptor_id: "llm-gguf".into(),
            files: vec![],
            request_id: Some("req-stalled-body".into()),
        };
        let start = std::time::Instant::now();
        let err2 = start_download_task(&c, &state, req2, |_| {}).await.unwrap_err();
        assert_eq!(err2, CANCELLED);
        assert!(start.elapsed() < Duration::from_secs(2), "elapsed: {:?}", start.elapsed());
    }

    #[tokio::test]
    async fn raii_drop_cleans_up_registry_and_duplicate_target_fails_busy() {
        let r = rev();
        let json = format!(r#"{{"sha":"{r}","siblings":[{{"rfilename":"m.gguf","size":5000}}]}}"#);
        let file_req = format!("/org/model/resolve/{r}/m.gguf");
        let port = spawn_raw_server(move |p, _, stream| {
            if p.starts_with("/api/models/org/model") {
                let res = http("200 OK", &[], json.as_bytes());
                let _ = stream.write_all(&res);
            } else if p == file_req {
                let h = "HTTP/1.1 200 OK\r\nContent-Length: 5000\r\nConnection: close\r\n\r\n";
                let _ = stream.write_all(h.as_bytes());
                let _ = stream.flush();
                std::thread::sleep(Duration::from_secs(5));
            }
        });
        let tmp = tempfile::tempdir().unwrap();
        let c = cfg(tmp.path(), port);
        let state = ModelDownloadsState::default();

        let req1 = ModelDownloadRequest {
            kind: ModelKind::Llm,
            repo_id: "org/model".into(),
            revision: None,
            descriptor_id: "llm-gguf".into(),
            files: vec!["m.gguf".into()],
            request_id: Some("req-1".into()),
        };
        let c1 = c.clone();
        let state1 = state.clone();
        let handle = tokio::spawn(async move {
            start_download_task(&c1, &state1, req1, |_| {}).await
        });

        // Give task 1 time to fetch metadata and register active target
        tokio::time::sleep(Duration::from_millis(100)).await;

        // Duplicate target request while task 1 is active fails safely
        let req2 = ModelDownloadRequest {
            kind: ModelKind::Llm,
            repo_id: "org/model".into(),
            revision: None,
            descriptor_id: "llm-gguf".into(),
            files: vec!["m.gguf".into()],
            request_id: Some("req-2".into()),
        };
        let err = start_download_task(&c, &state, req2, |_| {}).await.unwrap_err();
        assert!(err.contains("download already in progress"), "{err}");

        // Aborting/dropping the active task triggers RAII cleanup
        handle.abort();
        let _ = handle.await;

        assert!(state.active_downloads.lock().unwrap().is_empty());
        assert!(state.cancel_handles.lock().unwrap().is_empty());
    }
}

