import { useEffect, useState } from "react";
import { convertToModelMessages, pruneMessages, type UIMessage } from "ai";
import { usePreferencesStore } from "@/modules/settings/preferences";
import { useChatStore } from "../store/chatStore";
import { useAgentsStore } from "../store/agentsStore";
import { usePlanStore } from "../store/planStore";
import { getChatBudgetTools } from "../store/chatRuntime";
import { BUILTIN_AGENTS } from "../lib/agents";
import { prepareAgentBudget } from "../lib/agentBudget";
import { contextBudgetEstimate, contextBudgetKey, currentContextBudget } from "../lib/contextBudget";
import { compactModelMessagesDetailed } from "../lib/compact";
import { budgetBrowserObservations } from "../lib/browserObservation";
import { projectInterruptedHistory } from "../lib/interruptedHistory";
import { localCaptureBase, restrictScreenHistory } from "../lib/screenCapture";
import { projectRequestMessages, readProjectMemory } from "../lib/transport";
import { modelKeepsReasoning, resolveModel } from "../config";
import { buildScreenTools } from "../tools/screen";
import { useSharedModelContext } from "./useSharedContextLimit";

type Estimate = ReturnType<typeof contextBudgetEstimate> & { fixedInstructionsFull: boolean };
/** Read-only preflight. Existing chat handlers and cached schemas are shared with the guard. */
export function useContextBudget(messages: UIMessage[]) {
  const chat = useChatStore();
  const prefs = usePreferencesStore();
  const agents = useAgentsStore();
  const planMode = usePlanStore(state => state.active);
  const { contextLimit, supportsVision } = useSharedModelContext();
  const persona = [...BUILTIN_AGENTS, ...agents.customAgents].find(agent => agent.id === agents.activeId) ?? BUILTIN_AGENTS[0];
  const live = { workspaceRoot: chat.live.getWorkspaceRoot(), cwd: chat.live.getCwd(),
    activeFile: chat.live.getActiveFile(), terminalPrivate: chat.live.isActiveTerminalPrivate(), activeEditor: chat.live.getActiveEditor?.() ?? null };
  const key = contextBudgetKey(chat.selectedModelId, [chat.activeSessionId, chat.apiKeys, chat.customEndpointKeys,
    prefs.lmstudioBaseURL, prefs.lmstudioModelId, prefs.mlxBaseURL, prefs.mlxModelId,
    prefs.ollamaBaseURL, prefs.ollamaModelId, prefs.openaiCompatibleBaseURL, prefs.openaiCompatibleModelId,
    prefs.openaiCompatibleContextLimit, prefs.openrouterModelId, prefs.customEndpoints,
    prefs.customInstructions, persona.name, persona.instructions, planMode, live, contextLimit, supportsVision]);
  const [result, setResult] = useState<{key:string;value:Estimate}>();
  useEffect(() => {
    let active = true;
    const timer = setTimeout(() => { void (async () => {
      const session = chat.activeSessionId;
      if (!session) return;
      const info = resolveModel(chat.selectedModelId, prefs.customEndpoints);
      const captureAllowed = supportsVision && localCaptureBase(info.provider, prefs.lmstudioBaseURL) !== null;
      const baseTools = getChatBudgetTools(session);
      const tools = budgetBrowserObservations({ ...baseTools, ...(captureAllowed ? buildScreenTools(session, true) : {}) }, contextLimit);
      const projectMemory = await readProjectMemory(live.workspaceRoot);
      const budget = await prepareAgentBudget({ modelId: chat.selectedModelId, contextLimit, tools, owner: baseTools,
        persona, customInstructions: prefs.customInstructions, projectMemory, planMode });
      const projectedMessages = await projectRequestMessages(messages, live, chat.selectedModelId, prefs.lmstudioBaseURL);
      const projected = await convertToModelMessages(projectInterruptedHistory(projectedMessages), {tools});
      const pruned = pruneMessages({ messages: restrictScreenHistory(projected, captureAllowed),
        reasoning: modelKeepsReasoning(info) ? "none" : "before-last-message", emptyMessages: "remove" });
      const compact = compactModelMessagesDetailed(pruned, contextLimit, { reservedTokens: budget.reservedTokens });
      const value = { ...contextBudgetEstimate(contextLimit, budget.reservedTokens, compact.messages), fixedInstructionsFull: budget.fixedInstructionsFull };
      if (active) setResult({key,value});
    })().catch(() => { if (active) setResult(undefined); }); }, 100);
    return () => { active = false; clearTimeout(timer); };
  // The key contains every selected provider, instruction and live-context input.
  }, [key, messages]);
  return { estimate: currentContextBudget(result, key), contextLimit };
}
