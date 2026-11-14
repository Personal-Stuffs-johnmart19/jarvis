//! Owned MCP sessions. Network/protocol work uses the official rmcp client.
use super::config::{self, McpConfig, McpPolicyDecision, McpServerConfig, McpServerTransport};
use rmcp::{
    model::{CallToolRequestParams, PaginatedRequestParams},
    service::RunningService,
    transport::{
        streamable_http_client::StreamableHttpClientTransportConfig, StreamableHttpClientTransport,
        TokioChildProcess,
    },
    RoleClient, ServiceExt,
};
use serde::Serialize;
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::{HashMap, HashSet},
    path::PathBuf,
    process::Stdio,
    sync::{
        atomic::{AtomicBool, AtomicUsize, Ordering},
        Arc, Mutex,
    },
    time::Duration,
};
use tokio::sync::Notify;

const CONNECT_TIMEOUT: Duration = Duration::from_secs(20);
const CALL_TIMEOUT: Duration = Duration::from_secs(60);
const HEALTH_INTERVAL: Duration = Duration::from_secs(15);
const HEALTH_TIMEOUT: Duration = Duration::from_secs(5);
const SESSION_STOPPED: usize = 1usize << (usize::BITS - 1);
const MAX_RESULT: usize = 4 * 1024 * 1024;
const MAX_SEALED_REQUESTS: usize = 4096;

#[derive(Default)]
struct Cancellation {
    stopped: AtomicBool,
    notify: Notify,
}
impl Cancellation {
    fn stop(&self) {
        self.stopped.store(true, Ordering::SeqCst);
        self.notify.notify_one();
    }
    async fn wait(&self) {
        if !self.stopped.load(Ordering::SeqCst) {
            self.notify.notified().await;
        }
    }
}

struct OwnedTree {
    cleanup: Mutex<TreeCleanup>,
    #[cfg(windows)]
    _job: crate::modules::proc::job::ProcessJob,
    #[cfg(unix)]
    pid: u32,
}
#[derive(Default)]
struct TreeCleanup {
    stop_sent: bool,
    settled: bool,
}
impl TreeCleanup {
    fn stop(&mut self, signal: impl FnOnce()) {
        if !self.stop_sent && !self.settled {
            self.stop_sent = true;
            signal();
        }
    }
    fn settled(&mut self, probe: impl FnOnce() -> bool) -> bool {
        if !self.settled {
            self.settled = probe();
        }
        self.settled
    }
}
impl OwnedTree {
    fn stop(&self) {
        // A retained numeric process group may eventually be reused. Share the
        // one-shot signal with Drop; never signal again during later probes.
        self.cleanup.lock().unwrap().stop(|| {
            #[cfg(windows)]
            let _ = self._job.terminate();
            #[cfg(unix)]
            let _ = nix::sys::signal::killpg(
                nix::unistd::Pid::from_raw(self.pid as i32),
                nix::sys::signal::Signal::SIGKILL,
            );
        });
    }
    fn settled(&self) -> bool {
        self.cleanup.lock().unwrap().settled(|| {
            #[cfg(windows)]
            {
                self._job.is_empty().unwrap_or(false)
            }
            #[cfg(unix)]
            {
                matches!(
                    nix::sys::signal::killpg(nix::unistd::Pid::from_raw(self.pid as i32), None),
                    Err(nix::errno::Errno::ESRCH)
                )
            }
        })
    }
    fn for_pid(pid: u32) -> Result<Self, String> {
        #[cfg(windows)]
        {
            Ok(Self {
                cleanup: Mutex::default(),
                _job: crate::modules::proc::job::ProcessJob::create_for(pid)
                    .map_err(|_| "Cannot own MCP process tree")?,
            })
        }
        #[cfg(unix)]
        {
            Ok(Self {
                pid,
                cleanup: Mutex::default(),
            })
        }
    }
}
#[cfg(unix)]
impl Drop for OwnedTree {
    fn drop(&mut self) {
        self.stop();
    }
}

#[derive(Clone, Debug, Serialize)]
pub struct ToolInfo {
    pub server_id: String,
    pub name: String,
    pub description: String,
    pub input_schema: Value,
    pub policy: String,
    pub config_revision: String,
    pub connection_generation: String,
}
#[derive(Clone, Debug, Serialize)]
pub struct ServerStatus {
    pub id: String,
    pub enabled: bool,
    pub connected: bool,
    pub connecting: bool,
    pub tool_count: usize,
}
struct Session {
    service: Mutex<Option<RunningService<RoleClient, ()>>>,
    peer: rmcp::Peer<RoleClient>,
    transport_settled: Arc<AtomicBool>,
    active_calls: AtomicUsize,
    tree: Mutex<Option<OwnedTree>>,
    tools: Vec<ToolInfo>,
    revision: String,
    generation: String,
}
impl Session {
    fn stop(&self) {
        self.active_calls
            .fetch_or(SESSION_STOPPED, Ordering::SeqCst);
        self.close_transport();
    }
    fn close_transport(&self) {
        if let Some(tree) = self.tree.lock().unwrap().as_ref() {
            tree.stop();
        }
        if let Some(service) = self.service.lock().unwrap().take() {
            let settled = self.transport_settled.clone();
            // Cancellation flags and a closed sender precede transport.close().
            // Only joining the service can attest to actual transport cleanup.
            if let Ok(runtime) = tokio::runtime::Handle::try_current() {
                runtime.spawn(async move {
                    if service.cancel().await.is_ok() {
                        settled.store(true, Ordering::SeqCst);
                    }
                });
            }
        }
    }
    fn cleanup_settled(&self) -> bool {
        match self.tree.lock().unwrap().as_ref() {
            Some(tree) => tree.settled() && self.transport_settled.load(Ordering::SeqCst),
            None => self.transport_settled.load(Ordering::SeqCst),
        }
    }
    fn try_begin_call(&self) -> Result<(), String> {
        self.active_calls
            .fetch_update(Ordering::SeqCst, Ordering::SeqCst, |state| {
                (state & SESSION_STOPPED == 0).then_some(state + 1)
            })
            .map(|_| ())
            .map_err(|_| "MCP server is not connected".into())
    }
    fn stop_if_idle(&self) -> bool {
        // Claim shutdown and exclude new calls in the same atomic operation.
        // A call registered since the health probe began wins over that probe.
        if self
            .active_calls
            .compare_exchange(0, SESSION_STOPPED, Ordering::SeqCst, Ordering::SeqCst)
            .is_err()
        {
            return false;
        }
        self.close_transport();
        true
    }
    fn alive(&self) -> bool {
        self.active_calls.load(Ordering::SeqCst) & SESSION_STOPPED == 0
            && !self.peer.is_transport_closed()
    }
    fn monitor(self: &Arc<Self>, interval: Duration, timeout: Duration) {
        let session = Arc::downgrade(self);
        tokio::spawn(async move {
            loop {
                tokio::time::sleep(interval).await;
                let Some(session) = session.upgrade() else {
                    break;
                };
                if !session.alive() {
                    session.stop();
                    break;
                }
                // Serial servers can legitimately be busy until CALL_TIMEOUT.
                // A health request must not shorten an approved tool's budget.
                if session.active_calls.load(Ordering::SeqCst) != 0 {
                    continue;
                }
                let healthy = matches!(
                    // tools/list is read-only and works across the MCP
                    // versions supported here (newer versions omit ping).
                    tokio::time::timeout(timeout, session.peer.list_tools(None)).await,
                    Ok(Ok(_))
                );
                if !healthy && session.stop_if_idle() {
                    // A wedged server must not retain owned descendants or stay
                    // discoverable forever. Reconnect remains explicit; never
                    // replay a tool call after an uncertain outcome.
                    break;
                }
            }
        });
    }
}
impl Drop for Session {
    fn drop(&mut self) {
        self.stop();
    }
}
enum Slot {
    Connecting(Arc<Cancellation>),
    Ready(Arc<Session>),
}
impl Slot {
    fn stop(&self) {
        match self {
            Self::Connecting(c) => c.stop(),
            Self::Ready(s) => s.stop(),
        }
    }
}

pub struct McpManager {
    path: PathBuf,
    connect_timeout: Duration,
    call_timeout: Duration,
    health_interval: Duration,
    health_timeout: Duration,
    slots: Mutex<HashMap<String, Slot>>,
    calls: Mutex<HashMap<String, CallState>>,
    early_cancels: Mutex<HashSet<String>>,
    sealed_calls: Mutex<HashMap<String, String>>,
    config_lock: Mutex<()>,
}
struct CallState {
    cancellation: Arc<Cancellation>,
    nonce: Option<String>,
    cleanup: Option<Arc<Session>>,
}
#[derive(Debug, Serialize)]
pub struct CallSettlement {
    pub settled: bool,
}
impl Default for McpManager {
    fn default() -> Self {
        let root = std::env::var_os("JARVIS_ROOT")
            .map(PathBuf::from)
            .unwrap_or_else(|| std::env::current_dir().expect("application directory"));
        let data = if std::env::var("JARVIS_DESKTOP_TEST").as_deref() == Ok("1") {
            ".cache/desktop-test"
        } else {
            "data"
        };
        Self::new(root.join(data).join("mcp-servers.json"))
    }
}
fn revision(server: &McpServerConfig) -> String {
    fn canonical(value: Value) -> Value {
        match value {
            Value::Object(map) => {
                let mut entries: Vec<_> = map.into_iter().collect();
                entries.sort_by(|a, b| a.0.cmp(&b.0));
                Value::Object(
                    entries
                        .into_iter()
                        .map(|(key, value)| (key, canonical(value)))
                        .collect(),
                )
            }
            Value::Array(items) => Value::Array(items.into_iter().map(canonical).collect()),
            other => other,
        }
    }
    let value = canonical(serde_json::to_value(server).expect("serializable configuration"));
    hex::encode(Sha256::digest(serde_json::to_vec(&value).unwrap()))
}
fn policy_name(policy: McpPolicyDecision) -> &'static str {
    match policy {
        McpPolicyDecision::Allow => "allow",
        McpPolicyDecision::Ask => "ask",
        McpPolicyDecision::Deny => "deny",
    }
}
fn valid_request_id(id: &str) -> bool {
    !id.is_empty()
        && id.len() <= 128
        && id
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
}
fn valid_nonce(nonce: &str) -> bool {
    nonce.len() == 32
        && nonce
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

struct ConnectLease<'a> {
    manager: &'a McpManager,
    id: String,
    cancel: Arc<Cancellation>,
}
impl Drop for ConnectLease<'_> {
    fn drop(&mut self) {
        let mut slots = self.manager.slots.lock().unwrap();
        if matches!(slots.get(&self.id), Some(Slot::Connecting(c)) if Arc::ptr_eq(c, &self.cancel))
        {
            slots.remove(&self.id);
        }
    }
}
struct CallLease<'a> {
    manager: &'a McpManager,
    id: String,
    session: Arc<Session>,
    finished: bool,
}
impl Drop for CallLease<'_> {
    fn drop(&mut self) {
        if !self.finished {
            self.session.stop();
        }
        let mut calls = self.manager.calls.lock().unwrap();
        if self.finished || self.session.cleanup_settled() {
            calls.remove(&self.id);
            self.session.active_calls.fetch_sub(1, Ordering::SeqCst);
        } else if let Some(call) = calls.get_mut(&self.id) {
            call.cleanup = Some(self.session.clone());
        }
    }
}

impl McpManager {
    pub fn new(path: PathBuf) -> Self {
        Self {
            path,
            connect_timeout: CONNECT_TIMEOUT,
            call_timeout: CALL_TIMEOUT,
            health_interval: HEALTH_INTERVAL,
            health_timeout: HEALTH_TIMEOUT,
            slots: Mutex::default(),
            calls: Mutex::default(),
            early_cancels: Mutex::default(),
            sealed_calls: Mutex::default(),
            config_lock: Mutex::default(),
        }
    }
    pub fn config(&self) -> Result<McpConfig, String> {
        let _guard = self.config_lock.lock().unwrap();
        config::load_config_from_file(&self.path)
    }
    pub fn save(&self, config: McpConfig) -> Result<(), String> {
        let _guard = self.config_lock.lock().unwrap();
        config::save_config_atomic(&self.path, &config)?;
        let mut slots = self.slots.lock().unwrap();
        slots.retain(|id, slot| {
            let keep = match slot {
                Slot::Ready(session) => config
                    .servers
                    .iter()
                    .any(|s| s.id == *id && s.enabled && revision(s) == session.revision),
                Slot::Connecting(_) => false,
            };
            if !keep {
                slot.stop();
            }
            keep
        });
        Ok(())
    }
    pub fn server(&self, id: &str) -> Result<McpServerConfig, String> {
        self.config()?
            .servers
            .into_iter()
            .find(|s| s.id == id && s.enabled)
            .ok_or_else(|| "MCP server is missing or disabled".into())
    }
    pub fn disconnect(&self, id: &str) {
        if let Some(slot) = self.slots.lock().unwrap().remove(id) {
            slot.stop();
        }
    }
    pub fn shutdown(&self) {
        for (_, slot) in self.slots.lock().unwrap().drain() {
            slot.stop();
        }
        for cancellation in self.calls.lock().unwrap().values() {
            cancellation.cancellation.stop();
        }
    }
    pub fn statuses(&self) -> Result<Vec<ServerStatus>, String> {
        let config = self.config()?;
        let slots = self.slots.lock().unwrap();
        Ok(config
            .servers
            .iter()
            .map(|server| {
                let (connected, connecting, tool_count) = match slots.get(&server.id) {
                    Some(Slot::Ready(s)) if s.alive() => (true, false, s.tools.len()),
                    Some(Slot::Connecting(_)) => (false, true, 0),
                    _ => (false, false, 0),
                };
                ServerStatus {
                    id: server.id.clone(),
                    enabled: server.enabled,
                    connected,
                    connecting,
                    tool_count,
                }
            })
            .collect())
    }
    pub fn tools(&self, server_id: Option<&str>) -> Result<Vec<ToolInfo>, String> {
        let config = self.config()?;
        let slots = self.slots.lock().unwrap();
        let mut result = vec![];
        for (id, slot) in slots.iter() {
            if server_id.is_some_and(|wanted| wanted != id) {
                continue;
            }
            if let Slot::Ready(session) = slot {
                if !session.alive() {
                    continue;
                }
                for tool in &session.tools {
                    let mut tool = tool.clone();
                    tool.policy = policy_name(config.resolve_policy(id, &tool.name)).into();
                    result.push(tool);
                }
            }
        }
        result.sort_by(|a, b| (&a.server_id, &a.name).cmp(&(&b.server_id, &b.name)));
        Ok(result)
    }
    pub fn cancel_window(&self, label: &str) {
        let prefix = format!("{}-", hex::encode(Sha256::digest(label.as_bytes())));
        for (id, cancellation) in self.calls.lock().unwrap().iter() {
            if id.starts_with(&prefix) {
                cancellation.cancellation.stop();
            }
        }
    }
    pub fn cancel(&self, request_id: &str) -> Result<(), String> {
        if !valid_request_id(request_id) {
            return Err("Invalid MCP request ID".into());
        }
        // Registration takes these locks in this order too. Keep the calls lock
        // until the early cancellation is recorded so registration cannot slip
        // between the lookup and insertion and miss Stop.
        let mut calls = self.calls.lock().unwrap();
        Self::reap_calls(&mut calls);
        if let Some(call) = calls.get(request_id) {
            call.cancellation.stop();
        }
        if calls.contains_key(request_id) {
            return Ok(());
        }
        let mut early = self.early_cancels.lock().unwrap();
        if early.len() >= 256 {
            early.clear();
        }
        early.insert(request_id.into());
        Ok(())
    }
    fn reap_calls(calls: &mut HashMap<String, CallState>) {
        calls.retain(|_, call| {
            let Some(session) = &call.cleanup else {
                return true;
            };
            if !session.cleanup_settled() {
                return true;
            }
            session.active_calls.fetch_sub(1, Ordering::SeqCst);
            false
        });
    }
    /// Seal admission before reporting absence; the private nonce binds the
    /// caller's cleanup proof to exactly one physical operation.
    pub fn cancel_and_settle(
        &self,
        request_id: &str,
        nonce: &str,
    ) -> Result<CallSettlement, String> {
        if !valid_request_id(request_id) || !valid_nonce(nonce) {
            return Err("Invalid MCP cleanup identity".into());
        }
        let mut calls = self.calls.lock().unwrap();
        let mut sealed = self.sealed_calls.lock().unwrap();
        if calls
            .get(request_id)
            .is_some_and(|call| call.nonce.as_deref() != Some(nonce))
            || sealed.get(request_id).is_some_and(|value| value != nonce)
        {
            return Err("MCP cleanup identity does not match".into());
        }
        if let Some(call) = calls.get(request_id) {
            call.cancellation.stop();
        }
        if !sealed.contains_key(request_id) && sealed.len() >= MAX_SEALED_REQUESTS {
            return Err(
                "MCP cleanup seal capacity reached; restart Jarvis before new queued calls".into(),
            );
        }
        sealed.insert(request_id.to_owned(), nonce.to_owned());
        Self::reap_calls(&mut calls);
        Ok(CallSettlement {
            settled: !calls.contains_key(request_id),
        })
    }

    pub async fn connect(
        &self,
        server: McpServerConfig,
        secrets: HashMap<String, String>,
    ) -> Result<Vec<ToolInfo>, String> {
        server.validate()?;
        if !server.enabled || self.server(&server.id)? != server {
            return Err("MCP configuration changed; connect again".into());
        }
        let expected_revision = revision(&server);
        let mut generation = [0u8; 16];
        getrandom::fill(&mut generation).map_err(|_| "Could not identify MCP connection")?;
        let generation = hex::encode(generation);
        let cancellation = Arc::new(Cancellation::default());
        {
            let mut slots = self.slots.lock().unwrap();
            match slots.get(&server.id) {
                Some(Slot::Connecting(_)) => {
                    return Err("MCP server connection already in progress".into())
                }
                Some(Slot::Ready(s)) if s.alive() && s.revision == expected_revision => {
                    return Ok(s.tools.clone())
                }
                _ => {}
            }
            if let Some(old) =
                slots.insert(server.id.clone(), Slot::Connecting(cancellation.clone()))
            {
                old.stop();
            }
        }
        let _lease = ConnectLease {
            manager: self,
            id: server.id.clone(),
            cancel: cancellation.clone(),
        };
        let connection = async {
            let (service, tree) = match &server.transport {
                McpServerTransport::Stdio(config) => {
                    let executable = which::which(&config.command)
                        .map_err(|_| "MCP executable not found on PATH")?;
                    let mut command = tokio::process::Command::new(executable);
                    command.args(&config.args).kill_on_drop(true);
                    if let Some(cwd) = &config.cwd {
                        command.current_dir(cwd);
                    }
                    command.env_clear();
                    for key in [
                        "PATH",
                        "PATHEXT",
                        "SystemRoot",
                        "WINDIR",
                        "COMSPEC",
                        "TEMP",
                        "TMP",
                        "USERPROFILE",
                        "HOME",
                        "APPDATA",
                        "LOCALAPPDATA",
                        "PROGRAMDATA",
                        "PROGRAMFILES",
                        "PROGRAMFILES(X86)",
                        "LANG",
                    ] {
                        if let Some(value) = std::env::var_os(key) {
                            command.env(key, value);
                        }
                    }
                    for (key, value) in &secrets {
                        command.env(key, value);
                    }
                    #[cfg(windows)]
                    command.creation_flags(0x08000000);
                    let mut command = process_wrap::tokio::CommandWrap::from(command);
                    #[cfg(windows)]
                    command.wrap(process_wrap::tokio::JobObject);
                    #[cfg(unix)]
                    command.wrap(process_wrap::tokio::ProcessGroup::leader());
                    let (transport, _) = TokioChildProcess::builder(command)
                        .stderr(Stdio::null())
                        .spawn()
                        .map_err(|_| "Cannot start MCP process")?;
                    let tree = OwnedTree::for_pid(transport.id().ok_or("MCP process has no PID")?)?;
                    let service = ().serve(transport).await.map_err(|_| {
                        "MCP handshake failed; check command, arguments and working directory"
                    })?;
                    (service, Some(tree))
                }
                McpServerTransport::Http(config) => {
                    let _ = rustls::crypto::ring::default_provider().install_default();
                    let client = reqwest::Client::builder()
                        .redirect(reqwest::redirect::Policy::none())
                        .timeout(self.call_timeout)
                        .build()
                        .map_err(|_| "Cannot create MCP HTTP client")?;
                    let mut transport_config =
                        StreamableHttpClientTransportConfig::with_uri(config.url.clone());
                    transport_config.max_concurrent_requests = 4;
                    transport_config.allow_stateless = true;
                    transport_config.max_sse_event_size = MAX_RESULT;
                    // Never replay a tool mutation after a lost session.
                    transport_config.reinit_on_expired_session = false;
                    for (name, value) in &secrets {
                        let name = reqwest::header::HeaderName::from_bytes(name.as_bytes())
                            .map_err(|_| "Invalid MCP secret header name")?;
                        let mut value = reqwest::header::HeaderValue::from_str(value)
                            .map_err(|_| "Invalid MCP secret header value")?;
                        value.set_sensitive(true);
                        transport_config.custom_headers.insert(name, value);
                    }
                    let transport =
                        StreamableHttpClientTransport::with_client(client, transport_config);
                    (
                        ().serve(transport).await.map_err(|_| {
                            "MCP HTTP handshake failed; check endpoint and saved credentials"
                        })?,
                        None,
                    )
                }
            };
            let mut tools = Vec::new();
            let mut tool_names = HashSet::new();
            let mut cursor = None;
            let mut seen_cursors = HashSet::new();
            let config = self.config()?;
            for _ in 0..32 {
                let params = cursor
                    .take()
                    .map(|cursor| PaginatedRequestParams::default().with_cursor(Some(cursor)));
                let page = service
                    .peer()
                    .list_tools(params)
                    .await
                    .map_err(|_| "MCP tool discovery failed")?;
                for tool in page.tools {
                    if tool.name.is_empty()
                        || tool.name.len() > 128
                        || !tool
                            .name
                            .bytes()
                            .all(|b| b.is_ascii_alphanumeric() || b"_.-".contains(&b))
                    {
                        return Err("MCP returned an invalid tool name".into());
                    }
                    if !tool_names.insert(tool.name.to_string()) {
                        return Err("MCP server returned duplicate tool names".into());
                    }
                    let schema = serde_json::to_value(&tool.input_schema)
                        .map_err(|_| "Invalid MCP tool schema")?;
                    if schema.get("type").and_then(Value::as_str) != Some("object")
                        || serde_json::to_vec(&schema).unwrap().len() > 65536
                    {
                        return Err("MCP returned an unsupported or oversized tool schema".into());
                    }
                    tools.push(ToolInfo {
                        server_id: server.id.clone(),
                        name: tool.name.to_string(),
                        description: tool
                            .description
                            .map(|v| v.chars().take(8192).collect())
                            .unwrap_or_default(),
                        input_schema: schema,
                        policy: policy_name(config.resolve_policy(&server.id, tool.name.as_ref()))
                            .into(),
                        config_revision: expected_revision.clone(),
                        connection_generation: generation.clone(),
                    });
                    if tools.len() > 256 {
                        return Err("MCP server exceeds the 256-tool discovery limit".into());
                    }
                }
                match page.next_cursor {
                    None => {
                        return Ok(Session {
                            peer: service.peer().clone(),
                            service: Mutex::new(Some(service)),
                            transport_settled: Arc::new(AtomicBool::new(false)),
                            active_calls: AtomicUsize::new(0),
                            tree: Mutex::new(tree),
                            tools,
                            revision: expected_revision.clone(),
                            generation: generation.clone(),
                        })
                    }
                    Some(next) => {
                        if !seen_cursors.insert(next.clone()) {
                            return Err("MCP server repeated its tool cursor".into());
                        }
                        cursor = Some(next);
                    }
                }
            }
            Err::<Session, String>("MCP tool discovery exceeded the page limit".into())
        };
        let session = tokio::select! {
            biased;
            _ = cancellation.wait() => return Err("MCP connection cancelled".into()),
            result = tokio::time::timeout(self.connect_timeout, connection) => result.map_err(|_| "MCP connection timed out")??,
        };
        if self.server(&server.id)? != server {
            return Err("MCP configuration changed during connection".into());
        }
        let tools = session.tools.clone();
        let mut slots = self.slots.lock().unwrap();
        if cancellation.stopped.load(Ordering::SeqCst)
            || !matches!(slots.get(&server.id), Some(Slot::Connecting(c)) if Arc::ptr_eq(c, &cancellation))
        {
            return Err("MCP connection cancelled".into());
        }
        let session = Arc::new(session);
        session.monitor(self.health_interval, self.health_timeout);
        slots.insert(server.id, Slot::Ready(session));
        Ok(tools)
    }

    pub async fn call(
        &self,
        server_id: &str,
        name: &str,
        args: Map<String, Value>,
        request_id: String,
        config_revision: &str,
        connection_generation: &str,
        approved: bool,
    ) -> Result<Value, String> {
        self.call_with_nonce(
            server_id,
            name,
            args,
            request_id,
            config_revision,
            connection_generation,
            approved,
            None,
        )
        .await
    }
    pub async fn call_with_nonce(
        &self,
        server_id: &str,
        name: &str,
        args: Map<String, Value>,
        request_id: String,
        config_revision: &str,
        connection_generation: &str,
        approved: bool,
        cleanup_nonce: Option<String>,
    ) -> Result<Value, String> {
        if !valid_request_id(&request_id)
            || cleanup_nonce
                .as_deref()
                .is_some_and(|nonce| !valid_nonce(nonce))
            || serde_json::to_vec(&args)
                .map_err(|_| "Invalid MCP arguments")?
                .len()
                > 1024 * 1024
        {
            return Err("Invalid or oversized MCP request".into());
        }
        let config = self.config()?;
        // Resolve policy and its revision from one snapshot. Separate reads can
        // combine an old allow policy with a newly saved server revision.
        let server = config
            .servers
            .iter()
            .find(|server| server.id == server_id && server.enabled)
            .ok_or("MCP server is missing or disabled")?;
        if revision(&server) != config_revision {
            return Err("MCP settings changed; request approval again".into());
        }
        match config.resolve_policy(server_id, name) {
            McpPolicyDecision::Deny => return Err("MCP tool denied by policy".into()),
            McpPolicyDecision::Ask if !approved => return Err("MCP tool requires approval".into()),
            _ => {}
        }
        let session = match self.slots.lock().unwrap().get(server_id) {
            Some(Slot::Ready(s))
                if s.alive()
                    && s.revision == config_revision
                    && s.generation == connection_generation =>
            {
                s.clone()
            }
            _ => return Err("MCP server is not connected".into()),
        };
        if !session.tools.iter().any(|t| t.name == name) {
            return Err("MCP tool was not discovered".into());
        }
        let cancellation = Arc::new(Cancellation::default());
        {
            let mut calls = self.calls.lock().unwrap();
            Self::reap_calls(&mut calls);
            if calls.len() >= 64 || calls.contains_key(&request_id) {
                return Err("MCP request busy or duplicate ID".into());
            }
            if self.sealed_calls.lock().unwrap().contains_key(&request_id) {
                return Err("MCP call cancelled and sealed; verify outcome before retrying".into());
            }
            if self.early_cancels.lock().unwrap().remove(&request_id) {
                cancellation.stop();
            }
            session.try_begin_call()?;
            calls.insert(
                request_id.clone(),
                CallState {
                    cancellation: cancellation.clone(),
                    nonce: cleanup_nonce,
                    cleanup: None,
                },
            );
        }
        let mut lease = CallLease {
            manager: self,
            id: request_id,
            session: session.clone(),
            finished: false,
        };
        let request = session
            .peer
            .call_tool(CallToolRequestParams::new(name.to_owned()).with_arguments(args));
        let response = tokio::select! {
            biased;
            _ = cancellation.wait() => Err("MCP call cancelled; verify outcome before retrying".to_string()),
            result = tokio::time::timeout(self.call_timeout, request) => match result { Ok(value) => value.map_err(|_| "MCP call failed; verify outcome before retrying".to_string()), Err(_) => Err("MCP call timed out; verify outcome before retrying".to_string()) },
        };
        let result = response.and_then(|response| {
            let response = serde_json::to_value(response)
                .map_err(|_| "Invalid MCP tool response".to_string())?;
            if serde_json::to_vec(&response).unwrap().len() > MAX_RESULT {
                return Err("MCP tool response exceeds 4 MiB".into());
            }
            Ok(response)
        });
        if result.is_ok() {
            lease.finished = true;
        } else {
            session.stop();
            // A deadline bounds the IPC wait, never the cleanup reservation.
            // If cleanup is still unproven, Drop retains its state for probing.
            let _ = tokio::time::timeout(Duration::from_secs(5), async {
                while !session.cleanup_settled() {
                    tokio::time::sleep(Duration::from_millis(10)).await;
                }
            })
            .await;
        }
        result
    }
}
impl Drop for McpManager {
    fn drop(&mut self) {
        self.shutdown();
    }
}

#[cfg(test)]
#[path = "tests.rs"]
mod tests;
