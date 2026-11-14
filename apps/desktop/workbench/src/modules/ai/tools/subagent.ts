import { childModelOutput } from "../agents/childResults";
import { tool, type ToolSet } from "ai";
import { z } from "zod";
import { usePreferencesStore } from "@/modules/settings/preferences";
import { runSubagent } from "../agents/runSubagent";
import { SUBAGENTS, type SubagentType } from "../agents/registry";
import { executeSubagentTool } from "../agents/subagentExecution";
import { useChatStore } from "../store/chatStore";
import type { ToolContext } from "./context";

const TYPE_KEYS = Object.keys(SUBAGENTS) as [SubagentType, ...SubagentType[]];

export function buildSubagentTools(ctx: ToolContext, buildChildTools: (ctx: ToolContext) => ToolSet) {
  return {
    run_subagent: tool({
      toModelOutput: ({output,toolCallId}) => childModelOutput(output,toolCallId),
      description: `Spawn an isolated subagent with shared lazy capability discovery, safe reads and a fresh message history. Use when you need to delegate a self-contained read-only investigation (large search, code review, security audit) without polluting your own context. The subagent returns a single text summary; pick a 'type' that matches its job.

Types:
${TYPE_KEYS.map((k) => `- ${k}: ${SUBAGENTS[k].description}`).join("\n")}

Safe reads auto-execute. Other actions return pending_actions with exact names, arguments and child tool-call IDs; no action has run. Evaluate each requested handoff once through jarvis_action so the normal policy and approval card apply. Never report an action complete before its successful result. Children cannot resume automatically after parent execution.`,
      inputSchema: z.object({
        type: z.enum(TYPE_KEYS),
        prompt: z
          .string()
          .describe(
            "Self-contained instruction. The subagent has no memory of prior conversation — include all relevant context.",
          ),
        description: z
          .string()
          .optional()
          .describe("Short label shown in the chat UI for the spawn card."),
      }),
      execute: async ({ type, prompt, description }, options) => {
        return executeSubagentTool({
          type,
          prompt,
          description,
          abortSignal: options?.abortSignal,
          getSessionId: ctx.getSessionId,
          getActiveSessionId: () => useChatStore.getState().activeSessionId,
          captureConfig: () => {
            const {
              apiKeys,
              customEndpointKeys,
              selectedModelId,
            } = useChatStore.getState();
            const prefs = usePreferencesStore.getState();
            return {
              keys: { ...apiKeys },
              modelId: selectedModelId,
              local: {
                lmstudioBaseURL: prefs.lmstudioBaseURL,
                lmstudioModelId: prefs.lmstudioModelId,
                mlxBaseURL: prefs.mlxBaseURL,
                mlxModelId: prefs.mlxModelId,
                ollamaBaseURL: prefs.ollamaBaseURL,
                ollamaModelId: prefs.ollamaModelId,
                openaiCompatibleBaseURL: prefs.openaiCompatibleBaseURL,
                openaiCompatibleModelId: prefs.openaiCompatibleModelId,
                openaiCompatibleContextLimit: prefs.openaiCompatibleContextLimit,
                openrouterModelId: prefs.openrouterModelId,
                customEndpoints: prefs.customEndpoints,
                customEndpointKeys: { ...customEndpointKeys },
              },
            };
          },
          onStep: (label) =>
            useChatStore.getState().patchAgentMeta({ step: label }),
          run: ({ type: t, prompt: p, config, abortSignal, onStep }) =>
            runSubagent({
              type: t,
              prompt: p,
              keys: config.keys,
              modelId: config.modelId,
              toolContext: ctx,
              buildChildTools,
              local: config.local,
              abortSignal,
              onStep,
            }),
        });
      },
    }),
  } as const;
}
