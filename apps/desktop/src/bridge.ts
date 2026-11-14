import type { DisplayError } from "./displayError";
import { t } from "./localization";
import { invoke, isTauri } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
export type Message = { id: string; role: string; text: string; time: number };
export type Snapshot = {
  version: number;
  state: string;
  muted: boolean;
  silent: boolean;
  wake: boolean;
  connected: boolean;
  messages: Message[];
  stream: string;
  confirmation: boolean;
  model: string;
  active_model: string;
  models: string[];
  auto_fallback: boolean;
  voice: string;
  voice_label: string;
  speech_status?: DisplayError;
  voices: { id: string; name: string }[];
  network: { mode: string; proxy: string };
  browser: { mode: string; profile: string; engine?: string };
  key_configured: boolean;
  microphone: number | null;
};
export type BackendEvent = { event: string; data: Record<string, any> };
declare global {
  interface Window {
    __JARVIS_WORKSPACE_DIRTY__?: boolean;
    __JARVIS_RPC__?: (method: string, params: unknown) => Promise<any>;
  }
}
export async function rpc<T = any>(
  method: string,
  params: unknown = {},
): Promise<T> {
  if (isTauri()) return invoke<T>("backend", { method, params });
  if (window.__JARVIS_RPC__) return window.__JARVIS_RPC__(method, params);
  throw new Error(
    t("Launch Jarvis via Jarvis.cmd. Browser view is not connected to Python."),
  );
}
export async function subscribe(callback: (event: BackendEvent) => void) {
  if (isTauri())
    return listen<BackendEvent>("backend-event", (event) =>
      callback(event.payload),
    );
  const handler = (event: Event) => callback((event as CustomEvent).detail);
  window.addEventListener("jarvis-backend", handler);
  return () => window.removeEventListener("jarvis-backend", handler);
}
export async function compact() {
  if (isTauri()) await invoke("show_orb");
}
