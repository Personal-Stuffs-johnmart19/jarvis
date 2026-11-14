use super::{
    config::{McpConfig, McpServerConfig, McpServerTransport},
    runtime::{CallSettlement, McpManager, ServerStatus, ToolInfo},
};
use serde::Serialize;
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use tauri::{AppHandle, State, WebviewWindow};

fn request_key(window: &WebviewWindow, id: &str) -> Result<String, String> {
    if id.is_empty()
        || id.len() > 48
        || !id
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"_-".contains(&b))
    {
        return Err("Invalid MCP request ID".into());
    }
    Ok(format!(
        "{}-{}",
        hex::encode(Sha256::digest(window.label().as_bytes())),
        id
    ))
}
async fn credentials(
    app: &AppHandle,
    server: &McpServerConfig,
) -> Result<HashMap<String, String>, String> {
    let references = match &server.transport {
        McpServerTransport::Stdio(config) => &config.secret_env,
        McpServerTransport::Http(config) => &config.secret_headers,
    };
    let mut resolved = HashMap::new();
    for (name, reference) in references {
        let secret = crate::modules::secrets::secrets_get(
            app.clone(),
            reference.service.clone(),
            reference.account.clone(),
        )
        .await?
        .ok_or_else(|| format!("Save the MCP credential for {name} before connecting"))?;
        if secret.len() > 65536 {
            return Err("MCP credential exceeds its size limit".into());
        }
        resolved.insert(name.clone(), secret);
    }
    Ok(resolved)
}

#[tauri::command]
pub fn mcp_config_get(state: State<'_, McpManager>) -> Result<McpConfig, String> {
    state.config()
}
#[tauri::command]
pub fn mcp_config_save(config: McpConfig, state: State<'_, McpManager>) -> Result<(), String> {
    state.save(config)
}
#[tauri::command]
pub fn mcp_status(state: State<'_, McpManager>) -> Result<Vec<ServerStatus>, String> {
    state.statuses()
}
#[tauri::command]
pub async fn mcp_connect(
    app: AppHandle,
    server_id: String,
    state: State<'_, McpManager>,
) -> Result<Vec<ToolInfo>, String> {
    let server = state.server(&server_id)?;
    let secrets = credentials(&app, &server).await?;
    state.connect(server, secrets).await
}
#[tauri::command]
pub fn mcp_disconnect(server_id: String, state: State<'_, McpManager>) {
    state.disconnect(&server_id);
}
#[derive(Serialize)]
pub struct ToolsPage {
    items: Vec<ToolInfo>,
    total: usize,
    next_cursor: Option<usize>,
}
#[tauri::command]
pub fn mcp_tools(
    server_id: Option<String>,
    query: Option<String>,
    cursor: Option<usize>,
    limit: Option<usize>,
    include_parameters: Option<bool>,
    state: State<'_, McpManager>,
) -> Result<ToolsPage, String> {
    let query = query.unwrap_or_default().to_lowercase();
    if query.len() > 256 {
        return Err("MCP search query is too long".into());
    }
    let mut tools = state.tools(server_id.as_deref())?;
    tools.retain(|tool| {
        query.is_empty()
            || format!("{} {} {}", tool.server_id, tool.name, tool.description)
                .to_lowercase()
                .contains(&query)
    });
    let total = tools.len();
    let start = cursor.unwrap_or_default().min(total);
    let limit = limit.unwrap_or(10).clamp(1, 20);
    let items = tools
        .into_iter()
        .skip(start)
        .take(limit)
        .map(|mut tool| {
            if !include_parameters.unwrap_or(false) {
                tool.input_schema = Value::Null;
            }
            tool.description = tool.description.chars().take(1024).collect();
            tool
        })
        .collect();
    Ok(ToolsPage {
        items,
        total,
        next_cursor: (start + limit < total).then_some(start + limit),
    })
}
#[tauri::command]
pub fn mcp_tool_info(
    server_id: String,
    tool_name: String,
    state: State<'_, McpManager>,
) -> Result<ToolInfo, String> {
    state
        .tools(Some(&server_id))?
        .into_iter()
        .find(|tool| tool.name == tool_name)
        .ok_or_else(|| "MCP tool is not connected or discovered".into())
}
#[tauri::command]
pub async fn mcp_call_tool(
    window: WebviewWindow,
    server_id: String,
    tool_name: String,
    arguments: Map<String, Value>,
    request_id: String,
    config_revision: String,
    connection_generation: String,
    approved: bool,
    cleanup_nonce: Option<String>,
    state: State<'_, McpManager>,
) -> Result<Value, String> {
    state
        .call_with_nonce(
            &server_id,
            &tool_name,
            arguments,
            request_key(&window, &request_id)?,
            &config_revision,
            &connection_generation,
            approved,
            cleanup_nonce,
        )
        .await
}
#[tauri::command]
pub fn mcp_cancel(
    window: WebviewWindow,
    request_id: String,
    cleanup_nonce: Option<String>,
    state: State<'_, McpManager>,
) -> Result<Option<CallSettlement>, String> {
    let key = request_key(&window, &request_id)?;
    match cleanup_nonce {
        Some(nonce) => state.cancel_and_settle(&key, &nonce).map(Some),
        None => state.cancel(&key).map(|()| None),
    }
}

#[tauri::command]
pub fn mcp_config_validate(config: McpConfig) -> Result<(), String> {
    config.validate()
}
