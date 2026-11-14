import * as native from "@tauri-apps/plugin-notification";
import { nativePlugin } from "./plugin";
export const isPermissionGranted: typeof native.isPermissionGranted = (...args) => nativePlugin("notification", native).isPermissionGranted(...args);
export const requestPermission: typeof native.requestPermission = (...args) => nativePlugin("notification", native).requestPermission(...args);
