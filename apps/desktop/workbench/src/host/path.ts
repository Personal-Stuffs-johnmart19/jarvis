import * as native from "@tauri-apps/api/path";
function host(): typeof native {
  if (window.parent === window) return native;
  return (window.parent as unknown as { __JARVIS_NATIVE__: { path: typeof native } }).__JARVIS_NATIVE__.path;
}
export const homeDir: typeof native.homeDir = (...args) => host().homeDir(...args);
export const appConfigDir: typeof native.appConfigDir = (...args) => host().appConfigDir(...args);
export const join: typeof native.join = (...args) => host().join(...args);
