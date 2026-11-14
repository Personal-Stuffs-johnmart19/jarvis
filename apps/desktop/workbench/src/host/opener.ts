import * as native from "@tauri-apps/plugin-opener";
import { nativePlugin } from "./plugin";
export const openUrl: typeof native.openUrl = (...args) => nativePlugin("opener", native).openUrl(...args);
export const revealItemInDir: typeof native.revealItemInDir = (...args) => nativePlugin("opener", native).revealItemInDir(...args);
