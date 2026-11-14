import {
  isPermissionGranted,
  requestPermission,
} from "@/host/notification";
import { t } from "../../../../../src/localization";
import { invoke } from "@/host/core";
import { playAgentNotificationSound } from "./sound";

export type OsNotificationResult = "requested" | "denied" | "failed";

let granted = false;

async function ensurePermission(): Promise<boolean> {
  // Cache only the positive result so an OS-level settings change can be
  // observed on a later attempt after a denied check.
  if (granted) return true;
  let ok = await isPermissionGranted();
  if (!ok) ok = (await requestPermission()) === "granted";
  granted = ok;
  return ok;
}

export async function osNotify(
  title: string,
  body: string,
): Promise<OsNotificationResult> {
  try {
    if (!(await ensurePermission())) return "denied";
    await invoke("plugin:notification|notify", {options:{title,body}});
    return "requested";
  } catch (e) {
    console.warn("[terax] os notification failed:", e);
    return "failed";
  }
}

export async function testAgentOsNotification(
  withSound = true,
): Promise<OsNotificationResult> {
  const result = await osNotify(
    t("Jarvis notifications are working"),
    t("You will be notified when an agent needs your attention."),
  );
  if (result === "requested" && withSound) playAgentNotificationSound();
  return result;
}
