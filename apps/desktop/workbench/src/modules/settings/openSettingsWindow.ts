import { invoke } from "@/host/core";

export type SettingsTab =
  | "general"
  | "editor"
  | "themes"
  | "shortcuts"
  | "models"
  | "integrations"
  | "agents"
  | "about";

export async function openSettingsWindow(tab?: SettingsTab): Promise<void> {
  if (window.parent !== window) {
    window.parent.postMessage({type:"jarvis:settings",tab:tab??"general"},location.origin);
  } else await invoke("open_settings_window", {tab:tab??null});
}
