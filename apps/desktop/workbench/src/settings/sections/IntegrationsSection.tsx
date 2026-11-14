import { confirmAction } from "../../../../src/confirmAction";
import { t as translateMessage } from "../../../../src/localization";
import { ProviderIntegrations } from "./ModelsSection";
import { useEffect, useRef, useState } from "react";
import { invoke } from "@/host/core";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { t } from "../../../../src/localization";
import {
  compileServer,
  canStopMcpServer,
  replaceServer,
  serverDraft,
  type ServerDraft,
} from "@/modules/mcp/settings";
import type {
  McpConfig,
  McpPolicy,
  McpServer,
  McpStatus,
  McpTool,
} from "@/modules/mcp/types";

const field =
  "w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground";
type ToolPage = { items: McpTool[]; total: number; next_cursor: number | null };
export function IntegrationsSection() {
  const [config, setConfig] = useState<McpConfig>({ version: 1, servers: [] });
  const [status, setStatus] = useState<McpStatus[]>([]);
  const [draft, setDraft] = useState<ServerDraft | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [selected, setSelected] = useState("");
  const [tools, setTools] = useState<ToolPage | null>(null);
  const [query, setQuery] = useState("");
  const [cursor, setCursor] = useState(0);
  const [importText, setImportText] = useState("");
  const mounted = useRef(true);
  const toolSequence = useRef(0);
  const refresh = async () => {
    const [next, states] = await Promise.all([
      invoke<McpConfig>("mcp_config_get"),
      invoke<McpStatus[]>("mcp_status"),
    ]);
    if (mounted.current) {
      setConfig(next);
      setStatus(states);
      setLoaded(true);
    }
  };
  useEffect(() => {
    mounted.current = true;
    void refresh().catch((error) => {
      if (mounted.current) setError(String(error));
    });
    const timer = setInterval(() => {
      void invoke<McpStatus[]>("mcp_status")
        .then((value) => {
          if (mounted.current) setStatus(value);
        })
        .catch(() => {});
    }, 5000);
    return () => {
      mounted.current = false;
      toolSequence.current++;
      clearInterval(timer);
    };
  }, []);
  const operate = async (name: string, action: () => Promise<void>) => {
    setBusy(name);
    setError("");
    setMessage("");
    try {
      await action();
      await refresh();
    } catch (error) {
      if (mounted.current) setError(String(error));
    } finally {
      if (mounted.current) setBusy("");
    }
  };
  const save = async () => {
    if (!draft) return;
    const compiled = compileServer(draft);
    const latest = await invoke<McpConfig>("mcp_config_get");
    const next = replaceServer(latest, compiled.server);
    await invoke("mcp_config_validate", { config: next });
    for (const secret of compiled.writes) await invoke("secrets_set", secret);
    await invoke("mcp_config_save", { config: next });
    if (mounted.current) {
      setDraft(null);
      setTools(null);
      setMessage(
        t("Saved. Enabled servers can be connected on demand by the agent."),
      );
    }
  };
  const discover = async (id: string, start = 0, search = query) => {
    const sequence = ++toolSequence.current;
    await invoke("mcp_connect", { serverId: id });
    const page = await invoke<ToolPage>("mcp_tools", {
      serverId: id,
      query: search,
      cursor: start,
      limit: 20,
      includeParameters: false,
    });
    if (mounted.current && toolSequence.current === sequence) {
      setSelected(id);
      setTools(page);
      setCursor(start);
      setQuery(search);
    }
  };
  const changePolicy = async (tool: McpTool, value: string) => {
    const latest = await invoke<McpConfig>("mcp_config_get");
    const server = latest.servers.find((s) => s.id === tool.server_id);
    if (!server) throw new Error("MCP server was removed");
    const policies = { ...server.tool_policies };
    if (value === "inherit") delete policies[tool.name];
    else policies[tool.name] = value as McpPolicy;
    await invoke("mcp_config_save", {
      config: replaceServer(latest, { ...server, tool_policies: policies }),
    });
    await discover(server.id, cursor);
  };
  const remove = async (server: McpServer) => {
    if (
      !await confirmAction(
        t(
          "Remove this MCP integration and stop its owned processes? Saved credentials will be retained.",
        ),
      )
    )
      return;
    const latest = await invoke<McpConfig>("mcp_config_get");
    await invoke("mcp_config_save", {
      config: {
        ...latest,
        servers: latest.servers.filter((s) => s.id !== server.id),
      },
    });
    if (mounted.current && selected === server.id) {
      setTools(null);
      setSelected("");
      toolSequence.current++;
    }
  };
  const importConfig = async () => {
    if (new TextEncoder().encode(importText).length > 1024 * 1024)
      throw new Error("Configuration is limited to 1 MiB");
    const imported = JSON.parse(importText) as McpConfig;
    if (imported.version !== 1 || !Array.isArray(imported.servers))
      throw new Error(
        "Use a Jarvis MCP configuration with version 1 and a servers array.",
      );
    const latest = await invoke<McpConfig>("mcp_config_get");
    if (
      imported.servers.some((server) =>
        latest.servers.some((s) => s.id === server.id),
      )
    )
      throw new Error(
        "An imported server ID already exists. Edit or remove the existing integration first.",
      );
    const next = {
      ...latest,
      servers: [
        ...latest.servers,
        ...imported.servers.map((server) => ({ ...server, enabled: false })),
      ],
    };
    await invoke("mcp_config_validate", { config: next });
    await invoke("mcp_config_save", { config: next });
    if (mounted.current) {
      setImportText("");
      setMessage(
        t(
          "Imported with all new servers disabled. Review and enable each integration before use.",
        ),
      );
    }
  };
  const setField = <K extends keyof ServerDraft>(
    name: K,
    value: ServerDraft[K],
  ) =>
    setDraft((current) => (current ? { ...current, [name]: value } : current));

  return (
    <div className="mx-auto w-full max-w-5xl space-y-5 p-6 text-foreground">
      <header className="space-y-2">
        <h2 className="text-xl font-semibold">{t("Integrations")}</h2>
        <p className="text-sm text-muted-foreground">
          {t(
            "Connect MCP tools to every Jarvis chat and agent. Models share the same catalogue, settings and confirmation flow.",
          )}
        </p>
      </header>
      <ProviderIntegrations />
      <h2 className="text-lg font-semibold">{t("MCP tools")}</h2>
      {error && (
        <p
          role="alert"
          className="break-words rounded-md border border-destructive/40 p-3 text-sm text-destructive"
        >
          {error}
        </p>
      )}
      {message && (
        <p role="status" className="text-sm text-muted-foreground">
          {message}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button
          disabled={!loaded || !!busy}
          onClick={() => setDraft(serverDraft())}
        >
          {t("Add MCP server")}
        </Button>
        <Button
          variant="outline"
          disabled={!!busy}
          onClick={() => void operate("refresh", refresh)}
        >
          {t("Refresh")}
        </Button>
      </div>
      {draft && (
        <section
          aria-label={t("MCP server configuration")}
          className="space-y-4 rounded-xl border border-border bg-card/60 p-4"
        >
          <div className="flex items-center justify-between gap-3">
            <h3 className="font-semibold">{t("MCP server")}</h3>
            <span className="font-mono text-xs text-muted-foreground">
              {draft.id}
            </span>
          </div>
          <label className="block space-y-1 text-sm">
            {t("Name")}
            <Input
              value={draft.name}
              placeholder={t("Files, GitHub, documentation…")}
              onChange={(e) => setField("name", e.target.value)}
            />
          </label>
          <label className="block space-y-1 text-sm">
            {t("Transport")}
            <select
              className={field}
              value={draft.type}
              onChange={(e) =>
                setDraft({
                  ...draft,
                  type: e.target.value as "stdio" | "http",
                  secrets: [],
                })
              }
            >
              <option value="stdio">{t("Local process (stdio)")}</option>
              <option value="http">{t("Streamable HTTP (JSON / SSE)")}</option>
            </select>
          </label>
          {draft.type === "stdio" ? (
            <>
              <label className="block space-y-1 text-sm">
                {t("Executable")}
                <Input
                  value={draft.command}
                  placeholder={translateMessage("npx, uvx, python, or an executable path")}
                  onChange={(e) => setField("command", e.target.value)}
                />
              </label>
              <label className="block space-y-1 text-sm">
                {t("Arguments (JSON array)")}
                <textarea
                  aria-label={t("Arguments (JSON array)")}
                  className={field + " font-mono"}
                  rows={3}
                  value={draft.args}
                  onChange={(e) => setField("args", e.target.value)}
                  placeholder={
                    translateMessage("[\"-y\", \"@modelcontextprotocol/server-filesystem\", \"D:/Documents\"]")
                  }
                />
              </label>
              <label className="block space-y-1 text-sm">
                {t("Working directory (optional)")}
                <Input
                  value={draft.cwd}
                  onChange={(e) => setField("cwd", e.target.value)}
                />
              </label>
            </>
          ) : (
            <label className="block space-y-1 text-sm">
              {t("Endpoint URL")}
              <Input
                value={draft.url}
                onChange={(e) => setField("url", e.target.value)}
              />
            </label>
          )}
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={draft.enabled}
              onChange={(e) => setField("enabled", e.target.checked)}
            />
            {t("Enable this integration and allow Jarvis to connect on demand")}
          </label>
          <label className="block space-y-1 text-sm">
            {t("Default tool policy")}
            <select
              className={field}
              value={draft.policy}
              onChange={(e) => setField("policy", e.target.value as McpPolicy)}
            >
              <option value="ask">{t("Ask for confirmation")}</option>
              <option value="allow">{t("Allow")}</option>
              <option value="deny">{t("Deny")}</option>
            </select>
          </label>
          <div className="space-y-2">
            <h4 className="text-sm font-medium">
              {draft.type === "http"
                ? t("Credential headers")
                : t("Credential environment variables")}
            </h4>
            <p className="text-xs text-muted-foreground">
              {t(
                "Values stay in the native keyring. Leave a saved value blank to keep it. An Authorization header value usually starts with Bearer.",
              )}
            </p>
            {draft.secrets.map((secret, index) => (
              <div className="flex flex-wrap gap-2" key={index}>
                <Input
                  aria-label={t("Credential name")}
                  className="min-w-32 flex-1"
                  value={secret.name}
                  onChange={(e) =>
                    setField(
                      "secrets",
                      draft.secrets.map((row, i) =>
                        i === index ? { ...row, name: e.target.value } : row,
                      ),
                    )
                  }
                />
                <Input
                  aria-label={t("Credential value")}
                  type="password"
                  autoComplete="new-password"
                  className="min-w-40 flex-1"
                  placeholder={
                    secret.reference ? t("Saved in keyring") : t("Value")
                  }
                  value={secret.value}
                  onChange={(e) =>
                    setField(
                      "secrets",
                      draft.secrets.map((row, i) =>
                        i === index ? { ...row, value: e.target.value } : row,
                      ),
                    )
                  }
                />
                <Button
                  variant="ghost"
                  onClick={() =>
                    setField(
                      "secrets",
                      draft.secrets.filter((_, i) => i !== index),
                    )
                  }
                >
                  {t("Remove")}
                </Button>
              </div>
            ))}
            <Button
              size="sm"
              variant="outline"
              onClick={() =>
                setField("secrets", [
                  ...draft.secrets,
                  {
                    name: draft.type === "http" ? "Authorization" : "API_KEY",
                    value: "",
                  },
                ])
              }
            >
              {t("Add credential")}
            </Button>
          </div>
          <details>
            <summary className="cursor-pointer text-sm">
              {t("Advanced per-tool policies")}
            </summary>
            <textarea
              className={field + " mt-2 font-mono"}
              rows={4}
              aria-label={t("Per-tool policies (JSON object)")}
              value={draft.policies}
              onChange={(e) => setField("policies", e.target.value)}
            />
          </details>
          <p className="text-xs text-muted-foreground">
            {t(
              "Saving changed server settings disconnects that server and cancels its active calls. Imported integrations remain disabled until you enable them.",
            )}
          </p>
          <div className="flex gap-2">
            <Button
              disabled={!!busy}
              onClick={() => void operate("save", save)}
            >
              {busy === "save" ? t("Saving…") : t("Save")}
            </Button>
            <Button
              variant="outline"
              disabled={!!busy}
              onClick={() => setDraft(null)}
            >
              {t("Cancel")}
            </Button>
          </div>
        </section>
      )}
      <section className="space-y-3" aria-label={t("Configured MCP servers")}>
        {!config.servers.length && loaded && (
          <p className="text-sm text-muted-foreground">
            {t("No MCP integrations configured yet.")}
          </p>
        )}
        {config.servers.map((server) => {
          const current = status.find((item) => item.id === server.id);
          return (
            <article
              key={server.id}
              className="space-y-3 rounded-xl border border-border bg-card/60 p-4"
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <h3 className="font-semibold">{server.name || server.id}</h3>
                  <p className="text-xs text-muted-foreground">
                    {server.transport.type === "stdio"
                      ? t("Local process")
                      : "HTTP"}{" "}
                    ·{" "}
                    {current?.connecting
                      ? t("Connecting…")
                      : current?.connected
                        ? t("Connected")
                        : server.enabled
                          ? t("Ready to connect")
                          : t("Disabled")}{" "}
                    · {server.default_policy}
                  </p>
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={!!busy || !server.enabled}
                    onClick={() =>
                      void operate(server.id, () => discover(server.id, 0, ""))
                    }
                  >
                    {t("Connect / Discover")}
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={!canStopMcpServer(server.id, busy, current)}
                    onClick={() =>
                      void operate(server.id, async () => {
                        await invoke("mcp_disconnect", { serverId: server.id });
                        toolSequence.current++;
                        if (selected === server.id) setTools(null);
                      })
                    }
                  >
                    {t("Stop")}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={!!busy}
                    onClick={() => setDraft(serverDraft(server))}
                  >
                    {t("Edit")}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={!!busy}
                    onClick={() =>
                      void operate(server.id, () => remove(server))
                    }
                  >
                    {t("Remove")}
                  </Button>
                </div>
              </div>
              <p className="truncate font-mono text-xs text-muted-foreground">
                {server.id}
              </p>
            </article>
          );
        })}
      </section>
      {tools && selected && (
        <section
          className="space-y-3 rounded-xl border border-border p-4"
          aria-label={t("Discovered MCP tools")}
        >
          <h3 className="font-semibold">
            {t("Tools")} ·{" "}
            {config.servers.find((s) => s.id === selected)?.name || selected}
          </h3>
          <form
            className="flex gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              void operate("tools", () => discover(selected, 0));
            }}
          >
            <Input
              aria-label={t("Search MCP tools")}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <Button variant="outline" disabled={!!busy}>
              {t("Search")}
            </Button>
          </form>
          <div className="space-y-3">
            {tools.items.map((tool) => (
              <div
                key={tool.name}
                className="flex flex-wrap justify-between gap-3 border-t border-border pt-3"
              >
                <div className="min-w-40 flex-1">
                  <p className="break-all font-mono text-sm">{tool.name}</p>
                  <p className="break-words text-xs text-muted-foreground">
                    {tool.description}
                  </p>
                </div>
                <select
                  aria-label={translateMessage("{0} {1}", tool.name, t("policy"))}
                  className="rounded-md border border-border bg-background p-2 text-xs"
                  disabled={!!busy}
                  value={
                    config.servers.find((s) => s.id === selected)
                      ?.tool_policies?.[tool.name] ?? "inherit"
                  }
                  onChange={(e) =>
                    void operate("policy", () =>
                      changePolicy(tool, e.target.value),
                    )
                  }
                >
                  <option value="inherit">{t("Use server default")}</option>
                  <option value="ask">{t("Ask")}</option>
                  <option value="allow">{t("Allow")}</option>
                  <option value="deny">{t("Deny")}</option>
                </select>
              </div>
            ))}
          </div>
          <div className="flex items-center gap-3 text-xs text-muted-foreground">
            <Button
              size="sm"
              variant="outline"
              disabled={!!busy || cursor === 0}
              onClick={() =>
                void operate("tools", () =>
                  discover(selected, Math.max(0, cursor - 20)),
                )
              }
            >
              {t("Previous")}
            </Button>
            <span>
              {cursor + Math.min(1, tools.items.length)}–
              {cursor + tools.items.length} / {tools.total}
            </span>
            <Button
              size="sm"
              variant="outline"
              disabled={!!busy || tools.next_cursor === null}
              onClick={() =>
                void operate("tools", () =>
                  discover(selected, tools.next_cursor ?? 0),
                )
              }
            >
              {t("Next")}
            </Button>
          </div>
        </section>
      )}
      <details className="rounded-xl border border-border p-4">
        <summary className="cursor-pointer text-sm font-medium">
          {t("Import / Export Jarvis MCP configuration")}
        </summary>
        <p className="my-2 text-xs text-muted-foreground">
          {t(
            "Configuration contains keyring references, not credential values. Imported servers are disabled and are not started automatically.",
          )}
        </p>
        <textarea
          aria-label={t("MCP configuration JSON")}
          className={field + " font-mono"}
          rows={7}
          value={importText}
          onChange={(e) => setImportText(e.target.value)}
        />
        <div className="mt-2 flex gap-2">
          <Button
            size="sm"
            disabled={!!busy || !loaded || !importText.trim()}
            onClick={() => void operate("import", importConfig)}
          >
            {t("Import disabled servers")}
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={!!busy || !loaded}
            onClick={() => setImportText(JSON.stringify(config, null, 2))}
          >
            {t("Show current configuration")}
          </Button>
        </div>
      </details>
    </div>
  );
}
