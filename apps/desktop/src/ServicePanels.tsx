import { useEffect, useState } from "react";
import { load } from "@tauri-apps/plugin-store";
import { Search, RefreshCw, MessageSquare, ShieldCheck } from "lucide-react";
import { t } from "./localization";
import { ApiUsagePanel } from "./ApiUsagePanel";

type Run = <T>(method: string, params?: unknown) => Promise<T | undefined>;
type Capability = {
  name: string;
  description: string;
  category: string;
  executor: string;
  approval: string;
  available: boolean;
  description_owned?: boolean;
};
type Catalogue = {
  items: Capability[];
  total: number;
  next_cursor: number | null;
};
export function CapabilitiesPanel({
  run,
  select,
}: {
  run: Run;
  select: (section: string) => void;
}) {
  const [items, setItems] = useState<Capability[]>([]),
    [query, setQuery] = useState(""),
    [category, setCategory] = useState(""),
    [busy, setBusy] = useState(true),
    [error, setError] = useState("");
  const refresh = async () => {
    setBusy(true);
    setError("");
    try {
      const all: Capability[] = [];
      let cursor: number | null = 0;
      while (cursor !== null) {
        const page: Catalogue | undefined = await run<Catalogue>(
          "services.catalog",
          { cursor, limit: 100, include_parameters: false },
        );
        if (!page)
          throw new Error(
            t("Could not load the tool catalogue. Try refreshing."),
          );
        all.push(...page.items);
        cursor = page.next_cursor;
        if (all.length > 1000) throw new Error("Tool catalogue is too large");
      }
      setItems(all);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    void refresh();
  }, []);
  const needle = query.toLocaleLowerCase();
  const description = (item: Capability) => item.description_owned === true ? t(item.description) : item.description;
  const shown = items.filter(
    (item) =>
      (!category || item.category === category) &&
      `${item.name} ${item.description} ${description(item)}`.toLocaleLowerCase().includes(needle),
  );
  const useTool = (item: Capability) => {
    select("Chat");
    window.dispatchEvent(
      new CustomEvent("jarvis-focus-chat", {
        detail: `Use ${item.name} for this task: `,
      }),
    );
  };
  return (
    <section className="service-panel" aria-label={t("Tool catalogue")}>
      <p>
        {t(
          "These tools are shared by cloud and local models. Connection requirements and confirmations still apply.",
        )}
      </p>
      <div className="service-toolbar">
        <label className="service-search">
          <Search size={16} />
          <input
            aria-label={t("Search tools")}
            placeholder={t("Search tools")}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <select
          aria-label={t("Tool category")}
          value={category}
          onChange={(e) => setCategory(e.target.value)}
        >
          <option value="">{t("All categories")}</option>
          {[...new Set(items.map((i) => i.category))].sort().map((c) => (
            <option key={c}>{c}</option>
          ))}
        </select>
        <button disabled={busy} onClick={() => void refresh()}>
          <RefreshCw size={16} />
          {t("Refresh")}
        </button>
      </div>
      {busy && <p role="status">{t("Loading…")}</p>}
      {error && <p role="alert">{error}</p>}
      {!busy && !error && (
        <p role="status">
          {t("Tools shown")}: {shown.length} / {items.length}
        </p>
      )}
      <div className="service-shortcuts">
        {["Models", "Browser", "Documents", "Files & Tools"].map((section) => (
          <button key={section} onClick={() => select(section)}>
            {t(section)}
          </button>
        ))}
      </div>
      <div className="service-cards">
        {shown.map((item) => (
          <article
            key={`${item.category}:${item.name}`}
            data-capability={item.name}
          >
            <header>
              <strong>{item.name}</strong>
              <span>{item.category}</span>
            </header>
            <p>{description(item)}</p>
            <footer>
              <span>
                <ShieldCheck size={14} />
                {t(
                  item.approval === "none"
                    ? "No action confirmation"
                    : "Confirmation depends on the action",
                )}
              </span>
              <button disabled={!item.available} onClick={() => useTool(item)}>
                <MessageSquare size={14} />
                {t("Use in chat")}
              </button>
            </footer>
          </article>
        ))}
      </div>
      {!busy && !error && shown.length === 0 && (
        <p>{t("No tools match your search.")}</p>
      )}
    </section>
  );
}
type Session = {
  id: string;
  title: string;
  usage?: {
    tokens: {
      inputTokens: number;
      outputTokens: number;
      cachedInputTokens: number;
    };
    lastInputTokens: number;
  };
};
export function UsagePanel({ run }: { run: Run }) {
  const [sessions, setSessions] = useState<Session[]>([]),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const refresh = async () => {
    setBusy(true);
    setError("");
    try {
      const store = await load("terax-ai-sessions.json", {
        defaults: {},
        autoSave: false,
      });
      setSessions((await store.get<Session[]>("sessions")) ?? []);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    void refresh();
  }, []);
  const number = (value?: number) =>
    typeof value === "number" && Number.isFinite(value) && value >= 0
      ? value.toLocaleString()
      : "0";
  return (
    <section className="service-panel" aria-label={t("Conversation usage")}>
      <h4>{t("Conversation usage")}</h4>
      <p>
        {t(
          "Saved counters for all providers, including local models. These are reported tokens, not remaining quota or a billing estimate.",
        )}
      </p>
      <button disabled={busy} onClick={() => void refresh()}>
        <RefreshCw size={16} />
        {t("Refresh counters")}
      </button>
      {error && <p role="alert">{error}</p>}
      <div className="api-table-wrap">
        <table>
          <thead>
            <tr>
              <th>{t("Chat")}</th>
              <th>{t("Input")}</th>
              <th>{t("Output")}</th>
              <th>{t("Cached")}</th>
              <th>{t("Last request")}</th>
            </tr>
          </thead>
          <tbody>
            {sessions
              .filter((s) => s.usage)
              .map((s) => (
                <tr key={s.id}>
                  <td>{s.title}</td>
                  <td>{number(s.usage?.tokens?.inputTokens)}</td>
                  <td>{number(s.usage?.tokens?.outputTokens)}</td>
                  <td>{number(s.usage?.tokens?.cachedInputTokens)}</td>
                  <td>{number(s.usage?.lastInputTokens)}</td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>
      {!sessions.some((s) => s.usage) && (
        <p>{t("No reported conversation usage yet.")}</p>
      )}
      <details>
        <summary>{t("Google SDK counters and model access")}</summary>
        <ApiUsagePanel run={run} />
      </details>
    </section>
  );
}
