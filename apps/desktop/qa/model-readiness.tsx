import { useState } from "react";
import { createRoot } from "react-dom/client";
import { useLocalModelCatalog, useManagedModelCatalog, useOllamaModelCatalog, useModelLifecycleRevision } from "../workbench/src/modules/ai/hooks/useLocalModelCatalog";
import { getConfiguredChatModels, isModelSelectionConfigured, lmstudioOptionId } from "../workbench/src/modules/ai/config";
import { managedModelContext } from "../workbench/src/modules/ai/lib/managedModels";

type Mode = "loaded" | "unloaded" | "empty";
const managedId = `jarvis-managed:llm:fixture/model@${"a".repeat(40)}`;
const callbacks = new Map<number, (event: unknown) => void>();
let callbackId = 0;
const qa = {
  mode: "loaded" as Mode, hold: false,
  pending: [] as { command: string; resolve: (value: unknown) => void; reject: (error: Error) => void }[],
  calls: [] as string[],
  lifecycle() { for (const callback of callbacks.values()) callback({event:"jarvis://model-lifecycle",id:1,payload:null}); },
  finish(index: number, mode: Mode, fail = false) {
    const request = qa.pending[index];
    if (fail) request.reject(new Error("Synthetic unavailable server"));
    else request.resolve(response(request.command, mode));
  },
};
function response(command: string, mode: Mode): unknown {
  if (mode === "empty") return command === "backend" ? {models:[]} : [];
  const loaded = mode === "loaded";
  if (command === "lm_catalog") return [{key:"fixture-lm",type:"llm",display_name:"Fixture LM",loaded_instances:loaded?[{id:"fixture",config:{context_length:8192}}]:[]}];
  if (command === "lm_models") return ["fixture-lm"];
  if (command === "ollama_catalog") return [{id:"fixture-ollama",loaded,context_length:loaded?8192:null}];
  return {models:[{model_id:managedId,repo_id:"fixture/model",revision:"a".repeat(40),kind:"llm",status:mode,loaded,context:65536}]};
}
// This standalone source fixture owns the complete mock host; no native runtime exists.
Object.assign(window, {__modelReadinessQA:qa, __TAURI_INTERNALS__:{
  transformCallback(callback: (event: unknown) => void) {callbacks.set(++callbackId,callback);return callbackId;},
  unregisterCallback(id: number) {callbacks.delete(id);},
  invoke: async (command: string, args: Record<string, unknown>) => {
    if (command === "plugin:event|listen") return args.handler;
    if (command === "plugin:event|unlisten") {callbacks.delete(args.eventId as number);return;}
    if (!["lm_catalog","lm_models","ollama_catalog","backend"].includes(command)) throw new Error(`Unexpected command ${command}`);
    if (command === "backend" && args.method !== "managed_chat.list") throw new Error(`Unexpected backend ${args.method}`);
    qa.calls.push(command);
    if (qa.hold) return new Promise((resolve,reject)=>qa.pending.push({command,resolve,reject}));
    return response(command,qa.mode);
  },
}});
function Fixture() {
  const [refresh,setRefresh] = useState(0);
  const lm = useLocalModelCatalog("http://127.0.0.1:1/v1",refresh);
  const ollama = useOllamaModelCatalog("http://127.0.0.1:2/v1",refresh);
  const managedModels = useManagedModelCatalog(refresh);
  const revision = useModelLifecycleRevision();
  const state = {apiKeys:{},lmstudioModelId:"fixture-lm",lmstudioCatalog:lm.models,lmstudioDetectedIds:lm.ids,ollamaCatalog:ollama,managedModels};
  return <main><button onClick={()=>setRefresh(value=>value+1)}>Refresh</button>
    <output id="snapshot">{JSON.stringify({models:getConfiguredChatModels(state), lmReady:isModelSelectionConfigured(lmstudioOptionId("fixture-lm"),state), context:managedModelContext(managedId), revision})}</output>
  </main>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
