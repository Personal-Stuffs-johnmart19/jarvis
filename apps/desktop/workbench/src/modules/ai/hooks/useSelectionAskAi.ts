import { isTerminalSurfaceTarget } from "@/modules/terminal/lib/terminalSurfaceTarget";
import { useCallback, useEffect, useState } from "react";

type Params = {
  captureActiveSelection: () => string | null;
  askFromSelection: () => void;
};

/**
 * Tracks text selections inside the terminal / editor and surfaces the
 * "Ask AI" popup at the pointer. Dismisses on any click outside the AI surface.
 */
export function useSelectionAskAi({
  captureActiveSelection,
  askFromSelection,
}: Params) {
  const [askPopup, setAskPopup] = useState<{ x: number; y: number } | null>(
    null,
  );

  useEffect(() => {
    const isInsideAi = (t: EventTarget | null) => {
      const el = t as HTMLElement | null;
      if (!el) return false;
      return !!(
        el.closest("[data-selection-ask-ai]") ||
        el.closest("[data-ai-input-bar]") ||
        el.closest("[data-ai-mini-window]")
      );
    };

    let pendingTimer: ReturnType<typeof setTimeout> | null = null;
    const clearPending = () => {
      if (pendingTimer !== null) {
        clearTimeout(pendingTimer);
        pendingTimer = null;
      }
    };
    const onDown = (e: MouseEvent) => {
      if (isInsideAi(e.target)) return;
      clearPending();
      setAskPopup(null);
    };
    const onUp = (e: MouseEvent) => {
      if (isInsideAi(e.target)) return;
      const el = e.target as HTMLElement | null;
      const inContentArea =
        isTerminalSurfaceTarget(e.target) || el?.closest?.(".cm-editor");
      if (!inContentArea) return;
      // Defer one tick so the terminal and CodeMirror finalize the selection.
      clearPending();
      pendingTimer = setTimeout(() => {
        pendingTimer = null;
        const text = captureActiveSelection();
        if (text && text.trim().length > 0) {
          setAskPopup({ x: e.clientX, y: e.clientY });
        } else {
          setAskPopup(null);
        }
      }, 0);
    };

    document.addEventListener("mousedown", onDown);
    document.addEventListener("mouseup", onUp);
    return () => {
      clearPending();
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("mouseup", onUp);
    };
  }, [captureActiveSelection]);

  const onAskFromSelection = useCallback(() => {
    askFromSelection();
    setAskPopup(null);
  }, [askFromSelection]);

  return { askPopup, setAskPopup, onAskFromSelection };
}
