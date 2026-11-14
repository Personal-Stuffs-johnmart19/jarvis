import {
  Settings2,
  SlidersHorizontal,
  Bot,
  Palette,
  AudioLines,
  Globe,
  Files,
  ChartNoAxesCombined,
  ListMusic,
  Code2,
  Keyboard,
  Network,
  Camera,
  Sparkles,
  Info,
  X,
  Search,
  Terminal,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { t } from "./localization";
import { LanguagePicker } from "./i18n";
import { UtilitiesPanel } from "./UtilitiesPanel";
import { SettingsPanel } from "./SettingsPanel";
import { UsagePanel, CapabilitiesPanel } from "./ServicePanels";
import { EmbeddingPanel } from "./EmbeddingPanel";
import { TranslationPanel } from "./TranslationPanel";
import { Snapshot } from "./bridge";
import { applyWorkbenchTheme, workbenchTabs } from "./Workbench";

export const commonSections = [
  "General",
  "Models",
  "Integrations",
  "Agents",
  "Themes",
  "Speech & Voice",
  "Browser",
  "Documents",
  "API Usage",
  "Queue",
] as const;
const sectionIcons: Record<string, typeof Settings2> = {
  General: Settings2,
  Models: SlidersHorizontal,
  Integrations: Network,
  Agents: Bot,
  Themes: Palette,
  "Speech & Voice": AudioLines,
  Browser: Globe,
  Documents: Files,
  "API Usage": ChartNoAxesCombined,
  Queue: ListMusic,
  Editor: Code2,
  Shortcuts: Keyboard,
  "Network & Proxy": Network,
  Camera,
  Prompts: Sparkles,
  Features: Info,
  About: Info,
  Utilities: Terminal,
};
const advanced = [
  "Editor",
  "Shortcuts",
  "Utilities",
  "Network & Proxy",
  "Camera",
  "Prompts",
  "Features",
  "About",
];
type Props = {
  section: string;
  select: (value: string) => void;
  close: () => void;
  snapshot: Snapshot | null;
  hints: [string, string][];
  setHints: (v: [string, string][]) => void;
  run: <T = any>(method: string, params?: unknown) => Promise<T | undefined>;
  refresh: () => Promise<void>;
  onMode: (v: string) => void;
};
export function GlobalSettings({
  section,
  select,
  close,
  onMode,
  ...props
}: Props) {
  const [query, setQuery] = useState("");
  const [libraryKind, setLibraryKind] = useState<string | null>(null);
  useEffect(() => {
    const open = (event: Event) => {
      if ((event as CustomEvent).detail !== "tts") return;
      setLibraryKind("tts");
      select("Models");
    };
    window.addEventListener("jarvis:model-library", open);
    return () => window.removeEventListener("jarvis:model-library", open);
  }, [select]);
  useEffect(() => { if (section !== "Models") setLibraryKind(null); }, [section]);
  const sectionLabel = (name: string) =>
    t(name === "Models" ? "Models & Providers" : name);
  const matches = (name: string) =>
    `${name} ${sectionLabel(name)}`
      .toLocaleLowerCase()
      .includes(query.trim().toLocaleLowerCase());
  const visibleCommon = commonSections.filter(matches);
  const visibleAdvanced = advanced.filter(matches);
  const frame = useRef<HTMLIFrameElement>(null);
  const backdrop = useRef<HTMLDivElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const closeRef = useRef(close);
  closeRef.current = close;
  useEffect(() => {
    const previous = document.activeElement;
    const overflow = [
      document.documentElement.style.overflow,
      document.body.style.overflow,
    ];
    document.documentElement.style.overflow = "hidden";
    document.body.style.overflow = "hidden";
    const siblings = Array.from(
      backdrop.current?.parentElement?.children ?? [],
    ).filter(
      (node): node is HTMLElement =>
        node instanceof HTMLElement && node !== backdrop.current,
    );
    const states = siblings.map((node) => node.inert);
    siblings.forEach((node) => (node.inert = true));
    closeButton.current?.focus();
    return () => {
      document.documentElement.style.overflow = overflow[0];
      document.body.style.overflow = overflow[1];
      siblings.forEach((node, index) => (node.inert = states[index]));
      if (previous instanceof HTMLElement && previous.isConnected)
        previous.focus();
    };
  }, []);
  const shared = workbenchTabs[section];
  const sendSection = () => frame.current?.contentWindow?.postMessage(
    { type: "jarvis:settings-tab", tab: shared || "general", libraryKind }, location.origin,
  );
  useEffect(sendSection, [shared, libraryKind]);
  useEffect(() => {
    const receive = (event: MessageEvent) => {
      if (
        event.origin !== location.origin ||
        event.source !== frame.current?.contentWindow
      )
        return;
      if (event.data?.type === "jarvis:settings-ready") sendSection();
      if (event.data?.type === "jarvis:close-settings") closeRef.current();
      if (event.data?.type === "jarvis:settings")
        select(
          Object.keys(workbenchTabs).find(
            (k) => workbenchTabs[k] === event.data.tab,
          ) || "General",
        );
      if (event.data?.type === "jarvis:theme" && event.data.colors) {
        applyWorkbenchTheme(event.data.colors);
        if (["dark", "light"].includes(event.data.mode))
          onMode(event.data.mode);
      }
    };
    window.addEventListener("message", receive);
    return () => window.removeEventListener("message", receive);
  }, [select, onMode, shared, libraryKind]);
  return (
    <div
      ref={backdrop}
      className="modal-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) close();
      }}
    >
      <section
        className="global-settings modal glass"
        role="dialog"
        aria-modal="true"
        aria-label={t("Settings")}
      >
        <header>
          <div className="settings-heading">
            <span className="settings-heading-icon">
              <Settings2 />
            </span>
            <div>
              <h2>{t("Settings")}</h2>
              <p>{t("One place for your models, tools and preferences.")}</p>
            </div>
          </div>
          <div className="settings-header-actions">
            <LanguagePicker />
            <button
              ref={closeButton}
              className="icon"
              onClick={close}
              aria-label={t("Close Settings")}
            >
              <X size={20} />
            </button>
          </div>
        </header>
        <div className="global-settings-layout">
          <nav
            aria-label={t("Settings sections")}
            className="settings-navigation"
          >
            <label className="settings-search">
              <Search size={16} aria-hidden="true" />
              <input
                type="search"
                aria-label={t("Search settings")}
                placeholder={t("Search settings")}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
            </label>
            <label className="settings-mobile-select">
              {t("Settings sections")}
              <select
                value={section}
                onChange={(event) => select(event.target.value)}
              >
                {[...commonSections, ...advanced].map((name) => (
                  <option key={name} value={name}>
                    {sectionLabel(name)}
                  </option>
                ))}
              </select>
            </label>
            {visibleCommon.length === 0 && visibleAdvanced.length === 0 && (
              <p role="status" className="settings-empty">
                {t("No matching settings")}
              </p>
            )}
            {visibleCommon.map((name) => {
              const Icon = sectionIcons[name];
              return (
                <button
                  key={name}
                  aria-current={section === name ? "page" : undefined}
                  className={section === name ? "active" : ""}
                  onClick={() => select(name)}
                >
                  <Icon size={18} />
                  <span>
                    {t(name === "Models" ? "Models & Providers" : name)}
                  </span>
                </button>
              );
            })}
            <details
              className="settings-advanced"
              open={Boolean(query.trim()) || advanced.includes(section)}
              hidden={!visibleAdvanced.length}
            >
              <summary>{t("Advanced Settings")}</summary>
              {visibleAdvanced.map((name) => {
                const Icon = sectionIcons[name];
                return (
                  <button
                    key={name}
                    aria-current={section === name ? "page" : undefined}
                    className={section === name ? "active" : ""}
                    onClick={() => select(name)}
                  >
                    <Icon size={18} />
                    <span>{t(name)}</span>
                  </button>
                );
              })}
            </details>
          </nav>
          <div className="global-settings-content">
            <iframe
                ref={frame}
                src="workbench-settings.html?embedded=1"
                hidden={!shared}
                onLoad={sendSection}
                title={t(section)}
              />
            {!shared && (
              <div className="global-settings-scroll">
                <h3>{t(section)}</h3>
                {section === "Utilities" ? (
                  <UtilitiesPanel />
                ) : section === "Documents" ? (
                  <>
                    <EmbeddingPanel run={props.run} />
                    <TranslationPanel run={props.run} />
                  </>
                ) : section === "API Usage" ? (
                  <UsagePanel run={props.run} />
                ) : section === "Features" ? (
                  <CapabilitiesPanel run={props.run} select={select} />
                ) : (
                  <SettingsPanel
                    key={section}
                    tab={section}
                    setTab={select}
                    {...props}
                  />
                )}
              </div>
            )}
          </div>
        </div>
      </section>
    </div>
  );
}
