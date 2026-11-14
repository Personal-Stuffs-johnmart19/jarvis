import { useLocale } from "../../../../../src/i18n";
import { localizeSuggestions, type SuggestionCatalog } from "../../../../../src/suggestions";
import "../../../ecosystem.css";
import {
  MessageSquare,
  Settings2,
  Trash2,
  Send,
  Square,
  Sparkles,
} from "lucide-react";
import { useChat, type UIMessage } from "@ai-sdk/react";
import { useEffect, useMemo, useState } from "react";
import { invoke } from "@/host/core";
import { t } from "../../../../../src/localization";
import { useComposer } from "../lib/composer";
import { useChatStore } from "../store/chatStore";
import { getOrCreateChat } from "../store/chatRuntime";
import { AiChatView } from "./AiChat";
import { AiComposerInput } from "./AiComposerInput";
import { ContextIndicator, SessionPicker } from "./AiMiniWindow";
import { ModelDropdown } from "./AiStatusBarControls";
import { ChipsRow } from "./ChipsRow";
import { openSettingsWindow } from "@/modules/settings/openSettingsWindow";
import { SharedAttachments } from "./SharedAttachments";

const settings = () => openSettingsWindow("models");
export function UnifiedChat() {
  const session = useChatStore((s) => s.activeSessionId);
  return session ? (
    <SessionChat key={session} session={session} />
  ) : (
    <p className="p-4">{t("Loading sessions…")}</p>
  );
}
function SessionChat({ session }: { session: string }) {
  const chat = useMemo(() => getOrCreateChat(session), [session]);
  const helpers = useChat<UIMessage>({ chat });
  const c = useComposer();
  const usage = useChatStore((s) => s.agentMeta.tokens);
  useLocale();
  const [catalog, setCatalog] = useState<SuggestionCatalog>({items:[],builtin:false});
  const hints = localizeSuggestions(catalog,t);
  useEffect(() => {
    const reload = () =>
      void invoke<SuggestionCatalog>("backend", {
        method: "suggestions.catalog",
        params: {},
      })
        .then(setCatalog)
        .catch(console.error);
    reload();
    const event = (e: MessageEvent) => {
      if (e.source !== window.parent || e.origin !== location.origin) return;
      if (e.data?.type === "jarvis:focus-chat") {
        c.textareaRef.current?.focus();
        if (typeof e.data.text === "string")
          c.setValue(e.data.text.slice(0, 12000));
      }
      if (e.data?.type === "jarvis:suggestions") reload();
    };
    window.addEventListener("message", event);
    return () => window.removeEventListener("message", event);
  }, [c.setValue]);
  return (
    <section
      aria-label={t("Assistant Chat")}
      data-unified-chat
      className="jarvis-chat flex min-h-0 flex-1 flex-col text-foreground"
    >
      <header className="jarvis-chat-toolbar">
        <div className="jarvis-chat-model">
          <span className="jarvis-chat-mark">
            <MessageSquare size={18} />
          </span>
          <ModelDropdown />
          <ContextIndicator messages={helpers.messages} />
          <span className="jarvis-chat-usage" aria-label={t("Session tokens")}>
            {t("Input")}: {usage.inputTokens.toLocaleString()} · {t("Output")}:{" "}
            {usage.outputTokens.toLocaleString()} · {t("Cached")}:{" "}
            {usage.cachedInputTokens.toLocaleString()}
          </span>
        </div>
        <div className="jarvis-chat-actions">
          <SessionPicker />
          <button
            aria-label={t("Clear Chat")}
            className="jarvis-chat-action"
            onClick={() =>
              void invoke("backend", {
                method: "history.clear",
                params: {},
              }).catch(console.error)
            }
          >
            <Trash2 size={16} />
            <span>{t("Clear Chat")}</span>
          </button>
          <button
            onClick={() => void settings()}
            aria-label={t("Settings")}
            className="jarvis-chat-action"
          >
            <Settings2 size={16} />
            <span>{t("Settings")}</span>
          </button>
        </div>
      </header>
      <div
        className="flex min-h-0 flex-1 flex-col"
        role="log"
        aria-label={t("Messages")}
      >
        {helpers.messages.length ? (
          <AiChatView
            messages={helpers.messages}
            status={helpers.status}
            error={helpers.error}
            clearError={helpers.clearError}
            addToolApprovalResponse={helpers.addToolApprovalResponse}
            stop={helpers.stop}
          />
        ) : (
          <div className="jarvis-chat-empty">
            <span>
              <Sparkles size={28} />
            </span>
            <h2>{t("How can I help?")}</h2>
            <p>{t("Ask a question, attach a file or work with your tools.")}</p>
          </div>
        )}
      </div>
      <div className="jarvis-chat-hints">
        {hints.slice(0, 8).map(([name, prompt]) => (
          <button
            className="jarvis-hint"
            key={name}
            onClick={() => c.setValue(prompt)}
          >
            {name}
          </button>
        ))}
      </div>
      <div className="jarvis-composer shrink-0 space-y-2" data-shared-composer>
        <ChipsRow
          files={c.files}
          onRemoveFile={c.removeFile}
          snippets={c.pickedSnippets}
          onRemoveSnippet={c.removeSnippet}
          commands={c.pickedCommands}
          onRemoveCommand={c.removeCommand}
        />
        <AiComposerInput />
        <div className="flex flex-wrap items-center justify-between gap-2">
          <SharedAttachments />
          <button
            className="jarvis-send disabled:opacity-50"
            disabled={!c.isBusy && !c.canSend}
            onClick={c.isBusy ? c.stop : c.submit}
          >
            {c.isBusy ? <Square size={16} /> : <Send size={16} />}{" "}
            {t(c.isBusy ? "Stop" : "Send")}
          </button>
        </div>
      </div>
    </section>
  );
}
