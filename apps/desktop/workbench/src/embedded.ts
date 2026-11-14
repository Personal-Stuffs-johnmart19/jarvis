import "../../src/motion.css";
import { mirrorMotionPreference } from "../../src/motionPreference";
// All native callbacks belong to the single trusted Jarvis host document.
if (window.parent === window || window.parent.location.origin !== location.origin) throw new Error("Workbench requires the local Jarvis host");
const stopMotionSync = mirrorMotionPreference(window.parent.document.documentElement, document.documentElement);
window.addEventListener("pagehide", stopMotionSync, { once: true });
window.addEventListener("keydown", event => {
  if (event.defaultPrevented || event.isComposing) return;
  const host = window.parent as unknown as { __JARVIS_NATIVE__?: { shortcuts?: { dispatch(event: KeyboardEvent, source: Window): void } } };
  host.__JARVIS_NATIVE__?.shortcuts?.dispatch(event, window);
});
export {};
