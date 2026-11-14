export const reducedMotionKey = "jarvis.reduced";
const preferenceEvent = "jarvis-motion-preference";

function preferenceOwner(): Window {
  try {
    if (window.parent.location.origin === window.location.origin) return window.parent;
  } catch { /* A standalone surface keeps its own preference. */ }
  return window;
}

/** The explicit setting is independent of the effective OS preference. */
export function getReducedMotionPreference(): boolean {
  try { return preferenceOwner().localStorage.getItem(reducedMotionKey) === "true"; }
  catch { return false; }
}

export function subscribeReducedMotionPreference(changed: () => void) {
  const owner = preferenceOwner();
  const storage = (event: StorageEvent) => {
    if (event.key === reducedMotionKey || event.key === null) changed();
  };
  owner.addEventListener(preferenceEvent, changed);
  owner.addEventListener("storage", storage);
  return () => {
    owner.removeEventListener(preferenceEvent, changed);
    owner.removeEventListener("storage", storage);
  };
}

export function setReducedMotionPreference(reduced: boolean) {
  const owner = preferenceOwner();
  owner.localStorage.setItem(reducedMotionKey, String(reduced));
  owner.dispatchEvent(new Event(preferenceEvent));
}

/** Mirror the trusted host preference without remounting chat/editor/settings. */
export function mirrorMotionPreference(source: HTMLElement, target: HTMLElement) {
  const sync = () => { target.dataset.reducedMotion = source.dataset.reducedMotion ?? "false"; };
  sync();
  const observer = new MutationObserver(sync);
  observer.observe(source, { attributes: true, attributeFilter: ["data-reduced-motion"] });
  return () => observer.disconnect();
}
