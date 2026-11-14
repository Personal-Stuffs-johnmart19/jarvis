import { displayError, errorText, type DisplayError } from "./displayError";
import { explicitAgentStop } from "../workbench/src/modules/ai/lib/explicitAgentStop";
import { t as translateMessage } from "./localization";
import { createLocaleSync } from "./localeSync";
import type { SuggestionCatalog } from "./suggestions";
import "./nativeHost";
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { getReducedMotionPreference, subscribeReducedMotionPreference } from "./motionPreference";
import { createRoot } from "react-dom/client";
import {
  Bot,
  Palette,
  SlidersHorizontal,
  Activity,
  AudioLines,
  ChevronRight,
  FileText,
  House,
  Info,
  Maximize2,
  Menu,
  Mic,
  MicOff,
  Minimize2,
  Moon,
  Pause,
  PictureInPicture2,
  Settings,
  Sparkles,
  Square,
  Sun,
  Volume2,
  VolumeX,
  X,
} from "lucide-react";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { invoke, isTauri } from "@tauri-apps/api/core";
import { rpc, subscribe, compact, type Snapshot } from "./bridge";
import { Workbench } from "./Workbench";
import { GlobalSettings } from "./GlobalSettings";
import { VoiceOrb } from "./VoiceOrb";
import { useLocale, LanguagePicker } from "./i18n";
import { t } from "./localization";
import "./styles.css";
import "./motion.css";
const labels: Record<string, string> = {
  ONLINE: "Ready",
  MUTED: "Microphone muted",
  LISTENING: "Listening",
  RECOGNIZING: "Recognizing",
  THINKING: "Thinking",
  PROCESSING: "Executing",
  SYNTHESIZING: "Preparing speech",
  SPEAKING: "Speaking",
  PAUSED: "Speech paused",
  STOPPING: "Stopping speech",
  CONFIRMATION: "Waiting for confirmation",
  OFFLINE: "Handler error",
};
function App() {
  const locale = useLocale();
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null),
    [error, setError] = useState<DisplayError>(""),
    [liveSpeechStatus, setLiveSpeechStatus] = useState<DisplayError | null>(null),
    [tab, setTab] = useState(""),
    [hints, setHints] = useState<[string, string][]>([]);
  useEffect(() => {
    const title = t("Jarvis — voice assistant");
    document.title = title;
    if (isTauri()) void getCurrentWindow().setTitle(title).catch(console.error);
  }, [locale]);
  const [view, setView] = useState<"chat" | "files">("chat"),
    [navOpen, setNavOpen] = useState(false);
  const [theme, setTheme] = useState(
    () => {
      const saved = localStorage.getItem("terax-ui-theme-shadow") || localStorage.getItem("jarvis.theme");
      return saved === "system" ? (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light") : saved === "light" ? "light" : "dark";
    },
  );
  const reduced = useSyncExternalStore(subscribeReducedMotionPreference, getReducedMotionPreference, getReducedMotionPreference);
  const [systemReduced, setSystemReduced] = useState(() => matchMedia("(prefers-reduced-motion: reduce)").matches);
  const effectiveReduced = reduced || systemReduced;
  const [fullScreen, setFullScreen] = useState(false);
  const level = useRef(0),
    lastLevel = useRef(0);
  const workspaceVisible = view === "files",
    state = snapshot?.state || "INITIALISING";
  const orbOnly = new URLSearchParams(location.search).has("orb");
  const [syncLocale] = useState(() => createLocaleSync(selected => rpc("settings.locale", { locale: selected })));
  useEffect(() => {
    if (orbOnly) return;
    let active = true;
    void syncLocale(locale).catch((e) => {
      if (active) setError(displayError(e));
    });
    return () => { active = false; };
  }, [locale, orbOnly, syncLocale]);
  const run = async <T,>(
    method: string,
    params: unknown = {},
    rethrow = false,
  ): Promise<T | undefined> => {
    setError("");
    try {
      return await rpc<T>(method, params);
    } catch (e) {
      setError(displayError(e));
      if (rethrow) throw e;
    }
  };
  const refresh = async () => {
    const value = await run<Snapshot>("snapshot");
    if (value) setSnapshot(value);
  };
  const control = (action: string) => action === "stop"
    ? explicitAgentStop(() => {}, () => run("control", { action }))
    : run("control", { action });
  const requestTab = useCallback((section: string) => {
    setNavOpen(false);
    if (section === "Workspace" || section === "Files & Tools") {
      setView("files");
      setTab("");
      return;
    }
    if (section === "Home" || section === "Chat") {
      setView("chat");
      setTab("");
      return;
    }
    setTab(section);
  }, []);
  const quitSnapshot = useRef(snapshot);
  quitSnapshot.current = snapshot;
  useEffect(() => {
    if (orbOnly) return;
    const host = (window as unknown as { __JARVIS_NATIVE__: {
      quit: { register(owner: Window, role: "shell", guard: () => Promise<boolean>, validate?: () => Promise<boolean>): () => void };
      confirm(owner: Window, message: string): Promise<boolean>;
    } }).__JARVIS_NATIVE__;
    const isBusy = () => { const snapshot = quitSnapshot.current; return !snapshot || ["THINKING", "PROCESSING", "CONFIRMATION", "RECOGNIZING", "SYNTHESIZING", "SPEAKING", "PAUSED", "STOPPING"].includes(snapshot.state); };
    let confirmedBusy = false;
    return host.quit.register(window, "shell", async () => {
      const snapshot = quitSnapshot.current;
      if (!snapshot) throw new Error("Assistant state is unavailable");
      confirmedBusy = isBusy();
      return !confirmedBusy ||
        host.confirm(window, t("An assistant task is still running. Quit Jarvis and stop it?"));
    }, async () => !!quitSnapshot.current && (confirmedBusy || !isBusy()));
  }, [orbOnly]);
  useEffect(() => {
    const reveal = () => { setView("files"); setTab(""); setNavOpen(false); };
    window.addEventListener("jarvis-reveal-quit", reveal);
    return () => window.removeEventListener("jarvis-reveal-quit", reveal);
  }, []);
  const focusChat = (text?: string) => {
    setView("chat");
    window.dispatchEvent(
      new CustomEvent("jarvis-focus-chat", { detail: text }),
    );
  };
  const changeFullscreen = async () => {
    try {
      setFullScreen(
        await invoke<boolean>("fullscreen", { enabled: !fullScreen }),
      );
    } catch (e) {
      setError(displayError(e));
    }
  };
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("jarvis.theme", theme);
  }, [theme]);
  useEffect(() => {
    document.documentElement.dataset.reducedMotion = String(effectiveReduced);
  }, [effectiveReduced]);
  useEffect(() => {
    const media = matchMedia("(prefers-reduced-motion: reduce)");
    const sync = () => setSystemReduced(media.matches);
    sync();
    media.addEventListener("change", sync);
    return () => media.removeEventListener("change", sync);
  }, []);
  useEffect(() => {
    document.body.dataset.navOpen = String(navOpen);
  }, [navOpen]);
  useEffect(() => {
    let alive = true,
      off = () => {};
    subscribe((event) => {
      if (!alive) return;
      const data = event.data;
      if (event.event === "state")
        setSnapshot((old) => (old ? { ...old, ...data } : old));
      if (event.event === "speech.status")
        setLiveSpeechStatus(displayError(data.text));
      if (event.event === "audio.level") {
        level.current = data.level;
        lastLevel.current = performance.now();
      }
      if (event.event === "backend.error") setError(displayError(data.text));
      if (event.event === "camera.frame")
        window.dispatchEvent(
          new CustomEvent("camera-frame", { detail: data.image }),
        );
      if (event.event === "suggestions.changed") {
        setHints(data.items);
        window.dispatchEvent(new Event("jarvis-suggestions"));
      }
      if (
        event.event === "result" &&
        data.result.setup_kind?.startsWith("chrome")
      )
        setTab("Browser");
    })
      .then(async (un) => {
        off = un;
        await refresh();
        const saved = await run<SuggestionCatalog>("suggestions.catalog");
        if (saved && alive) setHints(saved.items);
      })
      .catch((e) => setError(displayError(e)));
    const timer = setInterval(() => {
      if (performance.now() - lastLevel.current > 350) level.current = 0;
    }, 100);
    return () => {
      alive = false;
      off();
      clearInterval(timer);
    };
  }, []);
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.isComposing) return;
      if (e.ctrlKey && e.key.toLowerCase() === "k") {
        e.preventDefault();
        if (tab) document.querySelector<HTMLInputElement>('.settings-search input')?.focus();
        else focusChat();
      }
      if (e.key === "F11" && !orbOnly) {
        e.preventDefault();
        void changeFullscreen();
      }
      if (e.key === "Escape") {
        if (tab) setTab("");
        else if (navOpen) setNavOpen(false);
        else if (fullScreen) void changeFullscreen();
      }
    };
    const fullscreen = () => void changeFullscreen();
    const forwarded = (event: Event) => key((event as CustomEvent<KeyboardEvent>).detail);
    const workspace = () => { setTab(""); setView("files"); };
    window.addEventListener("jarvis-shell-key", forwarded);
    window.addEventListener("jarvis-open-workspace", workspace);
    window.addEventListener("jarvis-fullscreen", fullscreen);
    window.addEventListener("keydown", key);
    return () => {
      window.removeEventListener("keydown", key);
      window.removeEventListener("jarvis-fullscreen", fullscreen);
      window.removeEventListener("jarvis-shell-key", forwarded);
      window.removeEventListener("jarvis-open-workspace", workspace);
    };
  }, [tab, navOpen, fullScreen]);
  const close = () => {
    if (isTauri()) void getCurrentWindow().close();
  };
  if (orbOnly)
    return (
      <main className="compact">
        <button
          className="icon close-orb"
          onClick={close}
          aria-label={t("Close Compact Window")}
        >
          <X />
        </button>
        <VoiceOrb level={level} state={state} reduced={effectiveReduced} />
        <p role="status">{t(labels[state] || "Connecting…")}</p>
        <div className="compact-actions">
          <button disabled={!snapshot} aria-label={t(snapshot?.muted ? "Microphone Off" : "Microphone On")} aria-pressed={snapshot ? !snapshot.muted : false} onClick={() => control("mute")}>
            <Mic aria-hidden="true" />
          </button>
          <button aria-label={t("Pause or Resume")} onClick={() => control("toggle_pause")}>
            <Pause aria-hidden="true" />
          </button>
          <button aria-label={t("Stop")} onClick={() => control("stop")}>
            <Square aria-hidden="true" />
          </button>
        </div>
      </main>
    );
  const speechStatus = errorText(liveSpeechStatus ?? snapshot?.speech_status ?? "");
  const connectionLabel = t(snapshot ? "AI on demand" : "Checking connection");
  return (
    <main className="shell ecosystem-shell">
      <header className="topbar">
        <div className="brand">
          <button
            className="icon nav-toggle"
            onClick={() => setNavOpen(!navOpen)}
            aria-label={t("Open Navigation")}
          >
            <Menu />
          </button>
          <span className="brand-orb" />
          <strong>{translateMessage("JARVIS")}</strong>
          <small>{t("voice assistant")}</small>
        </div>
        <div className="connection" role="status" aria-label={connectionLabel}
          title={snapshot ? t("Model connectivity is checked when you send a request.") : undefined}>
          <span aria-hidden="true" />
          <span className="connection-label">{connectionLabel}</span>
        </div>
        <button
          className="icon"
          onClick={() => {
            setTheme(theme === "dark" ? "light" : "dark");
            window.dispatchEvent(
              new CustomEvent("jarvis-theme-mode", {
                detail: theme === "dark" ? "light" : "dark",
              }),
            );
          }}
          aria-label={t("Toggle Light and Dark Theme")}
        >
          {theme === "dark" ? <Sun /> : <Moon />}
        </button>
        <button
          className="icon"
          onClick={() => void changeFullscreen()}
          aria-label={t(fullScreen ? "Exit Full Screen" : "Full Screen")}
        >
          {fullScreen ? <Minimize2 /> : <Maximize2 />}
        </button>
        <button
          className="icon"
          onClick={() => compact().catch((e) => setError(displayError(e)))}
          aria-label={t("Compact Window")}
        >
          <PictureInPicture2 />
        </button>
      </header>
      {navOpen && (
        <button
          className="nav-backdrop"
          aria-label={t("Close Navigation")}
          onClick={() => setNavOpen(false)}
        />
      )}
      <aside
        className={"sidebar glass" + (navOpen ? " expanded" : "")}
        id="primary-navigation"
      >
        <div className="eyebrow">{translateMessage("JARVIS")}</div>
        <nav aria-label={t("Main Navigation")}>
          {[
            ["Chat", House],
            ["Files & Tools", FileText],
            ["Models", SlidersHorizontal],
            ["Agents", Bot],
            ["Themes", Palette],
            ["Settings", Settings],
          ].map(([name, Icon]) => {
            const Label = Icon as typeof House;
            return (
              <button
                key={String(name)}
                className={
                  String(name) ===
                  (tab
                    ? ["Models", "Agents", "Themes"].includes(tab)
                      ? tab
                      : "Settings"
                    : workspaceVisible
                      ? "Files & Tools"
                      : "Chat")
                    ? "active"
                    : ""
                }
                onClick={() =>
                  name === "Settings"
                    ? requestTab("General")
                    : requestTab(String(name))
                }
              >
                <Label />
                {t(String(name))}
                <ChevronRight size={14} />
              </button>
            );
          })}
        </nav>
        <div className="sidebar-bottom">
          <LanguagePicker />
          <button onClick={() => requestTab("Features")}>
            <Info aria-hidden="true" />
            <span>{t("All Features")}</span>
          </button>
        </div>
      </aside>
      <section
        className="workspace ecosystem-content"
        aria-label={t(workspaceVisible ? "Files & Tools" : "Assistant Chat")}
      >
        {!workspaceVisible && (
          <>
            <div className="hero">
              <div className="status-cards left-cards">
                <button
                  className="status-card glass"
                  disabled={!snapshot}
                  aria-pressed={snapshot ? !snapshot.muted : false}
                  onClick={() => control("mute")}
                >
                  <span className="card-icon mint">
                    {snapshot?.muted ? <MicOff /> : <Mic />}
                  </span>
                  <span>
                    <strong>{t("Microphone")}</strong>
                    <small>{snapshot?.muted ? t("Off") : t("On · F4")}</small>
                  </span>
                  <i className={snapshot?.muted ? "" : "online-dot"} />
                </button>
                <button
                  className="status-card glass"
                  onClick={() => requestTab("Models")}
                >
                  <span className="card-icon blue">
                    <Sparkles />
                  </span>
                  <span>
                    <strong>{t("AI Model")}</strong>
                    <small>
                      {snapshot?.active_model ||
                        snapshot?.model ||
                        t("Preparing…")}
                    </small>
                  </span>
                  <ChevronRight />
                </button>
              </div>
              <div className="orb-zone">
                {!workspaceVisible && (
                  <VoiceOrb level={level} state={state} reduced={effectiveReduced} />
                )}
                <div
                  className="state-pill glass"
                  role="status"
                  aria-live="polite"
                >
                  <AudioLines />
                  {t(labels[state] || "Starting up")}
                  <span className="state-dot" />
                </div>
              </div>
              <div className="status-cards right-cards">
                <button
                  className="status-card glass"
                  onClick={() => requestTab("Speech & Voice")}
                >
                  <span className="card-icon lilac">
                    <Volume2 />
                  </span>
                  <span>
                    <strong>{t("Speech")}</strong>
                    <small>
                      {snapshot?.silent
                        ? t("Text Only")
                        : snapshot?.voice_label ? t(snapshot.voice_label) : t("Preparing…")}
                    </small>
                  </span>
                  <ChevronRight />
                </button>
                <button
                  className="status-card glass"
                  disabled={!snapshot}
                  aria-pressed={snapshot?.wake ?? false}
                  onClick={() => control("wake")}
                >
                  <span className="card-icon peach">
                    <Activity />
                  </span>
                  <span>
                    <strong>{t("Activation")}</strong>
                    <small>
                      {snapshot?.wake
                        ? t('By saying "Jarvis"')
                        : t("Free Commands")}
                    </small>
                  </span>
                  <ChevronRight />
                </button>
              </div>
            </div>
          </>
        )}
        <div className="speech-backend-status" role="status" aria-live="polite" title={speechStatus}>{speechStatus}</div>
        {error && (
          <div className="error-banner" role="alert">
            <span>{errorText(error)}</span>
            <button
              className="icon"
              onClick={() => setError("")}
              aria-label={t("Close Error Message")}
            >
              <X />
            </button>
          </div>
        )}
        <Workbench
          visible={workspaceVisible}
          onSettings={requestTab}
          onMode={setTheme}
        />
      </section>
      <footer className="footer glass">
        <div className="playback-buttons">
          <button onClick={() => control("toggle_pause")} aria-keyshortcuts="F7">
            <Pause aria-hidden="true" />
            <span>{t("Pause")}</span><kbd>{translateMessage(" F7")}</kbd>
          </button>
          <button onClick={() => control("speech_stop")} aria-keyshortcuts="F9">
            <Square aria-hidden="true" />
            <span>{t("Stop Speech")}</span><kbd>{translateMessage(" F9")}</kbd>
          </button>
        </div>
        <div className="voice-toggles">
          <button disabled={!snapshot} aria-pressed={snapshot ? !snapshot.muted : false} onClick={() => control("mute")}>
            {snapshot?.muted ? <MicOff /> : <Mic />}
            {t(snapshot?.muted ? "Microphone Off" : "Microphone On")}
          </button>
          <button disabled={!snapshot} aria-pressed={snapshot ? !snapshot.silent : false} onClick={() => control("voice")}>
            {snapshot?.silent ? <VolumeX /> : <Volume2 />}
            {t(snapshot?.silent ? "Text Only" : "With Voice")}
          </button>
        </div>
        <span className="keyboard-tip">
          {t("Ctrl + K - Type · Enter - Send")}
        </span>
      </footer>
      {tab && (
        <GlobalSettings
          section={tab}
          select={requestTab}
          close={() => setTab("")}
          snapshot={snapshot}
          hints={hints}
          setHints={setHints}
          run={run}
          refresh={refresh}
          onMode={setTheme}
        />
      )}
    </main>
  );
}
createRoot(document.getElementById("root")!).render(<App />);
