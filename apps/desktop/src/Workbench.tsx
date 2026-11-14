import { useEffect, useRef } from "react";
import { listen } from "@tauri-apps/api/event";
import { t } from "./localization";
import "./workbench.css";
export const workbenchTabs: Record<string, string> = {
  General: "general",
  Themes: "themes",
  Models: "models",
  Integrations: "integrations",
  Agents: "agents",
  Shortcuts: "shortcuts",
  Editor: "editor",
  About: "about",
};
const colorMap: Record<string, string> = {
  foreground: "--text",
  mutedForeground: "--muted",
  card: "--surface",
  border: "--line",
  primary: "--accent",
  input: "--field",
  background: "--workbench-background",
  secondary: "--button",
};
export function applyWorkbenchTheme(colors: Record<string, string>) {
  for (const [key, variable] of Object.entries(colorMap))
    if (typeof colors[key] === "string" && CSS.supports("color", colors[key]))
      document.documentElement.style.setProperty(variable, colors[key]);
}
export function Workbench({
  visible,
  onSettings,
  onMode,
}: {
  visible: boolean;
  onSettings: (value: string) => void;
  onMode: (value: string) => void;
}) {
  const frame = useRef<HTMLIFrameElement>(null);
  const send = () =>
    frame.current?.contentWindow?.postMessage(
      { type: "jarvis:surface", surface: visible ? "files" : "chat" },
      location.origin,
    );
  useEffect(send, [visible]);
  useEffect(() => {
    const receive = (event: MessageEvent) => {
      if (
        event.origin !== location.origin ||
        event.source !== frame.current?.contentWindow
      )
        return;
      if (event.data?.type === "jarvis:surface-ready") send();
      if (event.data?.type === "jarvis:fullscreen")
        window.dispatchEvent(new Event("jarvis-fullscreen"));
      if (event.data?.type === "jarvis:focus-main") {
        onSettings("Chat");
        frame.current?.contentWindow?.postMessage(
          { type: "jarvis:focus-chat" },
          location.origin,
        );
      }

      if (event.data?.type === "jarvis:settings")
        onSettings(
          Object.keys(workbenchTabs).find(
            (k) => workbenchTabs[k] === event.data.tab,
          ) || "General",
        );
      if (event.data?.type === "jarvis:theme" && event.data.colors) {
        applyWorkbenchTheme(event.data.colors);
        if (["dark", "light"].includes(event.data.mode))
          onMode(event.data.mode);
        localStorage.setItem(
          "jarvis.workbench.palette",
          JSON.stringify(event.data.colors),
        );
      }
    };
    const focus = (event: Event) =>
      frame.current?.contentWindow?.postMessage(
        {
          type: "jarvis:focus-chat",
          text: (event as CustomEvent<string>).detail,
        },
        location.origin,
      );
    const theme = (event: Event) =>
      frame.current?.contentWindow?.postMessage(
        {
          type: "jarvis:set-theme",
          mode: (event as CustomEvent<string>).detail,
        },
        location.origin,
      );
    const hints = () =>
      frame.current?.contentWindow?.postMessage(
        { type: "jarvis:suggestions" },
        location.origin,
      );
    window.addEventListener("jarvis-theme-mode", theme);
    window.addEventListener("message", receive);
    window.addEventListener("jarvis-focus-chat", focus);
    window.addEventListener("jarvis-suggestions", hints);
    const off = listen<string>("jarvis:workbench-settings", (e) =>
      onSettings(
        Object.keys(workbenchTabs).find(
          (k) => workbenchTabs[k] === e.payload,
        ) || "General",
      ),
    );
    return () => {
      window.removeEventListener("jarvis-theme-mode", theme);
      window.removeEventListener("message", receive);
      window.removeEventListener("jarvis-focus-chat", focus);
      window.removeEventListener("jarvis-suggestions", hints);
      void off.then((un) => un());
    };
  }, [visible, onSettings, onMode]);
  return (
    <section
      className="workbench-space glass"
      aria-label={t(visible ? "Files & Tools" : "Assistant Chat")}
    >
      <iframe
        ref={frame}
        title={t("Jarvis shared chat and tools")}
        src="workbench.html"
        onLoad={send}
      />
    </section>
  );
}
