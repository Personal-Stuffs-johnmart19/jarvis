import { useEffect, useRef } from "react";
import { usePreferencesStore } from "@/modules/settings/preferences";
import { SHORTCUTS, type ShortcutId } from "../shortcuts";
import { resolveShortcut } from "./resolveShortcut";

type ShortcutHandler = (e: KeyboardEvent) => void;
export type ShortcutHandlers = Partial<Record<ShortcutId, ShortcutHandler>>;

export type UseGlobalShortcutsOptions = {
  isDisabled?: (id: ShortcutId, e: KeyboardEvent) => boolean;
};

export function useGlobalShortcuts(
  handlers: ShortcutHandlers,
  options?: UseGlobalShortcutsOptions,
) {
  const latest = useRef({ handlers, options });
  latest.current = { handlers, options };

  // Access the shortcuts from the store
  const userShortcuts = usePreferencesStore((s) => s.shortcuts);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.isComposing) return;
      const external = (e.target as Node | null)?.ownerDocument?.defaultView !== window;
      const { handlers, options } = latest.current;
      const id = resolveShortcut(e, {
        shortcuts: SHORTCUTS,
        user: userShortcuts,
        isDisabled: (candidate, event) => (external && (candidate.startsWith("editor.") || candidate.startsWith("terminal.") || candidate.startsWith("blocks."))) || !!options?.isDisabled?.(candidate, event),
        hasHandler: (candidate) => handlers[candidate] !== undefined,
      });
      if (id === null) return;
      e.preventDefault();
      e.stopImmediatePropagation();
      if (external && /^(tab\.|pane\.|space\.|explorer\.|commandPalette\.)/.test(id))
        window.parent.dispatchEvent(new Event("jarvis-open-workspace"));
      handlers[id]?.(e);
    };
    window.addEventListener("keydown", onKey, { capture: true });
    const host = window.parent as unknown as { __JARVIS_NATIVE__?: { shortcuts?: { register(owner: Window, handler: (event: KeyboardEvent) => void): () => void } } };
    const unregister = host.__JARVIS_NATIVE__?.shortcuts?.register(window, onKey);
    return () => {
      unregister?.();
      window.removeEventListener("keydown", onKey, { capture: true });
    };
  }, [userShortcuts]);
}
