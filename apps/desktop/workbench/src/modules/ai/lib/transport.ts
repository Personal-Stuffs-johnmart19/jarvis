import { t } from "../../../../../src/localization";
import { readProjectInstructions } from "./projectInstructions";
import { formatEditorContext, refersToEditor, allowsLocalEditorContext, type ActiveEditorSnapshot } from "./editorContext";
import { checkReadableCanonical } from "./security";
import type { ToolSet } from "ai";
import type { UIMessage } from "@ai-sdk/react";
import type { CustomEndpoint } from "../config";
import { runAgentStream, type AgentUsageDelta } from "./agent";
import type { ProviderKeys, CustomEndpointKeys } from "./keyring";
import { formatAiError, formatToolInputErrors } from "./errors";
import { currentWorkspaceEnv } from "@/modules/workspace";
import { native } from "./native";
import type { ToolContext } from "../tools/tools";

export async function readProjectMemory(workspaceRoot: string | null): Promise<string | null> {
  if (!workspaceRoot) return null;
  const workspace = { ...currentWorkspaceEnv() };
  return readProjectInstructions(workspaceRoot, async path => {
    const safety = await checkReadableCanonical(path, path => native.canonicalize(path, workspace));
    if (!safety.ok) throw new Error(safety.reason);
    const content = await native.readProjectMemory(workspaceRoot, safety.canonical, workspace);
    if (content === null) throw new Error("Project instructions unavailable");
    return { kind: "text", content };
  });
}

type LiveSnapshot = {
  cwd: string | null;
  terminalPrivate: boolean;
  workspaceRoot: string | null;
  activeFile: string | null;
  activeEditor?: ActiveEditorSnapshot | null;
};

type Deps = {
  getKeys: () => ProviderKeys;
  toolContext: ToolContext;
  tools?: ToolSet;
  getModelId: () => string;
  getCustomInstructions: () => string;
  getAgentPersona: () => { name: string; instructions: string } | null;
  getLive: () => LiveSnapshot;
  getLmstudioBaseURL?: () => string | undefined;
  getLmstudioModelId?: () => string | undefined;
  getMlxBaseURL?: () => string | undefined;
  getMlxModelId?: () => string | undefined;
  getOllamaBaseURL?: () => string | undefined;
  getOllamaModelId?: () => string | undefined;
  getOpenaiCompatibleBaseURL?: () => string | undefined;
  getOpenaiCompatibleModelId?: () => string | undefined;
  getOpenaiCompatibleContextLimit?: () => number | undefined;
  getOpenrouterModelId?: () => string | undefined;
  getCustomEndpoints?: () => readonly CustomEndpoint[];
  getCustomEndpointKeys?: () => CustomEndpointKeys;
  onStep?: (step: string | null) => void;
  onUsage?: (delta: AgentUsageDelta) => void;
  onCompact?: (info: { droppedCount: number }) => void;
  onFinishMeta?: (info: { hitStepCap: boolean; finishReason: string }) => void;
  getPlanMode?: () => boolean;
};

type SendOptions = {
  messages: UIMessage[];
  abortSignal?: AbortSignal;
  [k: string]: unknown;
};

/** The meter and actual transport share editor safety checks and environment injection. */
export async function projectRequestMessages(messages: UIMessage[], live: LiveSnapshot, modelId: string, lmstudioBaseURL?: string) {
  let envBlock = formatEnvBlock(live);
  const latestUser = [...messages].reverse().find(message => message.role === "user");
  const question = latestUser?.parts.filter(part => part.type === "text").map(part => part.text).join(" ") ?? "";
  if (live.activeEditor && allowsLocalEditorContext(modelId, lmstudioBaseURL) && refersToEditor(question)) {
    const safety = await checkReadableCanonical(live.activeEditor.path, native.canonicalize);
    if (safety.ok) envBlock = [envBlock, formatEditorContext(live.activeEditor)].filter(Boolean).join("\n\n");
  }
  return envBlock ? injectEnvIntoLastUser(messages, envBlock) : messages;
}

export function createContextAwareTransport(deps: Deps) {
  const run = async (options: SendOptions) => {
    const modelId = deps.getModelId();
    const lmstudioBaseURL = deps.getLmstudioBaseURL?.();
    const live = deps.getLive();
    const projectMemory = await readProjectMemory(live.workspaceRoot);
    const messagesForRun = await projectRequestMessages(options.messages, live, modelId, lmstudioBaseURL);
    const result = await runAgentStream({
      keys: deps.getKeys(),
      modelId,
      customInstructions: deps.getCustomInstructions(),
      agentPersona: deps.getAgentPersona(),
      toolContext: deps.toolContext,
      tools: deps.tools,
      onStep: deps.onStep,
      onUsage: deps.onUsage,
      onCompact: deps.onCompact,
      onFinishMeta: deps.onFinishMeta,
      lmstudioBaseURL,
      lmstudioModelId: deps.getLmstudioModelId?.(),
      mlxBaseURL: deps.getMlxBaseURL?.(),
      mlxModelId: deps.getMlxModelId?.(),
      ollamaBaseURL: deps.getOllamaBaseURL?.(),
      ollamaModelId: deps.getOllamaModelId?.(),
      openaiCompatibleBaseURL: deps.getOpenaiCompatibleBaseURL?.(),
      openaiCompatibleModelId: deps.getOpenaiCompatibleModelId?.(),
      openaiCompatibleContextLimit: deps.getOpenaiCompatibleContextLimit?.(),
      openrouterModelId: deps.getOpenrouterModelId?.(),
      customEndpoints: deps.getCustomEndpoints?.(),
      customEndpointKeys: deps.getCustomEndpointKeys?.(),
      planMode: deps.getPlanMode?.(),
      projectMemory,
      uiMessages: messagesForRun,
      abortSignal: options.abortSignal,
    });
    return formatToolInputErrors(result.toUIMessageStream({
      originalMessages: options.messages,
      onError: error => formatAiError(error, t),
    }), t);
  };

  return {
    sendMessages: run,
    async reconnectToStream(): Promise<null> {
      return null;
    },
  };
}

export function injectEnvIntoLastUser(
  messages: UIMessage[],
  envBlock: string,
): UIMessage[] {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (m.role !== "user") continue;
    const parts = m.parts as ReadonlyArray<{ type: string; text?: string }>;
    let textIdx = -1;
    for (let j = 0; j < parts.length; j++) {
      if (parts[j].type === "text") {
        textIdx = j;
        break;
      }
    }
    const nextParts =
      textIdx === -1
        ? [{ type: "text", text: envBlock }, ...parts]
        : parts.map((p, idx) =>
            idx === textIdx
              ? { ...p, text: `${envBlock}\n\n${p.text ?? ""}` }
              : p,
          );
    const out = messages.slice();
    out[i] = { ...m, parts: nextParts } as UIMessage;
    return out;
  }
  return messages;
}

export function formatEnvBlock(live: LiveSnapshot): string | null {
  const lines: string[] = [];
  if (live.workspaceRoot) lines.push(`workspace_root: ${live.workspaceRoot}`);
  if (live.cwd) lines.push(`active_terminal_cwd: ${live.cwd}`);
  if (live.activeFile) lines.push(`active_file: ${live.activeFile}`);
  if (live.terminalPrivate) lines.push("active_terminal_mode: private");
  if (lines.length === 0) return null;
  return `<env>\n${lines.join("\n")}\n</env>`;
}
