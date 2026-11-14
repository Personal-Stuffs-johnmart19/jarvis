import * as native from "@tauri-apps/plugin-autostart";
import { nativePlugin } from "./plugin";
export const enable: typeof native.enable = (...args) => nativePlugin("autostart", native).enable(...args);
export const disable: typeof native.disable = (...args) => nativePlugin("autostart", native).disable(...args);
export const isEnabled: typeof native.isEnabled = (...args) => nativePlugin("autostart", native).isEnabled(...args);
