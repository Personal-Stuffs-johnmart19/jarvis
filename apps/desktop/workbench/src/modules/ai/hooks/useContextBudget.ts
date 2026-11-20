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
  const selectedModelId = useChatStore((s) => s.selectedModelId);
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const apiKeys = useChatStore((s) => s.apiKeys);
  const customEndpointKeys = useChatStore((s) => s.customEndpointKeys);
  const liveAccessors = useChatStore((s) => s.live);
  const lmstudioBaseURL = usePreferencesStore((s) => s.lmstudioBaseURL);
  const lmstudioModelId = usePreferencesStore((s) => s.lmstudioModelId);
  const mlxBaseURL = usePreferencesStore((s) => s.mlxBaseURL);
  const mlxModelId = usePreferencesStore((s) => s.mlxModelId);
  const ollamaBaseURL = usePreferencesStore((s) => s.ollamaBaseURL);
  const ollamaModelId = usePreferencesStore((s) => s.ollamaModelId);
  const openaiCompatibleBaseURL = usePreferencesStore((s) => s.openaiCompatibleBaseURL);
  const openaiCompatibleModelId = usePreferencesStore((s) => s.openaiCompatibleModelId);
  const openaiCompatibleContextLimit = usePreferencesStore((s) => s.openaiCompatibleContextLimit);
  const openrouterModelId = usePreferencesStore((s) => s.openrouterModelId);
  const customEndpoints = usePreferencesStore((s) => s.customEndpoints);
  const customInstructions = usePreferencesStore((s) => s.customInstructions);
  const activeAgentId = useAgentsStore((s) => s.activeId);
  const customAgents = useAgentsStore((s) => s.customAgents);
  const planMode = usePlanStore(state => state.active);
  const { contextLimit, supportsVision } = useSharedModelContext();
  const persona = [...BUILTIN_AGENTS, ...customAgents].find(agent => agent.id === activeAgentId) ?? BUILTIN_AGENTS[0];
  const live = { workspaceRoot: liveAccessors.getWorkspaceRoot(), cwd: liveAccessors.getCwd(),
    activeFile: liveAccessors.getActiveFile(), terminalPrivate: liveAccessors.isActiveTerminalPrivate(), activeEditor: liveAccessors.getActiveEditor?.() ?? null };
  const key = contextBudgetKey(selectedModelId, [activeSessionId, apiKeys, customEndpointKeys,
    lmstudioBaseURL, lmstudioModelId, mlxBaseURL, mlxModelId,
    ollamaBaseURL, ollamaModelId, openaiCompatibleBaseURL, openaiCompatibleModelId,
    openaiCompatibleContextLimit, openrouterModelId, customEndpoints,
    customInstructions, persona.name, persona.instructions, planMode, live, contextLimit, supportsVision]);
  const [result, setResult] = useState<{key:string;value:Estimate}>();
  useEffect(() => {
    let active = true;
    const timer = setTimeout(() => { void (async () => {
      const session = activeSessionId;
      if (!session) return;
      const info = resolveModel(selectedModelId, customEndpoints);
      const captureAllowed = supportsVision && localCaptureBase(info.provider, lmstudioBaseURL) !== null;
      const baseTools = getChatBudgetTools(session);
      const tools = budgetBrowserObservations({ ...baseTools, ...buildScreenTools(session, captureAllowed) }, contextLimit);
      const projectMemory = await readProjectMemory(live.workspaceRoot);
      const budget = await prepareAgentBudget({ modelId: selectedModelId, contextLimit, tools, owner: baseTools,
        persona, customInstructions, projectMemory, planMode });
      const projectedMessages = await projectRequestMessages(messages, live, selectedModelId, lmstudioBaseURL);
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
