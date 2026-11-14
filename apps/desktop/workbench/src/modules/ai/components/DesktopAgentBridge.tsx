import { useHelperTextBridge } from "./useHelperTextBridge";
import { QueuedTaskApprovals } from "./QueuedTaskApprovals";
import { t } from "../../../../../src/localization";
import { assistantReply } from "../lib/assistantReply";
import { projectConversation } from "../lib/conversationProjection";
import { revokePendingActions } from "../tools/jarvis";
import { useChatStore } from "../store/chatStore";
import { usePreferencesStore } from "@/modules/settings/preferences";
import { useChat, type UIMessage } from "@ai-sdk/react";
import { useEffect, useRef, useMemo } from "react";
import { invoke } from "@/host/core";
import { listen } from "@/host/event";
import type { Chat } from "@ai-sdk/react";
import { currentWorkspaceScopeKey, workspaceScopeKey, useWorkspaceEnvStore } from "@/modules/workspace";
import { queuedWorkspaceFingerprint } from "../lib/queuedTaskApprovals";

const rpc = (method: string, params: object = {}) =>
  invoke("backend", { method, params });
const currentQueueWorkspace = () => {
  const live=useChatStore.getState().live;
  return queuedWorkspaceFingerprint(currentWorkspaceScopeKey(),live.getCwd(),live.getWorkspaceRoot());
};
export function DesktopAgentBridge({ chat }: { chat: Chat<UIMessage> }) {
  const owner = useMemo(() => crypto.randomUUID(), [chat]);
  const ready = useRef(false);
  const workspaceSent = useRef<string|null|undefined>(undefined);
  const workspaceScope = useWorkspaceEnvStore(s=>workspaceScopeKey(s.env));
  const modelId = useChatStore((s) => s.selectedModelId);
  const localModel = usePreferencesStore((s) => s.lmstudioModelId);
  const model = modelId === "lmstudio-local" ? localModel : modelId;
  const { status, messages } = useChat<UIMessage>({ chat });
  const route = useHelperTextBridge(owner, chat.id, status);
  const attachment = useRef({ model, route });
  attachment.current = { model, route };
  const approval = messages.some(
    (m) =>
      m.role === "assistant" &&
      m.parts.some(
        (p) => "state" in p && p.state === "approval-requested",
      ),
  );
  useEffect(() => {
    void rpc("agent.activity", {
      owner: owner,
      busy: status === "submitted" || status === "streaming" || approval,
      approval,
    }).catch(console.error);
  }, [status, approval]);
  const mirror = useRef({
    payload: "[]",
    sent: "",
    times: new Map<string, number>(),
  });
  useEffect(() => {
    const state = mirror.current;
    const session = useChatStore
      .getState()
      .sessions.find((s) => s.id === chat.id);
    const initial = state.times.size === 0;
    const projected = projectConversation(messages, (id, index) => {
      let time = state.times.get(id);
      if (time === undefined) {
        time = initial
          ? ((session?.createdAt ?? Date.now()) + index) / 1000
          : Date.now() / 1000;
        state.times.set(id, time);
      }
      return time;
    });
    if (state.times.size > 2000) {
      const keep = new Set(projected.map((m) => m.id));
      for (const id of state.times.keys())
        if (!keep.has(id)) state.times.delete(id);
    }
    state.payload = JSON.stringify(projected);
  }, [messages, chat]);
  useEffect(() => {
    let disposed = false;
    const flush = () => {
      const state = mirror.current;
      if (disposed || !ready.current) return;
      const workspace=currentQueueWorkspace();
      if(workspace!==workspaceSent.current){
        workspaceSent.current=workspace;
        void rpc("agent.attach",{...attachment.current,session:chat.id,owner,workspace}).catch(error=>{workspaceSent.current=undefined;console.error(error);});
      }
      if(state.payload === state.sent)return;
      const payload = state.payload;
      state.sent = payload;
      void rpc("conversation.sync", {
        owner: owner,
        session: chat.id,
        items: JSON.parse(payload),
      }).catch((error) => {
        if (!disposed) state.sent = "";
        console.error(error);
      });
    };
    flush();
    const timer = window.setInterval(flush, 250);
    return () => {
      disposed = true;
      clearInterval(timer);
      mirror.current.sent = "";
      mirror.current.times.clear();
    };
  }, [chat]);
  const pending = useRef<{ id: string; start: number; sent: boolean } | null>(
    null,
  );
  useEffect(() => {
    let disposed = false;
    const subscriptions: (() => void)[] = [];
    void (async () => {
      const off = await listen<{
        event: string;
        data: { id: string; text: string };
      }>("backend-event", ({ payload }) => {
        if (disposed) return;
        if (payload.event === "agent.stop") {
          pending.current = null;
          revokePendingActions();
          void chat.stop();
          return;
        }
        if (
          payload.event === "agent.cancel" &&
          pending.current?.id === payload.data.id
        ) {
          pending.current = null;
          void chat.stop();
        }
        if (payload.event === "history.clear") {
          void chat.stop();
          chat.messages = [];
          useChatStore.getState().resetAgentMeta();
          return;
        }
        if (payload.event !== "agent.request") return;
        const { id, text } = payload.data;
        if (
          pending.current ||
          chat.status === "submitted" ||
          chat.status === "streaming"
        ) {
          void rpc("agent.complete", {
            id,
            error:
              "The shared agent is busy. Finish or stop the current task first.",
          });
          return;
        }
        pending.current = { id, start: chat.messages.length, sent: false };
        void chat
          .sendMessage({ text })
          .then(() => {
            if (pending.current?.id === id) pending.current.sent = true;
          })
          .catch((error) => {
            if (pending.current?.id !== id) return;
            pending.current = null;
            void rpc("agent.complete", { id, error: String(error) });
          });
      });
      if (disposed) {
        off();
        return;
      }
      subscriptions.push(off);
      const workspace=currentQueueWorkspace();
      await rpc("agent.attach", { model: attachment.current.model, session: chat.id, owner, route: attachment.current.route,workspace });
      workspaceSent.current=workspace;
      if (!disposed) ready.current = true;
    })().catch(console.error);
    return () => {
      disposed = true;
      ready.current = false;
      subscriptions.forEach((off) => off());
      revokePendingActions();
      void rpc("agent.detach", { owner: owner }).catch(() => {});
      const request = pending.current;
      pending.current = null;
      if (request) {
        void chat.stop();
        void rpc("agent.complete", {
          id: request.id,
          error: "Agent session was closed or changed",
        }).catch(() => {});
      }
    };
  }, [chat]);
  useEffect(() => {
    const workspace=currentQueueWorkspace();
    workspaceSent.current=workspace;
    void rpc("agent.attach", { model, session: chat.id, owner: owner, route,workspace }).catch(error=>{workspaceSent.current=undefined;console.error(error);});
  }, [model, chat, owner, route,workspaceScope]);
  // UI-origin turns use the same speech service as voice-origin turns. A lease
  // prevents replay after F9, a session switch, or an old restored conversation.
  const uiRun = useRef<{id:string; start:number; begin:Promise<unknown>} | null>(null);
  useEffect(() => {
    if ((status === "submitted" || status === "streaming") && !pending.current && !uiRun.current) {
      const id = crypto.randomUUID();
      uiRun.current = {id, start:Math.max(0, chat.messages.map(m=>m.role).lastIndexOf("user")), begin:rpc("agent.ui_speech", {owner,session:chat.id,run:id,phase:"begin"}).catch(console.error)};
    }
    const run = uiRun.current;
    if (!run || status === "submitted" || status === "streaming" || approval) return;
    uiRun.current = null;
    const text = status === "error" ? "" : assistantReply(messages.slice(run.start)).slice(0,12000);
    void run.begin.then(()=>rpc("agent.ui_speech", {owner,session:chat.id,run:run.id,phase:"end",text})).catch(console.error);
  }, [status, messages, approval, chat, owner]);
  useEffect(() => {
    const request = pending.current;
    if (!request || status === "submitted" || status === "streaming") return;
    const fresh = messages
      .slice(request.start)
      .filter((m) => m.role === "assistant");
    if (!fresh.length && status !== "error") return;
    const approval = fresh.some((m) =>
      m.parts.some(
        (p) => "state" in p && p.state === "approval-requested",
      ),
    );
    if (approval) {
      return;
    }
    pending.current = null;
    // A tool-only turn still has an outcome; never repeat side effects for missing prose.
    const text =
      assistantReply(fresh) ||
      t(
        "The model completed the request without a text response. Details are available in the workspace.",
      );
    void rpc("agent.complete", {
      id: request.id,
      text,
      ...(status === "error"
        ? { error: chat.error?.message || "Agent request failed" }
        : {}),
    }).catch(console.error);
  }, [status, messages, chat]);
  return <QueuedTaskApprovals key={owner} owner={owner} session={chat.id} route={route} />;
}
