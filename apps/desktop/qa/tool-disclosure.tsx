import { useState } from "react";
import { createRoot } from "react-dom/client";
import { Tool } from "../workbench/src/components/ai-elements/tool";
import { Reasoning, ReasoningTrigger, ReasoningContent } from "../workbench/src/components/ai-elements/reasoning";
import { AiToolApproval } from "../workbench/src/modules/ai/components/AiToolApproval";
import { buildJarvisTools } from "../workbench/src/modules/ai/tools/jarvis";
import { buildTerminalTools } from "../workbench/src/modules/ai/tools/terminal";
import { useChatStore } from "../workbench/src/modules/ai/store/chatStore";
import type { ToolPart } from "../workbench/src/components/ai-elements/tool";
import { setLocale } from "../src/localization";
import "../workbench/src/styles/globals.css";

const qa = (window as any).__toolDisclosureQA = {
  update: null as any, locale: setLocale, responses: [] as boolean[],
  showGateway: null as any, suggest: null as any, gatewayPart: null as ToolPart | null,
  insertions: [] as string[], automaticInsertions: [] as string[],
};
useChatStore.getState().setLive({
  ...useChatStore.getState().live,
  injectIntoActivePty: (command) => { qa.insertions.push(command); return true; },
});
const gatewayTools = buildJarvisTools(buildTerminalTools({
  getCwd: () => null, getWorkspaceRoot: () => null, getSessionId: () => "gateway-fixture",
  getTerminalContext: () => null, isActiveTerminalPrivate: () => false,
  injectIntoActivePty: (command) => { qa.automaticInsertions.push(command); return false; },
  openPreview: () => false, spawnAgent: () => null, readAgentOutput: () => null,
  readCache: new Map(),
}), false);
function Fixture() {
  const [state, update] = useState<{ failed: boolean; streaming: boolean; actionOutput?: unknown }>({ failed: false, streaming: true });
  const [gateway, setGateway] = useState<ToolPart | null>(null);
  qa.update = update;
  qa.showGateway = (part: ToolPart) => { qa.gatewayPart = part; setGateway(part); };
  qa.suggest = async () => {
    const request = { name: "suggest_command", args: { command: "echo GATEWAY_INSERT", explanation: "Owned suggestion fixture" } };
    const options = { toolCallId: `gateway-call-${qa.insertions.length}`, messages: [], abortSignal: new AbortController().signal };
    const approval = gatewayTools.jarvis_action.needsApproval;
    const required = typeof approval === "function" ? await approval(request, options) : Boolean(approval);
    if (required) throw new Error("A pure command suggestion must not require execution approval");
    const output = await gatewayTools.jarvis_action.execute!(request, options);
    const part = { type: "tool-jarvis_action", toolCallId: options.toolCallId, state: "output-available", input: request, output } as const;
    qa.showGateway(part);
    return part;
  };
  return <main>
    <p>User message remains visible</p>
    <section aria-label="catalog">
      <Tool toolName="jarvis_catalog" state={state.failed ? "output-error" : "output-available"}
        input={{ query: "tools" }} output={{ tools: Array.from({ length: 100 }, (_, i) => `catalog-item-${i}`) }}
        errorText={state.failed ? "Synthetic tool failure" : undefined} />
    </section>
    <section aria-label="action"><Tool toolName="jarvis_action" state={state.actionOutput ? "output-available" : "input-streaming"} input={{ name: "documents.list", args: {} }} output={state.actionOutput} /></section>
    <section aria-label="list"><Tool toolName="list_directory" state="output-available" input={{ path: "fixture" }} output={{ entries: [{ name: "fixture.txt", kind: "file" }] }} /></section>
    <section aria-label="reasoning"><Reasoning isStreaming={state.streaming}><ReasoningTrigger /><ReasoningContent>Private reasoning fixture</ReasoningContent></Reasoning></section>
    <section aria-label="approval"><AiToolApproval toolName="jarvis_action" part={{ type: "tool-jarvis_action", toolCallId: "fixture-call", state: "approval-requested", input: { name: "documents.delete", args: { id: "fixture" } }, approval: { id: "fixture-approval" } }} onRespond={approved => qa.responses.push(approved)} /></section>
    <section aria-label="gateway">
      {gateway && <Tool key={gateway.toolCallId} toolName="jarvis_action" state={gateway.state} input={gateway.input}
        output={"output" in gateway ? gateway.output : undefined} errorText={"errorText" in gateway ? gateway.errorText : undefined} />}
    </section>
  </main>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
