import { stopOnUnavailableToolCall, stopOnRepeatedInvalidToolCall, UNAVAILABLE_TOOL_MESSAGE, INVALID_TOOL_INPUT_MESSAGE } from "../lib/errors";
import { t } from "../../../../../src/localization";
import { z } from "zod";
import { childReadContext } from "./subagentExecution";
import { childCapabilityTools, type ChildHandoff } from "./childCapabilities";
import { archiveChildResults, type ChildResultArchive } from "./childResults";
import { readModelToolResult } from "../lib/toolHistory";
import { asSchema, generateText, isStepCount, tool, type ModelMessage, type ToolSet } from "ai";
import {
  buildConfiguredLanguageModel,
  configuredModelContextLimit,
  type LocalProviderConfig,
} from "../lib/agent";
import type { ProviderKeys } from "../lib/keyring";
import type { ToolContext } from "../tools/context";
import { assertContextBudget, replyTokenBudget, CONTEXT_FULL_MESSAGE, compactModelMessagesDetailed, estimateTokens } from "../lib/compact";
import { SUBAGENTS, type SubagentType } from "./registry";
import { activeAgentToolNames, toolProfileInstructions } from "../lib/serviceRouting";

const SUBAGENT_MAX_STEPS = 12;

type Args = {
  type: SubagentType;
  prompt: string;
  keys: ProviderKeys;
  modelId: string;
  toolContext: ToolContext;
  buildChildTools: (ctx: ToolContext) => ToolSet;
  local?: LocalProviderConfig;
  abortSignal?: AbortSignal;
  onStep?: (label: string) => void;
};

type RunResult = ChildResultArchive & {
  summary: string;
  stepCount: number;
  durationMs: number;
  pending_actions?: ChildHandoff[];
};

export async function runSubagent({
  type,
  prompt,
  keys,
  modelId,
  toolContext,
  buildChildTools,
  local,
  abortSignal,
  onStep,
}: Args): Promise<RunResult> {
  abortSignal?.throwIfAborted();
  const def = SUBAGENTS[type];
  if (!def) throw new Error(`unknown subagent type: ${type}`);

  const childContext = childReadContext(toolContext);
  const handoffs: ChildHandoff[] = [];
  const tools = childCapabilityTools(buildChildTools(childContext), handoffs);

  const model = await buildConfiguredLanguageModel(modelId, keys, local, abortSignal);
  abortSignal?.throwIfAborted();

  const contextLimit = await configuredModelContextLimit(modelId, local, abortSignal);
  let originalMessages: ModelMessage[] = [];
  const agentTools = tools as ToolSet;
  agentTools.tool_result_read = tool({
    description: "Read a completed tool output from this child agent by tool_call_id, without executing it again. Does not access parent chats or other agents.",
    inputSchema: z.object({ tool_call_id: z.string().min(1).max(128), offset: z.number().int().min(0).default(0), limit: z.number().int().min(1).max(8000).default(2000) }),
    execute: async ({ tool_call_id, offset, limit }) => readModelToolResult(originalMessages, tool_call_id, offset, limit),
  });
  const activeTools = activeAgentToolNames(agentTools, contextLimit);
  const schemas = await Promise.all(Object.entries(agentTools).filter(([name]) => activeTools.includes(name)).map(async ([name, action]) => ({
    name, description: action.description, parameters: await asSchema(action.inputSchema).jsonSchema,
  })));
  const instructions = def.systemPrompt + toolProfileInstructions(activeTools) + "\nAll configured Jarvis capabilities are discoverable through jarvis_catalog and mcp_catalog. Safe reads execute here; other actions return requires_parent_approval and stop this child. The parent must evaluate the exact handoff through its normal jarvis_action approval flow. Never claim a deferred action ran. Preserve paths and text in the user's language.";
  const reservedTokens = estimateTokens(instructions) + estimateTokens(schemas) + 128;
  abortSignal?.throwIfAborted();
  const start = Date.now();
  const result = await generateText({
    model,
    instructions,
    prompt,
    tools: agentTools,
    activeTools,
    maxOutputTokens: replyTokenBudget(contextLimit),
    prepareStep: ({ messages }) => {
      originalMessages = messages;
      const compact = compactModelMessagesDetailed(messages, contextLimit, { reservedTokens, recoveryTool: "direct" });
      assertContextBudget(compact, t(CONTEXT_FULL_MESSAGE));
      return { messages: compact.messages };
    },
    stopWhen: [isStepCount(SUBAGENT_MAX_STEPS), () => handoffs.length > 0, stopOnUnavailableToolCall, stopOnRepeatedInvalidToolCall],
    abortSignal,
    onStepEnd: (step) => {
      if (!onStep || abortSignal?.aborted) return;
      const last = step.toolCalls?.[step.toolCalls.length - 1];
      if (last) onStep(`${type}: ${last.toolName}`);
    },
  });

  const failure = stopOnUnavailableToolCall({steps: result.steps}) ? UNAVAILABLE_TOOL_MESSAGE
    : stopOnRepeatedInvalidToolCall({steps: result.steps}) ? INVALID_TOOL_INPUT_MESSAGE : null;
  return {
    summary: result.text || (failure ? t(failure) : "(no output)"),
    stepCount: result.steps?.length ?? 0,
    durationMs: Date.now() - start,
    ...archiveChildResults(result.steps),
    ...(handoffs.length ? { pending_actions: handoffs } : {}),
  };
}
