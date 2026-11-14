import { useManagedModelCatalog, useModelLifecycleRevision } from "./useLocalModelCatalog";
import { isManagedModelId } from "../lib/managedModels";
import { useEffect, useState } from "react";
import { usePreferencesStore } from "@/modules/settings/preferences";
import { useChatStore } from "../store/chatStore";
import { localServerModelMetadata } from "../lib/lmstudio";
import { getModelContextLimit, isCompatModelId, endpointIdFromCompatModel } from "../config";

export function useSharedModelContext() {
  const model = useChatStore((s) => s.selectedModelId);
  const managedModels = useManagedModelCatalog();
  const revision = useModelLifecycleRevision();
  const lastInput = useChatStore((s) => s.agentMeta.lastInputTokens);
  const base = usePreferencesStore((s) => s.lmstudioBaseURL);
  const local = usePreferencesStore((s) => s.lmstudioModelId);
  const ollamaBase = usePreferencesStore((s) => s.ollamaBaseURL);
  const ollamaModel = usePreferencesStore((s) => s.ollamaModelId);
  const endpoints = usePreferencesStore((s) => s.customEndpoints);
  const compat = usePreferencesStore((s) => s.openaiCompatibleContextLimit);
  const isLocal = model === "lmstudio-local" || model === "ollama-local";
  const provider = model === "ollama-local" ? "ollama" : "lmstudio";
  const serverBase = provider === "ollama" ? ollamaBase : base;
  const modelKey = provider === "ollama" ? ollamaModel : local;
  const requestKey = JSON.stringify([provider, serverBase, modelKey, revision, lastInput]);
  const [loaded, setLoaded] = useState<{ key: string; limit: number; supportsVision: boolean }>();
  useEffect(() => {
    if (!isLocal || !serverBase.trim() || !modelKey.trim()) {
      setLoaded(undefined);
      return;
    }
    let active = true;
    void localServerModelMetadata(provider, serverBase, modelKey)
      .then((metadata) => { if (active) setLoaded({ key: requestKey, limit: metadata.contextLimit, supportsVision: metadata.supportsVision }); })
      .catch(() => { if (active) setLoaded({ key: requestKey, limit: 4096, supportsVision: false }); });
    return () => { active = false; };
  }, [isLocal, provider, serverBase, modelKey, requestKey, lastInput]);
  if (isManagedModelId(model)) return { contextLimit: managedModels.find(item => item.model_id === model)?.context ?? 8192, supportsVision: false };
  const contextLimit = isLocal
    ? (loaded?.key === requestKey ? loaded.limit : 4096)
    : getModelContextLimit(model, isCompatModelId(model)
      ? endpoints.find((endpoint) => endpoint.id === endpointIdFromCompatModel(model))?.contextLimit
      : compat);
  return { contextLimit, supportsVision: isLocal && provider === "lmstudio" && loaded?.key === requestKey && loaded.supportsVision === true };
}

export function useSharedContextLimit() {
  return useSharedModelContext().contextLimit;
}
