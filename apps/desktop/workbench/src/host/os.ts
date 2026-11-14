import * as native from "@tauri-apps/plugin-os";
import { nativePlugin } from "./plugin";
export const platform: typeof native.platform = (...args) => nativePlugin("os", native).platform(...args);
export const hostname: typeof native.hostname = (...args) => nativePlugin("os", native).hostname(...args);
