import { countMessage } from "../../../../../src/localization";
import { useLocale } from "../../../../../src/i18n";
import { t as translateMessage } from "../../../../../src/localization";
import {
  Conversation,
  ConversationContent,
  ConversationEmptyState,
  ConversationScrollButton,
} from "@/components/ai-elements/conversation";
import {
  Message,
  MessageContent,
  MessageResponse,
  type MessageResponseProps,
} from "@/components/ai-elements/message";
import { MarkdownCode } from "@/components/ai-elements/markdown-code";
import {
  MarkdownLink,
  type MarkdownLinkProps,
} from "@/modules/markdown/MarkdownLink";
import {
  Reasoning,
  ReasoningContent,
  ReasoningTrigger,
} from "@/components/ai-elements/reasoning";
import { Tool } from "@/components/ai-elements/tool";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { HugeiconsIcon } from "@hugeicons/react";
import {
  ArrowRight01Icon,
  CodeIcon,
  File01Icon,
  HashtagIcon,
  TerminalIcon,
} from "@hugeicons/core-free-icons";
import { SLASH_COMMANDS, TERAX_CMD_RE } from "../lib/slashCommands";
import { Spinner } from "@/components/ui/spinner";
import { useChatStore } from "../store/chatStore";
import { sendMessage } from "../store/chatRuntime";
import type {
  ChatStatus,
  DynamicToolUIPart,
  ToolUIPart,
  UIMessage,
  UIMessagePart,
} from "ai";
import { memo, useCallback, useMemo } from "react";
import { AiToolApproval } from "./AiToolApproval";
import { estimateMessageHeight } from "./messageHeightEstimate";

function CommandSnippet({ name }: { name: string }) {
  const meta = SLASH_COMMANDS[name];
  if (!meta) {
    return (
      <div className="inline-flex items-center gap-1.5 rounded-md border border-border/50 bg-muted/40 px-2 py-1 font-mono text-[11px]">
        /{name}
      </div>
    );
  }
  return (
    <div className="inline-flex max-w-full items-center gap-2 rounded-md border border-border/50 bg-muted/40 px-2 py-1">
      <HugeiconsIcon
        icon={meta.icon}
        size={12}
        strokeWidth={1.75}
        className="shrink-0 text-foreground"
      />
      <span className="font-mono text-[11px] text-foreground">
        {meta.invocation}
      </span>
      <span className="truncate text-[11px] text-muted-foreground">
        {meta.label}
      </span>
    </div>
  );
}

type AnyToolPart = ToolUIPart | DynamicToolUIPart;

type ContextChip =
  | { kind: "selection"; source: "terminal" | "editor"; lines: number }
  | { kind: "file"; name: string; lines: number }
  | { kind: "snippet"; name: string };

const SELECTION_RE =
  /<selection\s+source="(terminal|editor)">\n?([\s\S]*?)\n?<\/selection>/g;
const FILE_RE =
  /<file\s+name="([^"]+)"[^>]*>\n?([\s\S]*?)\n?<\/file>/g;
const SNIPPET_RE = /<snippet\s+name="([^"]+)">\n?[\s\S]*?\n?<\/snippet>/g;

function countLines(s: string): number {
  if (!s) return 0;
  const trimmed = s.replace(/\n+$/, "");
  if (!trimmed) return 0;
  return trimmed.split("\n").length;
}

function stripUserContextBlocks(text: string): {
  text: string;
  chips: ContextChip[];
} {
  const chips: ContextChip[] = [];
  let out = text;
  out = out.replace(SELECTION_RE, (_m, source: string, body: string) => {
    chips.push({
      kind: "selection",
      source: source === "editor" ? "editor" : "terminal",
      lines: countLines(body),
    });
    return "";
  });
  out = out.replace(FILE_RE, (_m, name: string, body: string) => {
    chips.push({ kind: "file", name, lines: countLines(body) });
    return "";
  });
  out = out.replace(SNIPPET_RE, (_m, name: string) => {
    chips.push({ kind: "snippet", name });
    return "";
  });
  return { text: out.trim(), chips };
}

const ContextChips = memo(function ContextChips({
  chips,
}: {
  chips: ContextChip[];
}) {
  useLocale();
  return (
    <div className="mb-1 flex flex-wrap gap-1">
      {chips.map((c, i) => (
        <span
          key={i}
          className="inline-flex items-center gap-1 rounded-md border border-border/50 bg-card/60 px-1.5 py-0.5 text-[10.5px] text-muted-foreground"
        >
          {chipIcon(c)}
          <span className="font-medium text-foreground">{chipLabel(c)}</span>
          {"lines" in c && c.lines > 0 ? (
            <span className="opacity-70">· {c.lines}{translateMessage("L")}</span>
          ) : null}
        </span>
      ))}
    </div>
  );
});

function chipIcon(c: ContextChip) {
  if (c.kind === "selection") {
    return (
      <HugeiconsIcon
        icon={c.source === "editor" ? CodeIcon : TerminalIcon}
        size={10}
        strokeWidth={1.75}
      />
    );
  }
  if (c.kind === "file") {
    return <HugeiconsIcon icon={File01Icon} size={10} strokeWidth={1.75} />;
  }
  return <HugeiconsIcon icon={HashtagIcon} size={10} strokeWidth={1.75} />;
}

function chipLabel(c: ContextChip): string {
  if (c.kind === "selection") {
    return c.source === "editor" ? "Editor selection" : "Terminal selection";
  }
  if (c.kind === "file") return c.name;
  return `#${c.name}`;
}
type AnyPart = UIMessagePart<Record<string, never>, Record<string, never>>;

type ApprovalArg = {
  id: string;
  approved: boolean;
  reason?: string;
};

type Props = {
  messages: UIMessage[];
  status: ChatStatus;
  error: Error | undefined;
  clearError: () => void;
  addToolApprovalResponse: (arg: ApprovalArg) => void | PromiseLike<void>;
  stop: () => void | PromiseLike<void>;
};

export function AiChatView({
  messages,
  status,
  error,
  clearError,
}: Props) {
  const isBusy = status === "submitted" || status === "streaming";
  const lastMessage = messages[messages.length - 1];
  const showSpinner = isBusy && lastMessage?.role === "user";
  const streamingMessageId =
    status === "streaming" && lastMessage?.role === "assistant"
      ? lastMessage.id
      : null;
  const step = useChatStore((s) => s.agentMeta.step);
  const hitStepCap = useChatStore((s) => s.agentMeta.hitStepCap);
  const compactionNotice = useChatStore((s) => s.agentMeta.compactionNotice);
  const patchAgentMeta = useChatStore((s) => s.patchAgentMeta);
  const showContinue =
    !isBusy && hitStepCap && lastMessage?.role === "assistant";

  const onApproval = useCallback(
    (id: string, approved: boolean) => useChatStore.getState().respondToApproval(id, approved),
    [],
  );

  if (messages.length === 0) {
    return (
      <Conversation>
        <ConversationContent>
          <ConversationEmptyState
            title={translateMessage("Ask Jarvis anything")}
            description={translateMessage("Explain command output, fix errors, generate snippets, or run a task.")}
          />
        </ConversationContent>
      </Conversation>
    );
  }

  return (
    <Conversation>
      <ConversationContent className="gap-5 p-3">
        {messages.map((m, i) => (
          <RenderedMessage
            key={m.id}
            message={m}
            onApproval={onApproval}
            streaming={m.id === streamingMessageId}
            deferOffscreen={i < messages.length - LIVE_TAIL_MESSAGES}
          />
        ))}
        {compactionNotice && (
          <CompactionNotice
            droppedCount={compactionNotice.droppedCount}
            onDismiss={() => patchAgentMeta({ compactionNotice: null })}
          />
        )}
        {showSpinner && (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Spinner />
            <span className="truncate">{step ?? "Thinking…"}</span>
          </div>
        )}
        {showContinue && (
          <ContinueRow
            onContinue={() => {
              patchAgentMeta({ hitStepCap: false });
              void sendMessage(
                "Continue from where you stopped. Don't recap — just keep going.",
              );
            }}
          />
        )}
        {error && (
          <div className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
            <div className="font-medium">{translateMessage("Request failed.")}</div>
            <div className="mt-0.5 leading-relaxed opacity-90">
              {error.message}
            </div>
            <button
              type="button"
              onClick={clearError}
              className="mt-1 underline opacity-80 hover:opacity-100"
            >{translateMessage("Dismiss")}</button>
          </div>
        )}
      </ConversationContent>
      <ConversationScrollButton />
    </Conversation>
  );
}

const CompactionNotice = memo(function CompactionNotice({
  droppedCount,
  onDismiss,
}: {
  droppedCount: number;
  onDismiss: () => void;
}) {
  useLocale();
  return (
    <div className="flex items-center gap-2 rounded-md border border-border/40 bg-muted/30 px-2.5 py-1.5 text-[11px] text-muted-foreground">
      <span className="size-1.5 shrink-0 rounded-full bg-amber-500/80" />
      <span className="flex-1 truncate">{countMessage("Context compacted — {0} older tool results elided to save tokens.", droppedCount)}</span>
      <button
        type="button"
        onClick={onDismiss}
        className="text-[10.5px] underline opacity-70 hover:opacity-100"
      >{translateMessage("Dismiss")}</button>
    </div>
  );
});

const ContinueRow = memo(function ContinueRow({
  onContinue,
}: {
  onContinue: () => void;
}) {
  useLocale();
  return (
    <div className="flex items-center gap-2 rounded-md border border-border/50 bg-card/60 px-2.5 py-1.5 text-[11px]">
      <span className="flex-1 text-muted-foreground">{translateMessage("Hit the step limit. Continue to keep going.")}</span>
      <button
        type="button"
        onClick={onContinue}
        className="rounded-md border border-border/60 bg-background px-2 py-0.5 text-[11px] font-medium text-foreground transition-colors hover:bg-accent"
      >{translateMessage("Continue")}</button>
    </div>
  );
});

/**
 * Newest messages stay fully laid out: the stream, the stick-to-bottom
 * measurement and approval prompts all live there. Older ones let the browser
 * skip layout and paint while off screen (`content-visibility: auto`), so a
 * chat with hundreds of messages costs only what is near the viewport.
 */
const LIVE_TAIL_MESSAGES = 2;

export const RenderedMessage = memo(function RenderedMessage({
  message,
  onApproval,
  streaming,
  deferOffscreen,
}: {
  message: UIMessage;
  onApproval: (id: string, approved: boolean) => void;
  streaming: boolean;
  deferOffscreen: boolean;
}) {
  const deferProps = deferOffscreen
    ? {
        className: "terax-chat-item",
        style: {
          containIntrinsicSize: `auto ${estimateMessageHeight(message)}px`,
        },
      }
    : undefined;
  // Index of the trailing text part — only that one is "live" mid-stream.
  // Earlier text parts (separated by tool calls) are already finalized.
  let lastTextIdx = -1;
  for (let i = message.parts.length - 1; i >= 0; i -= 1) {
    if (message.parts[i]?.type === "text") {
      lastTextIdx = i;
      break;
    }
  }
  // Hooks must run unconditionally (Rules of Hooks) even though user messages
  // render without groups; grouping is cheap so this stays out of the way.
  const groups = useMemo(() => buildPartGroups(message.parts as AnyPart[]), [
    message.parts,
  ]);

  if (message.role === "user") {
    const rawText = message.parts
      .filter((p): p is { type: "text"; text: string } => p.type === "text")
      .map((p) => p.text)
      .join("\n");

    const cmdMatch = rawText.match(TERAX_CMD_RE);
    const commandName = cmdMatch?.[1] ?? null;
    const withoutCmd = cmdMatch ? rawText.slice(cmdMatch[0].length) : rawText;
    const stripped = stripUserContextBlocks(withoutCmd);

    return (
      <Message from="user" {...deferProps}>
        <MessageContent>
          {commandName ? <CommandSnippet name={commandName} /> : null}
          {stripped.chips.length > 0 ? (
            <ContextChips chips={stripped.chips} />
          ) : null}
          {stripped.text ? (
            <p className="whitespace-pre-wrap wrap-break-word">
              {stripped.text}
            </p>
          ) : null}
        </MessageContent>
      </Message>
    );
  }

  return (
    <Message from={message.role} {...deferProps}>
      <MessageContent>
        <div className="flex flex-col gap-3">
          {groups.map((g) => {
            if (g.kind === "activity") {
              return (
                <PartAppear key={`${message.id}-${g.key}`}>
                  <ActivityGroup parts={g.parts} onApproval={onApproval} streaming={streaming} />
                </PartAppear>
              );
            }
            return (
              <PartAppear key={`${message.id}-${g.key}`}>
                <RenderedPart
                  part={g.part}
                  onApproval={onApproval}
                  streaming={streaming && g.idx === lastTextIdx}
                />
              </PartAppear>
            );
          })}
        </div>
      </MessageContent>
    </Message>
  );
});

type Group =
  | { kind: "single"; part: AnyPart; idx: number; key: string }
  | { kind: "activity"; parts: AnyPart[]; key: string };

function partKey(p: AnyPart, idx: number): string {
  return (p as { toolCallId?: string }).toolCallId ?? `i-${idx}`;
}

function isActivityPart(part: AnyPart): boolean {
  if (part.type === "reasoning") return true;
  if (part.type !== "dynamic-tool" && !part.type.startsWith("tool-")) return false;
  const tool = part as unknown as AnyToolPart;
  // Decisions and failures stay in the conversation, outside the disclosure.
  if (["approval-requested", "output-error", "output-denied"].includes(tool.state)) return false;
  const output = "output" in tool ? tool.output : undefined;
  if (output && typeof output === "object") {
    const result = output as Record<string, unknown>;
    if (result.ok === false || result.error) return false;
  }
  return !("errorText" in tool && tool.errorText);
}

function buildPartGroups(parts: AnyPart[]): Group[] {
  const groups: Group[] = [];
  let activity: Extract<Group, { kind: "activity" }> | undefined;
  parts.forEach((part, idx) => {
    // SDK step markers have no visual content and do not split one activity.
    if (part.type === "step-start") return;
    if (isActivityPart(part)) {
      if (!activity) {
        activity = { kind: "activity", parts: [], key: `activity-${partKey(part, idx)}` };
        groups.push(activity);
      }
      activity.parts.push(part);
    } else {
      activity = undefined;
      groups.push({ kind: "single", part, idx, key: partKey(part, idx) });
    }
  });
  return groups;
}

const ActivityGroup = memo(function ActivityGroup({ parts, onApproval, streaming }: {
  parts: AnyPart[];
  streaming: boolean;
  onApproval: (id: string, approved: boolean) => void;
}) {
  useLocale();
  const working = streaming && parts.some(part => {
    const state = (part as { state?: string }).state;
    return state === "input-streaming" || state === "input-available" || state === "approval-responded";
  });
  return (
    <Collapsible className="group/activity not-prose">
      <CollapsibleTrigger className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[11px] text-muted-foreground hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring">
        <HugeiconsIcon icon={ArrowRight01Icon} size={11} strokeWidth={2}
          className="shrink-0 transition-transform group-data-[state=open]/activity:rotate-90" />
        <span>{translateMessage("Activity")}</span>
        {working ? <span>{translateMessage("Working…")}</span> : null}
      </CollapsibleTrigger>
      <CollapsibleContent className="terax-collapsible-content ml-3 space-y-1 border-l border-border/40 pl-2">
        {parts.map((part, idx) => <RenderedPart key={partKey(part, idx)} part={part}
          onApproval={onApproval} streaming={false} />)}
      </CollapsibleContent>
    </Collapsible>
  );
});
const PartAppear = memo(function PartAppear({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="animate-in fade-in-0 slide-in-from-bottom-1 duration-200 ease-out">
      {children}
    </div>
  );
});

const aiStreamdownComponents = {
  a: (props: MarkdownLinkProps) => (
    <MarkdownLink {...props} onSettled={useChatStore.getState().focusInput} />
  ),
  code: MarkdownCode,
};

function AiMessageResponse(props: Omit<MessageResponseProps, "components">) {
  return (
    <MessageResponse
      {...props}
      components={aiStreamdownComponents}
    />
  );
}

const RenderedPart = memo(function RenderedPart({
  part,
  onApproval,
  streaming,
}: {
  part: AnyPart;
  onApproval: (id: string, approved: boolean) => void;
  streaming: boolean;
}) {
  if (part.type === "text") {
    return (
      <AiMessageResponse streaming={streaming}>
        {(part as unknown as { text: string }).text}
      </AiMessageResponse>
    );
  }

  if (part.type === "reasoning") {
    return (
      <Reasoning>
        <ReasoningTrigger />
        <ReasoningContent>
          {(part as unknown as { text: string }).text}
        </ReasoningContent>
      </Reasoning>
    );
  }

  if (
    part.type === "dynamic-tool" ||
    (typeof part.type === "string" && part.type.startsWith("tool-"))
  ) {
    return (
      <RenderedTool
        part={part as unknown as AnyToolPart}
        onApproval={onApproval}
      />
    );
  }

  return null;
});

const RenderedTool = memo(function RenderedTool({
  part,
  onApproval,
}: {
  part: AnyToolPart;
  onApproval: (id: string, approved: boolean) => void;
}) {
  const toolName =
    part.type === "dynamic-tool"
      ? part.toolName
      : part.type.replace(/^tool-/, "");

  if (part.state === "approval-requested") {
    return (
      <AiToolApproval
        part={part as Extract<ToolUIPart, { state: "approval-requested" }>}
        toolName={toolName}
        onRespond={(approved) => onApproval(part.approval.id, approved)}
      />
    );
  }

  return (
    <Tool
      toolName={toolName}
      state={part.state}
      input={part.input}
      output={"output" in part ? part.output : undefined}
      errorText={"errorText" in part ? part.errorText : undefined}
    />
  );
});
