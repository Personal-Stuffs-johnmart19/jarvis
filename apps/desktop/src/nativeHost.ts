import { createQuitCoordinator, type QuitGuard, type QuitRole } from "./quitCoordinator";
import { t } from "./localization";
import { createStartupService } from "../workbench/src/settings/startupController";
import { loadPreferences, onPreferencesChange, setAutostart } from "../workbench/src/modules/settings/store";
import { createZoomController } from "./nativeZoom";
import { createAppConfirmation } from "./appConfirmation";
import { createShortcutRouter } from "./shortcutRouter";
import { createExplicitStopRegistry } from "../workbench/src/modules/ai/lib/explicitAgentStop";
import * as core from "@tauri-apps/api/core";
import * as path from "@tauri-apps/api/path";
import * as clipboard from "@tauri-apps/plugin-clipboard-manager";
import * as event from "@tauri-apps/api/event";
import * as nativeWindow from "@tauri-apps/api/window";
import * as webviewWindow from "@tauri-apps/api/webviewWindow";
import * as webview from "@tauri-apps/api/webview";
import * as store from "@tauri-apps/plugin-store";
import * as autostart from "@tauri-apps/plugin-autostart";
import * as notification from "@tauri-apps/plugin-notification";
import * as opener from "@tauri-apps/plugin-opener";
import * as os from "@tauri-apps/plugin-os";

import { lease, owned } from "./nativeLeases";

const allowedShortcutOwner = (owner: Window) => {
  try {
    return owner.location.origin === location.origin && (owner === window ||
      Array.from(document.querySelectorAll<HTMLIFrameElement>('iframe[src="workbench.html"], iframe[src^="workbench-settings.html?"]'))
        .some(frame => frame.contentWindow === owner));
  } catch { return false; }
};
const shortcuts = createShortcutRouter(allowedShortcutOwner);
const explicitStops = createExplicitStopRegistry(allowedShortcutOwner);
const confirmationOpen = () => document.querySelector("dialog.jarvis-confirmation[open]") !== null;
window.addEventListener("keydown", event => { if (!confirmationOpen()) shortcuts.dispatch(event, window); }, { capture: true });
const shortcutBridge = {
  register: shortcuts.register,
  dispatch(event: KeyboardEvent, source: Window) {
    if (!allowedShortcutOwner(source) || confirmationOpen()) return;
    shortcuts.dispatch(event, source);
    if (!event.defaultPrevented) window.dispatchEvent(new CustomEvent("jarvis-shell-key", { detail: event }));
  },
};
const confirm = createAppConfirmation(allowedShortcutOwner, t);
const quit = createQuitCoordinator({
  async reveal() {
    await core.invoke("reveal_quit_window");
    window.dispatchEvent(new Event("jarvis-reveal-quit"));
  },
  snapshotDownloads: () => core.invoke<number[]>("model_download_quit_snapshot"),
  confirmDownloads: () => confirm(window, t("Model downloads are still running. Quit Jarvis and interrupt them? Partial downloads can resume later.")),
  complete: (id, approved, downloads) => core.invoke("complete_quit", { id, approved, downloads }),
  failed: error => { console.error("Jarvis quit was cancelled", error); void confirm(window, t("Could not safely quit Jarvis. Please try again.")); },
});
void event.listen<number>("jarvis:quit-requested", event => void quit.request(event.payload)).catch(console.error);
const quitBridge = {
  register(owner: Window, role: QuitRole, guard: QuitGuard, validate?: QuitGuard) {
    if (!allowedShortcutOwner(owner) || (role === "shell" ? owner !== window : owner === window)) return () => {};
    let abandon: (() => void) | undefined;
    const unregister = quit.register(role, () => Promise.race([
      Promise.resolve().then(guard),
      new Promise<boolean>(resolve => { abandon = () => resolve(false); }),
    ]), validate);
    const dispose = () => { unregister(); abandon?.(); owner.removeEventListener("pagehide", dispose); };
    owner.addEventListener("pagehide", dispose, { once: true });
    return dispose;
  },
};
const nativePlugins = { autostart, notification, opener, os, clipboard };
const startup = createStartupService({
  read: () => nativePlugins.autostart.isEnabled(),
  change: next => next ? nativePlugins.autostart.enable() : nativePlugins.autostart.disable(),
  persist: setAutostart,
});
Object.defineProperty(globalThis.window, "__JARVIS_NATIVE__", {
  value: {
    shortcuts: shortcutBridge,
    explicitStops,
    confirm,
    quit: quitBridge,
    core,
    path,
    event,
    window: nativeWindow,
    webviewWindow,
    webview,
    store,
    plugins: nativePlugins,
    startup,
    lease,
    owned,
  },
});

// One native zoom owns the entire main webview, including both embedded pages.
const zoom = createZoomController(value => webview.getCurrentWebview().setZoom(value));
void (async () => {
  let changed = false;
  const unlisten = await onPreferencesChange((key, value) => {
    if (key === "zoomLevel") { changed = true; void zoom(value).catch(console.error); }
  });
  window.addEventListener("pagehide", unlisten, { once: true });
  const preferences = await loadPreferences();
  if (!changed) await zoom(preferences.zoomLevel);
})().catch(error => console.error("Jarvis UI zoom failed", error));
