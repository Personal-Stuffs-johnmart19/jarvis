import * as native from "@tauri-apps/plugin-clipboard-manager";
import { nativePlugin } from "./plugin";
export const readText: typeof native.readText = (...args) => nativePlugin("clipboard", native).readText(...args);
export const writeText: typeof native.writeText = (...args) => nativePlugin("clipboard", native).writeText(...args);
