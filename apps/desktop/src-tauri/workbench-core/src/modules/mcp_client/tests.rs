use super::super::config::{KeyringSecretRef, McpHttpConfig, McpPolicy, McpStdioConfig};
use super::*;
use std::{path::Path, process::Child};

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .ancestors()
        .nth(4)
        .unwrap()
        .to_path_buf()
}
fn python() -> PathBuf {
    #[cfg(windows)]
    {
        root().join(".cache/qa-tools/Scripts/python.exe")
    }
    #[cfg(not(windows))]
    {
        which::which("python3").expect("Python3 required for offline MCP fixtures")
    }
}
fn server(temp: &Path, extra: &[&str]) -> McpServerConfig {
    let mut args = vec![
        root()
            .join("tests/fixtures/mcp_server.py")
            .to_string_lossy()
            .into_owned(),
        "--pids".into(),
        temp.join("pids.json").to_string_lossy().into_owned(),
        "--calls".into(),
        temp.join("calls.txt").to_string_lossy().into_owned(),
    ];
    args.extend(extra.iter().map(|s| s.to_string()));
    McpServerConfig {
        id: "fixture".into(),
        name: Some("Offline fixture".into()),
        enabled: true,
        default_policy: McpPolicy::Ask,
        tool_policies: HashMap::new(),
        transport: McpServerTransport::Stdio(McpStdioConfig {
            command: python().to_string_lossy().into_owned(),
            args,
            cwd: Some(root()),
            secret_env: HashMap::new(),
        }),
    }
}
fn manager(temp: &Path, server: &McpServerConfig) -> Arc<McpManager> {
    let manager = Arc::new(McpManager::new(temp.join("config.json")));
    manager
        .save(McpConfig {
            version: 1,
            servers: vec![server.clone()],
        })
        .unwrap();
    manager
}
async fn wait_for(mut check: impl FnMut() -> bool) {
    let end = tokio::time::Instant::now() + Duration::from_secs(3);
    while !check() {
        assert!(
            tokio::time::Instant::now() < end,
            "offline fixture wait expired"
        );
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
}
fn pids(temp: &Path) -> Vec<u32> {
    serde_json::from_slice(&std::fs::read(temp.join("pids.json")).unwrap()).unwrap()
}
fn alive(pid: u32) -> bool {
    let pid = sysinfo::Pid::from_u32(pid);
    let mut system = sysinfo::System::new()
        .expect("initialize owned-process liveness monitor");
    system.refresh_processes(sysinfo::ProcessesToUpdate::Some(&[pid]), true);
    system.process(pid).is_some()
}
async fn gone(pids: &[u32]) {
    wait_for(|| pids.iter().all(|pid| !alive(*pid))).await;
}
fn args() -> Map<String, Value> {
    serde_json::json!({"text": "hello"})
        .as_object()
        .unwrap()
        .clone()
}

#[tokio::test]
async fn stdio_discovery_approval_calls_disconnect_and_reconnect() {
    let temp = tempfile::tempdir().unwrap();
    let server = server(temp.path(), &[]);
    let manager = manager(temp.path(), &server);
    let tools = manager
        .connect(server.clone(), HashMap::new())
        .await
        .unwrap();
    assert_eq!(tools.len(), 2);
    assert_eq!(tools[0].policy, "ask");
    let rev = tools[0].config_revision.clone();
    let generation = tools[0].connection_generation.clone();
    assert!(manager
        .call(
            "fixture",
            "echo",
            args(),
            "unapproved".into(),
            &rev,
            &generation,
            false
        )
        .await
        .unwrap_err()
        .contains("approval"));
    assert!(!temp.path().join("calls.txt").exists());
    let result = manager
        .call(
            "fixture",
            "echo",
            args(),
            "approved".into(),
            &rev,
            &generation,
            true,
        )
        .await
        .unwrap();
    assert_eq!(result["content"][0]["text"], "hello");
    let owned = pids(temp.path());
    manager.disconnect("fixture");
    gone(&owned).await;
    assert!(!manager.statuses().unwrap()[0].connected);
    assert_eq!(
        manager.connect(server, HashMap::new()).await.unwrap().len(),
        2
    );
    let owned = pids(temp.path());
    drop(manager);
    gone(&owned).await;
}

#[tokio::test]
async fn health_shutdown_atomically_yields_to_registered_calls_and_blocks_late_calls() {
    let temp = tempfile::tempdir().unwrap();
    let config = server(temp.path(), &[]);
    let manager = manager(temp.path(), &config);
    manager.connect(config, HashMap::new()).await.unwrap();
    let session = match manager.slots.lock().unwrap().get("fixture") {
        Some(Slot::Ready(session)) => session.clone(),
        _ => panic!("fixture must be connected"),
    };
    // The probe observed idle; an approved call registers before its result.
    assert_eq!(session.active_calls.load(Ordering::SeqCst), 0);
    session.try_begin_call().unwrap();
    assert!(!session.stop_if_idle());
    assert!(session.alive());
    session.active_calls.fetch_sub(1, Ordering::SeqCst);
    let owned = pids(temp.path());
    assert!(session.stop_if_idle());
    assert!(session.try_begin_call().is_err());
    assert!(!session.alive());
    assert!(!temp.path().join("calls.txt").exists());
    gone(&owned).await;
}

#[tokio::test]
async fn health_monitor_does_not_interrupt_busy_serial_tool_servers() {
    let temp = tempfile::tempdir().unwrap();
    let config = server(temp.path(), &[]);
    let mut manager = McpManager::new(temp.path().join("config.json"));
    manager.health_interval = Duration::from_millis(30);
    manager.health_timeout = Duration::from_millis(30);
    manager
        .save(McpConfig {
            version: 1,
            servers: vec![config.clone()],
        })
        .unwrap();
    let manager = Arc::new(manager);
    let tools = manager.connect(config, HashMap::new()).await.unwrap();
    let running = manager.clone();
    let rev = tools[0].config_revision.clone();
    let generation = tools[0].connection_generation.clone();
    let task = tokio::spawn(async move {
        running
            .call(
                "fixture",
                "wait",
                Map::new(),
                "busy-health".into(),
                &rev,
                &generation,
                true,
            )
            .await
    });
    wait_for(|| temp.path().join("calls.txt").exists()).await;
    tokio::time::sleep(Duration::from_millis(200)).await;
    assert!(
        !task.is_finished(),
        "health timeout must not shorten a tool timeout"
    );
    assert!(manager.statuses().unwrap()[0].connected);
    let owned = pids(temp.path());
    manager.cancel("busy-health").unwrap();
    assert!(task.await.unwrap().unwrap_err().contains("cancelled"));
    gone(&owned).await;
}

#[tokio::test]
async fn unresponsive_health_probe_revokes_tools_and_cleans_up_owned_tree() {
    let temp = tempfile::tempdir().unwrap();
    let source = std::fs::read_to_string(root().join("tests/fixtures/mcp_server.py")).unwrap();
    let source = source.replace(
        "elif method == 'tools/list':",
        "elif method == 'tools/list':\n        health = Path(options.calls).with_suffix('.health')\n        if health.exists():\n            time.sleep(120)\n        health.touch()",
    );
    let fixture = temp.path().join("unresponsive.py");
    std::fs::write(&fixture, source).unwrap();
    let mut config = server(temp.path(), &[]);
    if let McpServerTransport::Stdio(stdio) = &mut config.transport {
        stdio.args[0] = fixture.to_string_lossy().into_owned();
    }
    let mut manager = McpManager::new(temp.path().join("config.json"));
    manager.health_interval = Duration::from_millis(50);
    manager.health_timeout = Duration::from_millis(100);
    manager
        .save(McpConfig {
            version: 1,
            servers: vec![config.clone()],
        })
        .unwrap();
    manager.connect(config, HashMap::new()).await.unwrap();
    let owned = pids(temp.path());
    wait_for(|| !manager.statuses().unwrap()[0].connected).await;
    assert!(manager.tools(None).unwrap().is_empty());
    assert_eq!(manager.statuses().unwrap()[0].tool_count, 0);
    gone(&owned).await;
    assert!(
        !temp.path().join("calls.txt").exists(),
        "health probes must not execute tools"
    );
    // Recovery requires an explicit connect, and does not replay any mutation.
    let config = server(temp.path(), &[]);
    manager
        .save(McpConfig {
            version: 1,
            servers: vec![config.clone()],
        })
        .unwrap();
    let tools = manager.connect(config, HashMap::new()).await.unwrap();
    assert!(manager
        .call(
            "fixture",
            "echo",
            args(),
            "healthy".into(),
            &tools[0].config_revision,
            &tools[0].connection_generation,
            true
        )
        .await
        .is_ok());
}

#[tokio::test]
async fn stopped_session_is_unavailable_immediately_and_reconnect_starts_new_process() {
    let temp = tempfile::tempdir().unwrap();
    let config = server(temp.path(), &[]);
    let manager = manager(temp.path(), &config);
    let tools = manager
        .connect(config.clone(), HashMap::new())
        .await
        .unwrap();
    let owned = pids(temp.path());
    let session = match manager.slots.lock().unwrap().get("fixture") {
        Some(Slot::Ready(session)) => session.clone(),
        _ => panic!("fixture must be connected"),
    };
    // No await between stop and inspection: the transport task has not had a
    // chance to close, but Stop must already revoke the cached session/tools.
    session.stop();
    assert!(!session.alive());
    let status = &manager.statuses().unwrap()[0];
    assert!(!status.connected);
    assert_eq!(status.tool_count, 0);
    assert!(manager.tools(None).unwrap().is_empty());
    assert!(manager
        .call(
            "fixture",
            "echo",
            args(),
            "stopped".into(),
            &tools[0].config_revision,
            &tools[0].connection_generation,
            true
        )
        .await
        .unwrap_err()
        .contains("not connected"));
    let tools = manager.connect(config, HashMap::new()).await.unwrap();
    assert_ne!(pids(temp.path())[0], owned[0]);
    gone(&owned).await;
    assert!(manager
        .call(
            "fixture",
            "echo",
            args(),
            "restarted".into(),
            &tools[0].config_revision,
            &tools[0].connection_generation,
            true
        )
        .await
        .is_ok());
}

#[tokio::test]
async fn cancellation_before_registration_does_not_dispatch_tool() {
    let temp = tempfile::tempdir().unwrap();
    let config = server(temp.path(), &[]);
    let manager = manager(temp.path(), &config);
    let tools = manager.connect(config, HashMap::new()).await.unwrap();
    let owned = pids(temp.path());
    manager.cancel("early-stop").unwrap();
    assert!(manager
        .call(
            "fixture",
            "echo",
            args(),
            "early-stop".into(),
            &tools[0].config_revision,
            &tools[0].connection_generation,
            true
        )
        .await
        .unwrap_err()
        .contains("cancelled"));
    assert!(!temp.path().join("calls.txt").exists());
    assert!(manager.calls.lock().unwrap().is_empty());
    assert!(manager.early_cancels.lock().unwrap().is_empty());
    gone(&owned).await;
}

#[tokio::test]
async fn cancellation_kills_owned_descendants_and_leaves_manager_reusable() {
    let temp = tempfile::tempdir().unwrap();
    let server = server(temp.path(), &[]);
    let manager = manager(temp.path(), &server);
    let tools = manager
        .connect(server.clone(), HashMap::new())
        .await
        .unwrap();
    let rev = tools[0].config_revision.clone();
    let generation = tools[0].connection_generation.clone();
    let child_manager = manager.clone();
    let call = tokio::spawn(async move {
        child_manager
            .call(
                "fixture",
                "wait",
                Map::new(),
                "cancel-me".into(),
                &rev,
                &generation,
                true,
            )
            .await
    });
    wait_for(|| {
        std::fs::read_to_string(temp.path().join("calls.txt")).is_ok_and(|s| s.contains("wait"))
    })
    .await;
    let owned = pids(temp.path());
    manager.cancel("cancel-me").unwrap();
    assert!(call.await.unwrap().unwrap_err().contains("cancelled"));
    gone(&owned).await;
    assert!(manager.calls.lock().unwrap().is_empty());
    let tools = manager.connect(server, HashMap::new()).await.unwrap();
    assert!(manager
        .call(
            "fixture",
            "echo",
            args(),
            "after-stop".into(),
            &tools[0].config_revision,
            &tools[0].connection_generation,
            true
        )
        .await
        .is_ok());
}

#[tokio::test]
async fn cancelling_handshake_and_rejecting_schema_clean_up_process_trees() {
    let temp = tempfile::tempdir().unwrap();
    let config = server(temp.path(), &["--no-initialize"]);
    let manager = manager(temp.path(), &config);
    let connecting = manager.clone();
    let child_config = config.clone();
    let task = tokio::spawn(async move { connecting.connect(child_config, HashMap::new()).await });
    wait_for(|| {
        std::fs::read(temp.path().join("pids.json"))
            .ok()
            .and_then(|data| serde_json::from_slice::<Vec<u32>>(&data).ok())
            .is_some()
    })
    .await;
    let owned = pids(temp.path());
    manager.disconnect("fixture");
    assert!(task.await.unwrap().unwrap_err().contains("cancelled"));
    gone(&owned).await;
    let mut bad = config;
    if let McpServerTransport::Stdio(s) = &mut bad.transport {
        s.args.pop();
        s.args.push("--bad-schema".into());
    }
    std::fs::remove_file(temp.path().join("pids.json")).unwrap();
    manager
        .save(McpConfig {
            version: 1,
            servers: vec![bad.clone()],
        })
        .unwrap();
    assert!(manager
        .connect(bad, HashMap::new())
        .await
        .unwrap_err()
        .contains("schema"));
    gone(&pids(temp.path())).await;
}

#[test]
fn config_fingerprint_is_independent_of_hashmap_order() {
    let temp = tempfile::tempdir().unwrap();
    let mut a = server(temp.path(), &[]);
    a.tool_policies.insert("first".into(), McpPolicy::Allow);
    a.tool_policies.insert("second".into(), McpPolicy::Deny);
    let mut b = a.clone();
    b.tool_policies.clear();
    b.tool_policies.insert("second".into(), McpPolicy::Deny);
    b.tool_policies.insert("first".into(), McpPolicy::Allow);
    assert_eq!(revision(&a), revision(&b));
}

struct FixtureProcess {
    child: Child,
    tree: Option<OwnedTree>,
}
impl Drop for FixtureProcess {
    fn drop(&mut self) {
        self.tree.take();
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}
#[tokio::test]
async fn streamable_http_auth_and_stateless_tools_use_official_transport() {
    let temp = tempfile::tempdir().unwrap();
    let port_file = temp.path().join("port.txt");
    let stdio = server(temp.path(), &[]);
    let McpServerTransport::Stdio(s) = stdio.transport else {
        unreachable!()
    };
    let mut command = std::process::Command::new(&s.command);
    command
        .args(&s.args)
        .arg("--http-port-file")
        .arg(&port_file)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        command.process_group(0);
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    let child = command.spawn().unwrap();
    let tree = OwnedTree::for_pid(child.id()).unwrap();
    let _fixture = FixtureProcess {
        child,
        tree: Some(tree),
    };
    wait_for(|| {
        std::fs::read_to_string(&port_file)
            .ok()
            .and_then(|port| port.parse::<u16>().ok())
            .is_some()
    })
    .await;
    let port = std::fs::read_to_string(&port_file).unwrap();
    let config = McpServerConfig {
        id: "fixture".into(),
        name: None,
        enabled: true,
        default_policy: McpPolicy::Ask,
        tool_policies: HashMap::new(),
        transport: McpServerTransport::Http(McpHttpConfig {
            url: format!("http://127.0.0.1:{port}/mcp"),
            secret_headers: HashMap::from([(
                "Authorization".into(),
                KeyringSecretRef::new("app.jarvis.workbench", "fixture-token").unwrap(),
            )]),
        }),
    };
    let manager = manager(temp.path(), &config);
    assert!(manager
        .connect(config.clone(), HashMap::new())
        .await
        .is_err());
    let tools = manager
        .connect(
            config,
            HashMap::from([("Authorization".into(), "Bearer fixture-token".into())]),
        )
        .await
        .unwrap();
    let result = manager
        .call(
            "fixture",
            "echo",
            args(),
            "http-call".into(),
            &tools[0].config_revision,
            &tools[0].connection_generation,
            true,
        )
        .await
        .unwrap();
    assert_eq!(result["content"][0]["text"], "hello");
    manager.disconnect("fixture");
}

#[tokio::test]
async fn timeout_cleans_up_handshake_and_active_call_without_replaying() {
    let temp = tempfile::tempdir().unwrap();
    let config = server(temp.path(), &["--no-initialize"]);
    let mut manager = McpManager::new(temp.path().join("config.json"));
    manager.connect_timeout = Duration::from_millis(500);
    manager.call_timeout = Duration::from_millis(500);
    manager
        .save(McpConfig {
            version: 1,
            servers: vec![config.clone()],
        })
        .unwrap();
    assert!(manager
        .connect(config, HashMap::new())
        .await
        .unwrap_err()
        .contains("timed out"));
    gone(&pids(temp.path())).await;
    let config = server(temp.path(), &[]);
    manager
        .save(McpConfig {
            version: 1,
            servers: vec![config.clone()],
        })
        .unwrap();
    let tools = manager.connect(config, HashMap::new()).await.unwrap();
    assert!(manager
        .call(
            "fixture",
            "wait",
            Map::new(),
            "timeout".into(),
            &tools[0].config_revision,
            &tools[0].connection_generation,
            true
        )
        .await
        .unwrap_err()
        .contains("timed out"));
    gone(&pids(temp.path())).await;
    assert_eq!(
        std::fs::read_to_string(temp.path().join("calls.txt"))
            .unwrap()
            .lines()
            .collect::<Vec<_>>(),
        vec!["wait"]
    );
}

#[tokio::test]
async fn window_cancel_is_scoped_and_independent_servers_are_concurrent() {
    let first = tempfile::tempdir().unwrap();
    let second = tempfile::tempdir().unwrap();
    let a = server(first.path(), &[]);
    let mut b = server(second.path(), &[]);
    b.id = "second".into();
    let manager = manager(first.path(), &a);
    manager
        .save(McpConfig {
            version: 1,
            servers: vec![a.clone(), b.clone()],
        })
        .unwrap();
    let tools = manager.connect(a, HashMap::new()).await.unwrap();
    let child_manager = manager.clone();
    let rev = tools[0].config_revision.clone();
    let generation = tools[0].connection_generation.clone();
    let key = format!("{}-held", hex::encode(Sha256::digest(b"main")));
    let held = tokio::spawn(async move {
        child_manager
            .call("fixture", "wait", Map::new(), key, &rev, &generation, true)
            .await
    });
    wait_for(|| first.path().join("calls.txt").exists()).await;
    manager.cancel_window("different-window");
    assert!(!held.is_finished());
    let tools = manager.connect(b, HashMap::new()).await.unwrap();
    assert!(manager
        .call(
            "second",
            "echo",
            args(),
            "other".into(),
            &tools[0].config_revision,
            &tools[0].connection_generation,
            true
        )
        .await
        .is_ok());
    assert!(
        !held.is_finished(),
        "waiting on one server must not block another"
    );
    manager.cancel_window("main");
    assert!(held.await.unwrap().unwrap_err().contains("cancelled"));
    gone(&pids(first.path())).await;
    assert!(
        manager
            .statuses()
            .unwrap()
            .iter()
            .find(|s| s.id == "second")
            .unwrap()
            .connected
    );
}

#[tokio::test]
async fn dropping_call_future_cannot_leave_server_tool_children_running() {
    let temp = tempfile::tempdir().unwrap();
    let config = server(temp.path(), &[]);
    let manager = manager(temp.path(), &config);
    let tools = manager.connect(config, HashMap::new()).await.unwrap();
    let rev = tools[0].config_revision.clone();
    let generation = tools[0].connection_generation.clone();
    let running = manager.clone();
    let task = tokio::spawn(async move {
        running
            .call(
                "fixture",
                "wait",
                Map::new(),
                "dropped".into(),
                &rev,
                &generation,
                true,
            )
            .await
    });
    wait_for(|| temp.path().join("calls.txt").exists()).await;
    let owned = pids(temp.path());
    task.abort();
    assert!(task.await.unwrap_err().is_cancelled());
    gone(&owned).await;
    wait_for(|| {
        let mut calls = manager.calls.lock().unwrap();
        McpManager::reap_calls(&mut calls);
        calls.is_empty()
    })
    .await;
}

#[tokio::test]
async fn dropping_handshake_future_releases_slot_and_owned_tree() {
    let temp = tempfile::tempdir().unwrap();
    let config = server(temp.path(), &["--no-initialize"]);
    let manager = manager(temp.path(), &config);
    let running = manager.clone();
    let task = tokio::spawn(async move { running.connect(config, HashMap::new()).await });
    wait_for(|| {
        std::fs::read(temp.path().join("pids.json"))
            .ok()
            .and_then(|data| serde_json::from_slice::<Vec<u32>>(&data).ok())
            .is_some()
    })
    .await;
    let owned = pids(temp.path());
    task.abort();
    assert!(task.await.unwrap_err().is_cancelled());
    gone(&owned).await;
    assert!(manager.slots.lock().unwrap().is_empty());
}

#[tokio::test]
async fn same_config_reconnect_rejects_approval_for_previous_connection_generation() {
    let temp = tempfile::tempdir().unwrap();
    let config = server(temp.path(), &[]);
    let manager = manager(temp.path(), &config);
    let original = manager
        .connect(config.clone(), HashMap::new())
        .await
        .unwrap();
    let old_pids = pids(temp.path());
    manager.disconnect("fixture");
    gone(&old_pids).await;
    let current = manager.connect(config, HashMap::new()).await.unwrap();
    assert_eq!(original[0].config_revision, current[0].config_revision);
    assert_ne!(
        original[0].connection_generation,
        current[0].connection_generation
    );
    let result = manager
        .call(
            "fixture",
            "echo",
            args(),
            "stale-connection".into(),
            &original[0].config_revision,
            &original[0].connection_generation,
            true,
        )
        .await;
    assert!(result.is_err());
    assert!(!temp.path().join("calls.txt").exists());
    let result = manager
        .call(
            "fixture",
            "echo",
            args(),
            "fresh-connection".into(),
            &current[0].config_revision,
            &current[0].connection_generation,
            true,
        )
        .await
        .unwrap();
    assert_eq!(result["content"][0]["text"], "hello");
    let owned = pids(temp.path());
    drop(manager);
    gone(&owned).await;
}

#[tokio::test]
async fn cleanup_seal_rejects_delayed_admission_and_wrong_nonce() {
    let temp = tempfile::tempdir().unwrap();
    let config = server(temp.path(), &[]);
    let manager = manager(temp.path(), &config);
    let tools = manager.connect(config, HashMap::new()).await.unwrap();
    let nonce = "0123456789abcdef0123456789abcdef";
    assert!(manager.cancel_and_settle("delayed", nonce).unwrap().settled);
    assert!(manager.cancel_and_settle("delayed", nonce).unwrap().settled);
    assert!(manager
        .cancel_and_settle("delayed", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
        .is_err());
    assert!(manager
        .call_with_nonce(
            "fixture",
            "echo",
            args(),
            "delayed".into(),
            &tools[0].config_revision,
            &tools[0].connection_generation,
            true,
            Some(nonce.into())
        )
        .await
        .unwrap_err()
        .contains("sealed"));
    // Direct callers cannot bypass a queued operation's sealed request ID.
    assert!(manager
        .call(
            "fixture",
            "echo",
            args(),
            "delayed".into(),
            &tools[0].config_revision,
            &tools[0].connection_generation,
            true
        )
        .await
        .unwrap_err()
        .contains("sealed"));
    assert!(!temp.path().join("calls.txt").exists());
    manager.disconnect("fixture");
    gone(&pids(temp.path())).await;
}

#[test]
fn cleanup_seal_capacity_never_evicts_old_tombstones() {
    let temp = tempfile::tempdir().unwrap();
    let manager = McpManager::new(temp.path().join("config.json"));
    let nonce = "0123456789abcdef0123456789abcdef";
    for n in 0..MAX_SEALED_REQUESTS {
        assert!(
            manager
                .cancel_and_settle(&format!("sealed-{n}"), nonce)
                .unwrap()
                .settled
        );
    }
    assert!(manager
        .cancel_and_settle("overflow", nonce)
        .unwrap_err()
        .contains("capacity"));
    assert!(
        manager
            .cancel_and_settle("sealed-0", nonce)
            .unwrap()
            .settled
    );
    assert_eq!(
        manager.sealed_calls.lock().unwrap().len(),
        MAX_SEALED_REQUESTS
    );
}

#[tokio::test]
async fn cleanup_probe_waits_for_lease_and_physical_tree_then_keeps_seal() {
    let temp = tempfile::tempdir().unwrap();
    let config = server(temp.path(), &[]);
    let manager = manager(temp.path(), &config);
    let tools = manager.connect(config, HashMap::new()).await.unwrap();
    let session = match manager.slots.lock().unwrap().get("fixture") {
        Some(Slot::Ready(session)) => session.clone(),
        _ => panic!("ready session"),
    };
    let running = manager.clone();
    let nonce = "0123456789abcdef0123456789abcdef";
    let task = tokio::spawn(async move {
        running
            .call_with_nonce(
                "fixture",
                "wait",
                Map::new(),
                "physical".into(),
                &tools[0].config_revision,
                &tools[0].connection_generation,
                true,
                Some(nonce.into()),
            )
            .await
    });
    wait_for(|| temp.path().join("calls.txt").exists()).await;
    let owned = pids(temp.path());
    assert!(manager
        .cancel_and_settle("physical", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
        .is_err());
    // No yield: the running future still owns its lease despite receiving Stop.
    assert!(
        !manager
            .cancel_and_settle("physical", nonce)
            .unwrap()
            .settled
    );
    assert_eq!(
        session.active_calls.load(Ordering::SeqCst) & !SESSION_STOPPED,
        1
    );
    assert!(task.await.unwrap().unwrap_err().contains("cancelled"));
    wait_for(|| {
        manager
            .cancel_and_settle("physical", nonce)
            .unwrap()
            .settled
    })
    .await;
    gone(&owned).await;
    assert_eq!(
        session.active_calls.load(Ordering::SeqCst) & !SESSION_STOPPED,
        0
    );
    assert!(manager
        .sealed_calls
        .lock()
        .unwrap()
        .contains_key("physical"));
}

#[test]
fn retained_tree_cleanup_never_signals_or_probes_a_reused_group() {
    let mut cleanup = TreeCleanup::default();
    let mut signals = 0;
    cleanup.stop(|| signals += 1);
    assert!(!cleanup.settled(|| false));
    cleanup.stop(|| signals += 1);
    assert!(cleanup.settled(|| true));
    // Model later stop/Drop and a recycled group becoming live again.
    cleanup.stop(|| signals += 1);
    assert!(cleanup.settled(|| panic!("settled group must never be probed again")));
    assert_eq!(signals, 1);
    let mut already_gone = TreeCleanup::default();
    assert!(already_gone.settled(|| true));
    already_gone.stop(|| panic!("never signal a group already proven gone"));
}
