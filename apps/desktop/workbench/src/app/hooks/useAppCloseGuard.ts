import { t as translateMessage } from "../../../../src/localization";

import { confirmAction } from "../../../../src/confirmAction";
import { chats, useChatStore, flushPersist } from "@/modules/ai/store/chatStore";
import {
  flushSessionWrites,
} from "@/modules/ai/lib/sessions";

import {
  type RefObject,
  useEffect,
  useRef,
  useState,
} from "react";
import { usePreferencesStore } from "@/modules/settings/preferences";
import type { Tab } from "@/modules/tabs";
import { leafHasForegroundProcess, leafIds } from "@/modules/terminal";

async function busyTerminalLeaves(tabs: Tab[]): Promise<number[]> {
  const leaves = tabs.flatMap(tab => tab.kind === "terminal" ? leafIds(tab.paneTree) : []);
  const checks = await Promise.all(leaves.map(async leaf => {
    const busy = await leafHasForegroundProcess(leaf, true);
    return busy ? leaf : null;
  }));
  return checks.filter((id): id is number => id !== null);
}
function busyAgents(): string[] {
  const busy = [...chats].filter(([, chat]) => chat.status === "submitted" || chat.status === "streaming").map(([id]) => id);
  if (["thinking", "streaming", "awaiting-approval"].includes(useChatStore.getState().agentMeta.status)) busy.push("active-agent");
  return busy;
}
/** How long closing waits for chat history to reach the disk. */
export const CLOSE_FLUSH_TIMEOUT_MS = 4_000;

export type AppCloseBlocker = {
  dirtyEditors: number;
  busyTerminal: boolean;
  busyAgent?: boolean;
};

/**
 * The opt-out only covers running processes, so it stays hidden whenever the
 * same prompt is also the last warning before discarding unsaved buffers.
 */
export function canOptOutOfAppClosePrompt(blocker: AppCloseBlocker): boolean {
  return blocker.busyTerminal && !blocker.busyAgent && blocker.dirtyEditors === 0;
}

export function useAppCloseGuard(tabsRef: RefObject<Tab[]>) {
  const [pendingAppClose, setPendingAppClose] =
    useState<AppCloseBlocker | null>(null);
  const resolvePrompt = useRef<((approved: boolean) => void) | null>(null);
  useEffect(() => {
    const host = (window.parent as unknown as { __JARVIS_NATIVE__?: {
      quit: { register(owner: Window, role: "workspace", guard: () => Promise<boolean>, validate?: () => Promise<boolean>): () => void };
    } }).__JARVIS_NATIVE__;
    if (!host) return;
    let validate = async () => false;
    const unregister = host.quit.register(window, "workspace", async () => {
      if (!useChatStore.getState().sessionsHydrated) throw new Error("Chat history is still loading");
      const checkTerminals = usePreferencesStore.getState().confirmCloseRunningTerminal;
      const busyLeaves = checkTerminals ? await busyTerminalLeaves(tabsRef.current) : [];
      const busyTerminal = busyLeaves.length > 0;
      const agents = busyAgents();
      const busyAgent = agents.length > 0;
      const dirtyTabs = tabsRef.current.filter(tab => tab.kind === "editor" && tab.dirty);
      const dirtyEditors = dirtyTabs.length;
      const unchanged = () => tabsRef.current.filter(tab => tab.kind === "editor" && tab.dirty).every(tab => dirtyTabs.includes(tab));
      validate = async () => {
        const leaves = checkTerminals ? await busyTerminalLeaves(tabsRef.current) : [];
        return unchanged() && leaves.every(leaf => busyLeaves.includes(leaf)) && busyAgents().every(id => agents.includes(id));
      };
      if (dirtyEditors || busyTerminal || busyAgent) {
        const approved = await new Promise<boolean>(resolve => {
          resolvePrompt.current = resolve;
          setPendingAppClose({ dirtyEditors, busyTerminal, busyAgent });
        });
        if (!approved) return false;
      }
      if (!await validate()) return false;
      // Stop accepted streams before draining their final persistence writes.
      await Promise.all([...chats].filter(([id]) => agents.includes(id)).map(([, chat]) => chat.stop()));
      try {
        flushPersist();
        const flush = await flushSessionWrites({ timeoutMs: CLOSE_FLUSH_TIMEOUT_MS });
        if (!unchanged()) return false;
        if (flush.ok) return true;
      } catch (error) { console.error("Could not save chat history before quitting", error); }
      return await confirmAction(translateMessage("Could not save chat history. The latest messages may be lost. Quit anyway?")) && unchanged();
    }, () => validate());
    return () => { unregister(); resolvePrompt.current?.(false); resolvePrompt.current = null; };
  }, [tabsRef]);
  const settle = (approved: boolean) => {
    const resolve = resolvePrompt.current;
    resolvePrompt.current = null;
    setPendingAppClose(null);
    resolve?.(approved);
  };
  return { pendingAppClose, confirmAppClose: () => settle(true), cancelAppClose: () => settle(false) };
}
