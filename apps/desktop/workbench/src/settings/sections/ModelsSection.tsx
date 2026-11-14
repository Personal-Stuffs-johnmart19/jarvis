import { emit } from "@/host/event";
import { unloadLocalModel } from "@/modules/ai/lib/modelLifecycle";
import { createProxyFetch } from "@/modules/ai/lib/proxyFetch";
import { confirmAction } from "../../../../src/confirmAction";
import { t as translateMessage } from "../../../../src/localization";
import { unloadManagedModel } from "@/modules/ai/lib/managedModels";
import { openSettingsWindow } from "@/modules/settings/openSettingsWindow";
import { whisperCppReady } from "@/modules/ai/lib/stt";
import { useLocalModelCatalog, useOllamaModelCatalog, useManagedModelCatalog } from "@/modules/ai/hooks/useLocalModelCatalog";
import { t } from "../../../../src/localization";
import { lmCatalog, ollamaCatalog, type LocalModel } from "@/modules/ai/lib/lmstudio";
import { setLmstudioReasoning } from "@/modules/settings/store";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import {
  type CustomEndpoint,
  compatModelIdForEndpoint,
  filterChatLocalCatalog,
  filterChatModelIds,
  getConfiguredChatModels,
  type ModelInfo,
  isLmstudioOptionId,
  lmstudioKeyFromOptionId,
  isOllamaOptionId,
  ollamaKeyFromOptionId,
  localAutocompleteModelId,
  getCompatModelInfo,
  getProvider,
  isCompatModelId,
  MODELS,
  type ModelId,
  PROVIDERS,
  type ProviderId,
  type ProviderInfo,
  providerNeedsKey,
  STT_PROVIDER_LABELS,
  getAvailableSttProviders,
  WHISPERCPP_DEFAULT_BASE_URL,
} from "@/modules/ai/config";
import {
  type CustomEndpointKeys,
  clearCustomEndpointKey,
  clearKey,
  getAllCustomEndpointKeys,
  getAllKeys,
  setCustomEndpointKey,
  setKey,
} from "@/modules/ai/lib/keyring";
import { usePreferencesStore } from "@/modules/settings/preferences";
import {
  type AutocompleteTrigger,
  emitKeysChanged,
  setAutocompleteEnabled,
  setAutocompleteModelId,
  setAutocompleteProvider,
  setAutocompleteTrigger,
  setCustomEndpoints,
  setDefaultModel,
  setFavoriteModelIds,
  setGroqSttModel,
  setLmstudioBaseURL,
  setLmstudioModelId,
  setMlxBaseURL,
  setMlxModelId,
  setOllamaBaseURL,
  setOllamaModelId,
  setOpenaiCompatibleBaseURL,
  setOpenaiCompatibleContextLimit,
  setOpenaiCompatibleModelId,
  setOpenrouterModelId,
  setRecentModelIds,
  setSttProvider,
  setWhispercppBaseURL,
} from "@/modules/settings/store";
import { getBindingTokens, SHORTCUTS } from "@/modules/shortcuts/shortcuts";
import {
  Add01Icon,
  ArrowDown01Icon,
  ArrowUpRight01Icon,
  Cancel01Icon,
  CheckmarkCircle02Icon,
  ChevronDown,
  Mic01Icon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { invoke } from "@/host/core";
import { openUrl } from "@/host/opener";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { ProviderIcon } from "../components/ProviderIcon";
import { ProviderKeyCard } from "../components/ProviderKeyCard";
import { SectionHeader } from "../components/SectionHeader";
import { ModelLibrary } from "../components/ModelLibrary";

type KeysMap = Record<ProviderId, string | null>;

const isLocalProvider = (id: ProviderId): boolean => !providerNeedsKey(id);

type LocalMeta = {
  urlPlaceholder: string;
  modelPlaceholder: string;
  description: string;
  modelHint: React.ReactNode;
};

const LOCAL_META: Partial<Record<ProviderId, LocalMeta>> = {
  lmstudio: {
    urlPlaceholder: "http://localhost:1234/v1",
    modelPlaceholder: "qwen2.5-coder-7b-instruct",
    description:
      "Run GGUF models via LM Studio's HTTP server (Developer tab → enable).",
    modelHint: (
      <>
        {t("The model id loaded in LM Studio — see the server's")}{" "}
        <span className="font-mono">{t("/v1/models")}</span> {t("page.")}{" "}
      </>
    ),
  },
  mlx: {
    urlPlaceholder: "http://127.0.0.1:8080/v1",
    modelPlaceholder: "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit",
    description:
      "Apple-silicon inference via mlx_lm.server (pip install mlx-lm).",
    modelHint: (
      <>{t("The Hugging Face repo path you launched mlx_lm.server with.")}</>
    ),
  },
  ollama: {
    urlPlaceholder: "http://localhost:11434/v1",
    modelPlaceholder: "qwen2.5-coder:7b",
    description: "Local models via Ollama's built-in OpenAI-compatible API.",
    modelHint: <>{t("The model name from `ollama list` / `ollama pull`.")}</>,
  },
  "openai-compatible": {
    urlPlaceholder: "https://api.example.com/v1",
    modelPlaceholder: "gpt-4o, qwen3-max, glm-4.6, …",
    description: "Any OpenAI-compatible endpoint — vLLM, Z.AI, Fireworks, etc.",
    modelHint: null,
  },
  openrouter: {
    urlPlaceholder: "",
    modelPlaceholder: "anthropic/claude-sonnet-5, openai/gpt-5.6, …",
    description: "Any model on OpenRouter — type its full provider/model id.",
    modelHint: (
      <>
        {t("Browse ids at")}{" "}
        <span className="font-mono">{t("openrouter.ai/models")}</span>.
      </>
    ),
  },
};

function reportCredentialRemovalError(error: unknown): void {
  toast.error(t("Could not remove credential"), { description: String(error) });
}

function reportPartialRemovalError(error: unknown): void {
  toast.error(t("Credential removed; settings may be incomplete"), {
    description: String(error),
  });
}

export function ModelsSection() { return <ModelProviderSettings setup={false} />; }
export function ProviderIntegrations() { return <ModelProviderSettings setup />; }
function ModelProviderSettings({ setup }: { setup: boolean }) {
  const [keys, setKeys] = useState<KeysMap | null>(null);
  const [epKeys, setEpKeys] = useState<CustomEndpointKeys>({});
  const [adding, setAdding] = useState<Set<ProviderId>>(new Set(["lmstudio"]));

  const defaultModel = usePreferencesStore((s) => s.defaultModelId);
  const lmstudioBaseURL = usePreferencesStore((s) => s.lmstudioBaseURL);
  const lmstudioModelId = usePreferencesStore((s) => s.lmstudioModelId);
  const mlxBaseURL = usePreferencesStore((s) => s.mlxBaseURL);
  const mlxModelId = usePreferencesStore((s) => s.mlxModelId);
  const ollamaBaseURL = usePreferencesStore((s) => s.ollamaBaseURL);
  const ollamaModelId = usePreferencesStore((s) => s.ollamaModelId);
  const compatBaseURL = usePreferencesStore((s) => s.openaiCompatibleBaseURL);
  const compatModelId = usePreferencesStore((s) => s.openaiCompatibleModelId);
  const compatContextLimit = usePreferencesStore(
    (s) => s.openaiCompatibleContextLimit,
  );
  const openrouterModelId = usePreferencesStore((s) => s.openrouterModelId);
  const customEndpoints = usePreferencesStore((s) => s.customEndpoints);

  useEffect(() => {
    void getAllKeys().then(setKeys);
  }, []);

  useEffect(() => {
    void getAllCustomEndpointKeys(customEndpoints).then(setEpKeys);
  }, [customEndpoints]);

  const onSaveKey = async (provider: ProviderId, value: string) => {
    await setKey(provider, value);
    setKeys(await getAllKeys());
    await emitKeysChanged();
  };

  const onClearKey = async (provider: ProviderId) => {
    await clearKey(provider);
    setKeys((prev) => (prev ? { ...prev, [provider]: null } : prev));
    await emitKeysChanged().catch(reportPartialRemovalError);
  };

  const onSaveEndpointKey = async (endpointId: string, value: string) => {
    await setCustomEndpointKey(endpointId, value);
    setEpKeys((prev) => ({ ...prev, [endpointId]: value }));
    await emitKeysChanged();
  };

  const onClearEndpointKey = async (endpointId: string) => {
    await clearCustomEndpointKey(endpointId);
    setEpKeys((prev) => ({ ...prev, [endpointId]: null }));
    await emitKeysChanged().catch(reportPartialRemovalError);
  };

  const addOpenClawEndpoint = async () => {
    const oc = await import("@/modules/ai/lib/openclaw");
    const ep: CustomEndpoint = {
      id: crypto.randomUUID().slice(0, 8),
      name: oc.OPENCLAW_ENDPOINT_NAME,
      baseURL: oc.OPENCLAW_DEFAULT_BASE_URL,
      modelId: "",
      contextLimit: 128_000,
    };
    await setCustomEndpoints([...customEndpoints, ep]);
  };

  const addCustomEndpoint = async () => {
    const ep: CustomEndpoint = {
      id: crypto.randomUUID().slice(0, 8),
      name: "",
      baseURL: "",
      modelId: "",
      contextLimit: 128_000,
    };
    await setCustomEndpoints([...customEndpoints, ep]);
  };

  const updateCustomEndpoint = async (
    id: string,
    patch: Partial<CustomEndpoint>,
  ) => {
    await setCustomEndpoints(
      customEndpoints.map((e) => (e.id === id ? { ...e, ...patch } : e)),
    );
  };

  const removeCustomEndpoint = async (id: string) => {
    await clearCustomEndpointKey(id);
    setEpKeys((prev) => {
      const next = { ...prev };
      delete next[id];
      return next;
    });

    try {
      // Finish history cleanup before resetting the selection, which can add
      // to recents. A failed write must not prevent removal of the endpoint.
      const deadModelId = compatModelIdForEndpoint(id);
      const { favoriteModelIds, recentModelIds } =
        usePreferencesStore.getState();
      const history = await Promise.allSettled([
        ...(favoriteModelIds.includes(deadModelId)
          ? [
              setFavoriteModelIds(
                favoriteModelIds.filter((m) => m !== deadModelId),
              ),
            ]
          : []),
        ...(recentModelIds.includes(deadModelId)
          ? [setRecentModelIds(recentModelIds.filter((m) => m !== deadModelId))]
          : []),
      ]);
      const remaining = customEndpoints.filter((e) => e.id !== id);
      const cleanup = await Promise.allSettled([
        setCustomEndpoints(remaining),
        emitKeysChanged(),
      ]);
      const failed = [...history, ...cleanup].find(
        (result) => result.status === "rejected",
      );
      if (failed) throw failed.reason;
    } catch (error) {
      reportPartialRemovalError(error);
    }
  };

  const localConfig = (id: ProviderId): LocalConfig | null => {
    switch (id) {
      case "lmstudio":
        return {
          baseURL: lmstudioBaseURL,
          modelId: lmstudioModelId,
          setBaseURL: setLmstudioBaseURL,
          setModelId: setLmstudioModelId,
        };
      case "mlx":
        return {
          baseURL: mlxBaseURL,
          modelId: mlxModelId,
          setBaseURL: setMlxBaseURL,
          setModelId: setMlxModelId,
        };
      case "ollama":
        return {
          baseURL: ollamaBaseURL,
          modelId: ollamaModelId,
          setBaseURL: setOllamaBaseURL,
          setModelId: setOllamaModelId,
        };
      case "openai-compatible":
        return {
          baseURL: compatBaseURL,
          modelId: compatModelId,
          setBaseURL: setOpenaiCompatibleBaseURL,
          setModelId: setOpenaiCompatibleModelId,
          contextLimit: compatContextLimit,
          setContextLimit: setOpenaiCompatibleContextLimit,
        };
      case "openrouter":
        return {
          baseURL: "",
          modelId: openrouterModelId,
          setBaseURL: async () => {},
          setModelId: setOpenrouterModelId,
          noBaseURL: true,
        };
      default:
        return null;
    }
  };

  const isConfigured = (id: ProviderId): boolean => {
    if (id === "openrouter") return !!keys?.[id] && !!openrouterModelId.trim();
    if (!isLocalProvider(id)) return !!keys?.[id];
    const cfg = localConfig(id);
    if (!cfg) return false;
    if (id === "openai-compatible")
      return !!cfg.baseURL.trim() && !!cfg.modelId.trim();
    return !!cfg.modelId.trim();
  };

  if (!keys) {
    return (
      <div className="text-[12px] text-muted-foreground">{t("Loading…")}</div>
    );
  }

  const configuredIds = new Set(
    PROVIDERS.filter((p) => isConfigured(p.id)).map((p) => p.id),
  );
  const visibleIds = new Set<ProviderId>(configuredIds);
  for (const id of adding) visibleIds.add(id);
  const visibleProviders = PROVIDERS.filter(
    (p) => p.id !== "openai-compatible" && visibleIds.has(p.id),
  );
  const addableProviders = PROVIDERS.filter(
    (p) => p.id !== "openai-compatible" && !visibleIds.has(p.id),
  );

  const removeProvider = async (id: ProviderId) => {
    await onClearKey(id);
    try {
      if (id === "openrouter") {
        await setOpenrouterModelId("");
      } else if (isLocalProvider(id)) {
        const cfg = localConfig(id);
        if (cfg) {
          await cfg.setModelId("");
          if (id === "openai-compatible") await cfg.setBaseURL("");
        }
      }
      setAdding((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    } catch (error) {
      reportPartialRemovalError(error);
    }
  };

  const addProvider = (id: ProviderId) => {
    setAdding((prev) => new Set(prev).add(id));
    requestAnimationFrame(() =>
      requestAnimationFrame(() =>
        document
          .querySelector(`[data-provider="${id}"]`)
          ?.scrollIntoView({ block: "center" }),
      ),
    );
  };

  return (
    <div className="flex flex-col gap-7">
      <SectionHeader
        title={t(setup ? "Model providers" : "Models")}
        description={t(
          setup ? "Connect the providers you use. Keys live in your OS keychain and are used by Jarvis." : "Choose available models. Configure connections in Integrations.",
        )}
      />

      {!setup && <>
      <DefaultsBlock
        defaultModel={defaultModel}
        keys={keys}
        customEndpoints={customEndpoints}
      />

      {configuredIds.has("google") && <GoogleFallbackSetting />}
      <ModelLibrary />
      </>}
      {setup && <LlamaSetting />}
      <VoiceBlock setup={setup} keys={keys} />

      {setup && (

      <div className="flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <Label>{t("Providers")}</Label>
          <Button
            size="sm"
            variant="outline"
            onClick={() => void addOpenClawEndpoint()}
            className="ml-auto mr-2 h-7 px-2 text-[11px]"
          >
            {t("Add OpenClaw Gateway")}
          </Button>
          <AddProviderMenu
            providers={addableProviders}
            onAdd={addProvider}
            onAddCompat={addCustomEndpoint}
          />
        </div>

        {visibleProviders.length === 0 && customEndpoints.length === 0 ? (
          <div className="rounded-lg border border-dashed border-border/60 bg-card/40 px-4 py-8 text-center">
            <p className="text-[12px] text-muted-foreground">
              {t("No providers connected yet.")}{" "}
            </p>
            <p className="mt-0.5 text-[10.5px] text-muted-foreground/70">
              {t(
                'Click "Add provider" to connect a cloud or local model source.',
              )}{" "}
            </p>
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            {visibleProviders.map((p) =>
              p.id === "openrouter" ? (
                <LocalProviderCard
                  key={p.id}
                  provider={p}
                  configured={configuredIds.has(p.id)}
                  config={localConfig(p.id)!}
                  meta={LOCAL_META[p.id]!}
                  compatKey={keys[p.id]}
                  onSaveKey={(v) => onSaveKey(p.id, v)}
                  onClearKey={() =>
                    onClearKey(p.id).catch(reportCredentialRemovalError)
                  }
                  onRemove={() =>
                    removeProvider(p.id).catch(reportCredentialRemovalError)
                  }
                />
              ) : isLocalProvider(p.id) ? (
                <LocalProviderCard
                  key={p.id}
                  provider={p}
                  configured={configuredIds.has(p.id)}
                  config={localConfig(p.id)!}
                  meta={LOCAL_META[p.id]!}
                  onSaveKey={(v) => onSaveKey(p.id, v)}
                  onClearKey={() =>
                    onClearKey(p.id).catch(reportCredentialRemovalError)
                  }
                  onRemove={() =>
                    removeProvider(p.id).catch(reportCredentialRemovalError)
                  }
                />
              ) : (
                <ProviderKeyCard
                  key={p.id}
                  provider={p}
                  currentKey={keys[p.id]}
                  onSave={(v) => onSaveKey(p.id, v)}
                  onClear={() => onClearKey(p.id).catch(reportCredentialRemovalError)}
                  managed={p.id === "google"}
                  onRemove={p.id === "google" ? undefined : () => removeProvider(p.id).catch(reportCredentialRemovalError)
                  }
                />
              ),
            )}
            {customEndpoints.map((ep) => (
              <CustomEndpointCard
                key={ep.id}
                endpoint={ep}
                endpointKey={epKeys[ep.id] ?? null}
                onSaveKey={(v) => onSaveEndpointKey(ep.id, v)}
                onClearKey={() =>
                  onClearEndpointKey(ep.id).catch(reportCredentialRemovalError)
                }
                onUpdate={(patch) => updateCustomEndpoint(ep.id, patch)}
                onRemove={() =>
                  removeCustomEndpoint(ep.id).catch(
                    reportCredentialRemovalError,
                  )
                }
              />
            ))}
          </div>
        )}
      </div>
      )}
    </div>
  );
}

type LocalConfig = {
  baseURL: string;
  modelId: string;
  setBaseURL: (v: string) => Promise<void>;
  setModelId: (v: string) => Promise<void>;
  contextLimit?: number;
  setContextLimit?: (v: number) => Promise<void>;
  noBaseURL?: boolean;
};

function AddProviderMenu({
  providers,
  onAdd,
  onAddCompat,
}: {
  providers: readonly ProviderInfo[];
  onAdd: (id: ProviderId) => void;
  onAddCompat: () => void;
}) {
  const cloud = providers.filter((p) => !isLocalProvider(p.id));
  const local = providers.filter(
    (p) => isLocalProvider(p.id) && p.id !== "openai-compatible",
  );

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          size="sm"
          variant="outline"
          className="h-7 gap-1.5 px-2.5 text-[11px]"
        >
          <HugeiconsIcon icon={Add01Icon} size={12} strokeWidth={2} />
          {t("Add provider")}{" "}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-55 p-1">
        {cloud.length > 0 ? (
          <>
            <DropdownMenuLabel className="px-2 text-[10px] tracking-wide text-muted-foreground uppercase">
              {t("Cloud")}{" "}
            </DropdownMenuLabel>
            {cloud.map((p) => (
              <ProviderMenuItem key={p.id} provider={p} onAdd={onAdd} />
            ))}
          </>
        ) : null}
        <DropdownMenuLabel className="px-2 text-[10px] tracking-wide text-muted-foreground uppercase">
          {t("Local & custom")}{" "}
        </DropdownMenuLabel>
        {local.map((p) => (
          <ProviderMenuItem key={p.id} provider={p} onAdd={onAdd} />
        ))}
        <DropdownMenuItem
          onSelect={() => onAddCompat()}
          className="flex items-center gap-2 text-[12px]"
        >
          <ProviderIcon provider="openai-compatible" size={13} />
          <span>{t("OpenAI Compatible")}</span>
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function ProviderMenuItem({
  provider,
  onAdd,
}: {
  provider: ProviderInfo;
  onAdd: (id: ProviderId) => void;
}) {
  return (
    <DropdownMenuItem
      onSelect={() => onAdd(provider.id)}
      className="flex items-center gap-2 text-[12px]"
    >
      <ProviderIcon provider={provider.id} size={13} />
      <span>{provider.label}</span>
    </DropdownMenuItem>
  );
}

function DefaultsBlock({
  defaultModel,
  keys,
  customEndpoints,
}: {
  defaultModel: ModelId;
  keys: KeysMap;
  customEndpoints: readonly CustomEndpoint[];
}) {
  const prefs = usePreferencesStore();
  const { models: lmstudioCatalog, ids: lmstudioDetectedIds } = useLocalModelCatalog(prefs.lmstudioBaseURL);
  const ollamaCatalog = useOllamaModelCatalog(prefs.ollamaBaseURL);
  const [managedError, setManagedError] = useState("");
  const [unloading, setUnloading] = useState<string | null>(null);
  const managedModels = useManagedModelCatalog();
  const unload = async (id: string) => {
    setUnloading(id); setManagedError("");
    try { await unloadManagedModel(id); await emit("jarvis://model-lifecycle"); }
    catch (error) { setManagedError(String(error)); }
    finally { setUnloading(null); }
  };
  const availableModels = getConfiguredChatModels({ ...prefs, apiKeys: keys, lmstudioCatalog, lmstudioDetectedIds, ollamaCatalog, managedModels });
  return (
    <div className="flex flex-col gap-3">
      <Label>{t("Defaults")}</Label>
      <p className="text-xs text-muted-foreground">
        {t(
          "Only configured models are listed. Add providers and API keys in Integrations.",
        )}{" "}
      </p>
      <Button variant="outline" onClick={() => void openSettingsWindow("integrations")}>{t("Configure integrations")}</Button>
      <div className="flex flex-col gap-2.5 rounded-lg border border-border/60 bg-card/60 px-3 py-2.5">
        <FieldRow label={translateMessage("Chat model")}>{(inputId) => <>
          <DefaultModelPicker id={inputId}
            defaultModel={defaultModel}
            customEndpoints={customEndpoints}
            availableModels={availableModels}
          />
        </>}</FieldRow>
        <AutocompleteRow availableModels={availableModels} />
        {managedModels.map(model => <div key={model.model_id} className="flex items-center justify-between gap-2 text-xs">
          <span className="truncate">{model.name || model.repo_id}  @  {t(model.loaded ? "Loaded" : "Installed")}</span>
          <Button variant="outline" size="sm" disabled={!model.loaded || unloading !== null} onClick={() => void unload(model.model_id)}>{t("Unload")}</Button>
        </div>)}
        {managedError && <p role="alert" className="text-xs text-destructive">{managedError}</p>}
      </div>
    </div>
  );
}

function DefaultModelPicker({
  id,
  defaultModel,
  customEndpoints,
  availableModels,
}: {
  id: string;
  defaultModel: ModelId;
  customEndpoints: readonly CustomEndpoint[];
  availableModels: readonly ModelInfo[];
}) {
  const localId = usePreferencesStore((state) => state.lmstudioModelId);
  const ollamaId = usePreferencesStore((state) => state.ollamaModelId);
  const available = availableModels.find((model) => model.id === defaultModel || (defaultModel === "lmstudio-local" && isLmstudioOptionId(model.id) && lmstudioKeyFromOptionId(model.id) === localId) || (defaultModel === "ollama-local" && isOllamaOptionId(model.id) && ollamaKeyFromOptionId(model.id) === ollamaId));
  const m = available ?? (isCompatModelId(defaultModel)
    ? getCompatModelInfo(defaultModel, customEndpoints)
    : MODELS.find(model => model.id === defaultModel) ?? {
      id: defaultModel, provider: "openai-compatible", label: defaultModel,
      hint: "Unavailable", description: "Selected model is unavailable",
      capabilities: { intelligence: 1, speed: 1, cost: 1 },
    });

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          id={id}
          variant="outline"
          className="h-8 flex-1 justify-between gap-2 px-2.5 text-[11.5px]"
        >
          <span className="flex items-center gap-2 truncate">
            <ProviderIcon provider={m.provider} size={13} />
            <span className="truncate">{m.label}</span>
            <span className="text-muted-foreground">· {m.hint}</span>
          </span>
          <HugeiconsIcon
            icon={ArrowDown01Icon}
            size={11}
            strokeWidth={2}
            className="opacity-70"
          />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="start"
        side="bottom"
        sideOffset={6}
        collisionPadding={12}
        className="min-w-70 p-1"
      >
        <div className="max-h-72 overflow-y-auto overscroll-contain pr-1">
          {PROVIDERS.map((p) => {
            const models = availableModels.filter((model) => model.provider === p.id);
            if (models.length === 0) return null;
            return (
              <div key={p.id} className="px-1 pt-1.5 first:pt-1">
                <div className="mb-0.5 flex items-center gap-1.5 px-2 text-[10px] font-medium tracking-wide text-muted-foreground uppercase">
                  <ProviderIcon provider={p.id} size={11} />
                  <span>{p.label}</span>
                </div>
                {models.map((mod) => (
                  <DropdownMenuItem
                    key={mod.id}
                    onSelect={async () => {
                      if (isOllamaOptionId(mod.id)) {
                        await setOllamaModelId(ollamaKeyFromOptionId(mod.id));
                        await setDefaultModel("ollama-local");
                      } else if (isLmstudioOptionId(mod.id)) {
                        await setLmstudioModelId(lmstudioKeyFromOptionId(mod.id));
                        await setDefaultModel("lmstudio-local");
                      } else await setDefaultModel(mod.id as ModelId);
                    }}
                    className={cn(
                      "flex items-start gap-2 text-[12px]",
                      mod.id === m.id && "bg-accent/50",
                    )}
                  >
                    <span className="flex flex-1 flex-col">
                      <span>{mod.label}</span>
                      <span className="text-[10px] text-muted-foreground">
                        {mod.description}
                      </span>
                    </span>
                  </DropdownMenuItem>
                ))}
              </div>
            );
          })}
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function AutocompleteRow({
  availableModels,
}: {
  availableModels: readonly ModelInfo[];
}) {
  const enabled = usePreferencesStore((s) => s.autocompleteEnabled);
  const trigger = usePreferencesStore((s) => s.autocompleteTrigger);
  const provider = usePreferencesStore((s) => s.autocompleteProvider);
  const modelId = usePreferencesStore((s) => s.autocompleteModelId);
  const userShortcuts = usePreferencesStore((s) => s.shortcuts);
  const aiCompleteShortcut = useMemo(() => {
    const s = SHORTCUTS.find((x) => x.id === "editor.aiComplete");
    const bindings = userShortcuts["editor.aiComplete"] || s?.defaultBindings;
    if (!bindings || bindings.length === 0) return "";
    return getBindingTokens(bindings[0]).join("");
  }, [userShortcuts]);

  const items = availableModels.filter((model) =>
    isLocalProvider(model.provider) || model.capabilities.speed >= 4);
  const legacyLocal = usePreferencesStore((state) => provider === "lmstudio" ? state.lmstudioModelId : provider === "ollama" ? state.ollamaModelId : state.mlxModelId);
  const currentModel = items.find((model) => model.provider === provider &&
    (model.id === modelId || (provider === "lmstudio" && lmstudioKeyFromOptionId(model.id) === localAutocompleteModelId(provider, modelId, legacyLocal)) || (provider === "ollama" && ollamaKeyFromOptionId(model.id) === localAutocompleteModelId(provider, modelId, legacyLocal)) || (isLocalProvider(provider) && provider !== "ollama" && provider !== "openai-compatible" && provider !== "lmstudio" && !modelId)));
  const setModel = async (id: string, providerId: ProviderId) => {
    await setAutocompleteModelId(isLmstudioOptionId(id) ? lmstudioKeyFromOptionId(id) : isOllamaOptionId(id) ? ollamaKeyFromOptionId(id) : id);
    await setAutocompleteProvider(providerId);
  };

  const grouped = useMemo(() => {
    const map = new Map<ProviderId, (typeof items)[number][]>();
    for (const m of items) {
      const arr = map.get(m.provider) ?? [];
      arr.push(m);
      map.set(m.provider, arr);
    }
    return map;
  }, [items]);

  const hasKey = Boolean(currentModel);

  return (
    <>
      <FieldRow label={translateMessage("Autocomplete")}>{(inputId) => <>
        <div className="flex flex-1 items-center gap-2">
          <Switch id={inputId}
            checked={enabled}
            disabled={!currentModel && !enabled}
            onCheckedChange={(v) => void setAutocompleteEnabled(v)}
          />
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="outline"
                disabled={items.length === 0}
                className="h-8 flex-1 justify-between gap-2 px-2.5 text-[11.5px]"
              >
                <span className="flex items-center gap-2 truncate">
                  {currentModel && <ProviderIcon provider={currentModel.provider} size={12} />}
                  <span className="truncate">{currentModel?.label ?? t("Select an available model")}</span>
                  <span className="text-muted-foreground">
                    {currentModel ? `· ${currentModel.hint}` : ""}
                  </span>
                </span>
                <HugeiconsIcon
                  icon={ArrowDown01Icon}
                  size={11}
                  strokeWidth={2}
                  className="opacity-70"
                />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent
              align="start"
              collisionPadding={12}
              className="max-h-72 min-w-70 overflow-y-auto"
            >
              {PROVIDERS.map((p) => {
                const list = grouped.get(p.id);
                if (!list || list.length === 0) return null;
                return (
                  <div key={p.id} className="px-1 pt-1.5 first:pt-1">
                    <div className="mb-0.5 flex items-center gap-1.5 px-2 text-[10px] font-medium tracking-wide text-muted-foreground uppercase">
                      <ProviderIcon provider={p.id} size={11} />
                      <span>{p.label}</span>

                    </div>
                    {list.map((m) => (
                      <DropdownMenuItem
                        key={m.id}
                        onSelect={() => void setModel(m.id, p.id)}
                        className={cn(
                          "text-[11.5px]",
                          m.id === modelId && "bg-accent/50",
                        )}
                      >
                        <span className="flex flex-col">
                          <span>{m.label}</span>
                          <span className="text-[10px] text-muted-foreground">
                            {m.description}
                          </span>
                        </span>
                      </DropdownMenuItem>
                    ))}
                  </div>
                );
              })}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </>}</FieldRow>
      {enabled ? (
        <FieldRow label={translateMessage("Trigger")}>{(inputId) => <>
          <Select
            value={trigger}
            onValueChange={(v) =>
              void setAutocompleteTrigger(v as AutocompleteTrigger)
            }
          >
            <SelectTrigger id={inputId} className="h-8 w-full text-[11.5px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="auto">
                {t("Automatic (as you type)")}
              </SelectItem>
              <SelectItem value="manual">
                {t("Manual (")}
                {aiCompleteShortcut || "shortcut"})
              </SelectItem>
            </SelectContent>
          </Select>
        </>}</FieldRow>
      ) : null}
      {enabled && !hasKey ? (
        <p className="pl-19 text-[10.5px] text-muted-foreground">
          {getProvider(provider).label}{" "}
          {t("is unavailable — configure its provider in Integrations or select an available model.")}{" "}
        </p>
      ) : null}
    </>
  );
}

function LocalProviderCard({
  provider,
  configured,
  config,
  meta,
  compatKey,
  onSaveKey,
  onClearKey,
  onRemove,
}: {
  provider: ProviderInfo;
  configured: boolean;
  config: LocalConfig;
  meta: LocalMeta;
  compatKey?: string | null;
  onSaveKey: (v: string) => Promise<void>;
  onClearKey: () => Promise<void>;
  onRemove: () => void;
}) {
  const {
    baseURL,
    modelId,
    setBaseURL,
    setModelId,
    contextLimit,
    setContextLimit,
    noBaseURL,
  } = config;
  const [urlDraft, setUrlDraft] = useState(baseURL);
  const [modelDraft, setModelDraft] = useState(modelId);
  const [contextDraft, setContextDraft] = useState(String(contextLimit ?? ""));
  const [keyDraft, setKeyDraft] = useState("");
  const [detectedModels, setDetectedModels] = useState<string[]>([]);
  const [catalog, setCatalog] = useState<LocalModel[]>([]);
  const [ollamaModels, setOllamaModels] = useState<Awaited<ReturnType<typeof ollamaCatalog>>>([]);
  const reasoning = usePreferencesStore((s) => s.lmstudioReasoning);
  const selected = catalog.find((m) => m.key === modelDraft);
  const selectedReasoning = selected?.capabilities?.reasoning;
  const selectedOllama = ollamaModels.find((m) => m.id === modelDraft);
  const [unloadingLocal, setUnloadingLocal] = useState(false);
  const [lifecycleError, setLifecycleError] = useState("");
  const lifecycleRequest = useRef<AbortController | null>(null);
  const unloadPending = useRef(false);
  useEffect(() => {
    setUnloadingLocal(false);
    return () => { lifecycleRequest.current?.abort(); unloadPending.current = false; };
  }, [baseURL, provider.id]);
  const unloadSelected = async () => {
    if (unloadPending.current || !["lmstudio", "ollama"].includes(provider.id)) return;
    unloadPending.current = true;
    const providerId = provider.id as "lmstudio" | "ollama";
    const key = modelDraft, url = baseURL;
    const instances = selected?.loaded_instances.map(instance => instance.id) ?? [];
    const controller = new AbortController();
    lifecycleRequest.current = controller;
    setLifecycleError("");
    setUnloadingLocal(true);
    try {
      await unloadLocalModel(providerId, url, key, instances, createProxyFetch({allowPrivateNetwork:true}), controller.signal,
        () => confirmAction(t("Unload {0}? Active requests using this model may stop. Downloaded files and your model selections are kept.", key)));
    } catch (error) {
      if (!controller.signal.aborted) setLifecycleError(String(error));
    } finally {
      if (lifecycleRequest.current === controller) {
        lifecycleRequest.current = null;
        unloadPending.current = false;
      }
      if (!controller.signal.aborted) {
        setUnloadingLocal(false);
        await discover(url);
        await emit("jarvis://model-lifecycle");
      }
    }
  };

  const [discoveryError, setDiscoveryError] = useState("");
  const [discovering, setDiscovering] = useState(false);
  const discoveryVersion = useRef(0);
  const discover = async (url = urlDraft) => {
    const version = ++discoveryVersion.current;
    setDiscovering(true);
    setDiscoveryError("");
    setOllamaModels([]);
    try {
      if (provider.id === "lmstudio") {
        const [cat, rawIds] = await Promise.all([
          lmCatalog(url).catch(() => null),
          invoke<string[]>("lm_models", { baseUrl: url }).catch(() => null),
        ]);
        if (version !== discoveryVersion.current) return;
        if (cat === null && rawIds === null) {
          throw new Error("Could not reach LM Studio server");
        }
        const chatCat = cat ? filterChatLocalCatalog(cat) : [];
        setCatalog(chatCat);
        const catKeys = chatCat.map((m) => m.key);
        const fallbackIds = rawIds ? filterChatModelIds(rawIds) : [];
        const merged = Array.from(new Set([...catKeys, ...fallbackIds]));
        setDetectedModels(merged);
        setTestStatus("ok");
        if (!merged.length) {
          setDiscoveryError(
            "Server is reachable but returned no chat models. Load or download a model and refresh.",
          );
        }
      } else if (provider.id === "ollama") {
        const models = await ollamaCatalog(url);
        if (version !== discoveryVersion.current) return;
        setOllamaModels(models);
        const chatIds = filterChatModelIds(models.map((m) => m.id));
        setDetectedModels(chatIds);
        setTestStatus("ok");
        if (!chatIds.length) setDiscoveryError("Server is reachable but has no chat models. Download a chat model in Ollama, then refresh.");
      } else {
        const rawIds = await invoke<string[]>("lm_models", { baseUrl: url });
        if (version !== discoveryVersion.current) return;
        const ids = filterChatModelIds(rawIds);
        setDetectedModels(ids);
        setTestStatus("ok");
        if (!ids.length)
          setDiscoveryError(
            "Server is reachable but returned no models. Load or download a model and refresh.",
          );
      }
    } catch (error) {
      if (version !== discoveryVersion.current) return;
      setCatalog([]);
      setOllamaModels([]);
      setDetectedModels([]);
      setTestStatus("fail");
      const help =
        provider.id === "lmstudio"
          ? "Start the local API server in LM Studio → Developer, then refresh. Installing the app alone does not start its API."
          : "Start the local model server at the configured Base URL, then refresh.";
      setDiscoveryError(`${String(error)}. ${help}`);
    } finally {
      if (version === discoveryVersion.current) setDiscovering(false);
    }
  };
  useEffect(() => {
    if (["lmstudio", "ollama", "mlx"].includes(provider.id))
      void discover(baseURL);
    return () => {
      discoveryVersion.current++;
    };
  }, [baseURL, provider.id]);

  const [testStatus, setTestStatus] = useState<
    "idle" | "testing" | "ok" | "fail"
  >("idle");

  useEffect(() => setUrlDraft(baseURL), [baseURL]);
  useEffect(() => setModelDraft(modelId), [modelId]);
  useEffect(() => setContextDraft(String(contextLimit ?? "")), [contextLimit]);

  const supportsKey =
    provider.id === "openai-compatible" || provider.id === "openrouter";

  const test = async () => {
    setTestStatus("testing");
    try {
      const status = await invoke<number>("lm_ping", { baseUrl: urlDraft });
      setTestStatus(status >= 200 && status < 300 ? "ok" : "fail");
    } catch {
      setTestStatus("fail");
    }
  };

  return (
    <div
      data-provider={provider.id}
      className="flex flex-col gap-2 rounded-lg border border-border/60 bg-card/60 px-3 py-2.5"
    >
      <div className="flex items-center gap-2">
        <ProviderIcon provider={provider.id} size={15} />
        <span className="text-[12.5px] font-medium">{provider.label}</span>
        {configured ? (
          <Badge
            variant="outline"
            className="ml-1 h-4 gap-1 border-border/60 bg-muted/40 px-1.5 text-[10px] font-normal text-muted-foreground"
          >
            <HugeiconsIcon
              icon={CheckmarkCircle02Icon}
              size={9}
              strokeWidth={2}
            />
            {translateMessage(testStatus === "ok" ? "Server reachable" : "Configured")}
          </Badge>
        ) : null}
        <button
          type="button"
          onClick={() => void openUrl(provider.consoleUrl)}
          className="ml-auto inline-flex items-center gap-0.5 text-[10.5px] text-muted-foreground transition-colors hover:text-foreground"
        >
          {t("Docs")}{" "}
          <HugeiconsIcon
            icon={ArrowUpRight01Icon}
            size={11}
            strokeWidth={1.75}
          />
        </button>
        <Button
          size="icon"
          variant="ghost"
          onClick={onRemove}
          title={t("Remove provider")}
          className="size-7 text-muted-foreground hover:text-destructive"
        >
          <HugeiconsIcon icon={Cancel01Icon} size={12} strokeWidth={1.75} />
        </Button>
      </div>

      <span className="text-[10.5px] leading-relaxed text-muted-foreground">
        {meta.description}
      </span>

      <div className="mt-0.5 flex flex-col gap-2.5">
        {noBaseURL ? null : (
          <FieldRow label={translateMessage("Base URL")}>{(inputId) => <>
            <div className="flex flex-1 gap-1.5">
              <Input id={inputId}
                value={urlDraft}
                onChange={(e) => setUrlDraft(e.target.value)}
                onBlur={() => {
                  const v = urlDraft.trim();
                  if (v !== baseURL) void setBaseURL(v);
                }}
                placeholder={meta.urlPlaceholder}
                spellCheck={false}
                className="h-8 flex-1 font-mono text-[11.5px]"
              />
              <Button
                size="sm"
                variant="outline"
                onClick={() => void test()}
                disabled={!urlDraft.trim()}
                className="h-8 px-3 text-[11px]"
              >
                {t("Test")}{" "}
              </Button>
            </div>
          </>}</FieldRow>
        )}

        {!noBaseURL && (
          <div className="flex flex-col gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={discovering || !urlDraft.trim()}
              onClick={() => void discover()}
            >
              {translateMessage(discovering ? "Discovering…" : "Discover models")}
            </Button>
            {detectedModels.length > 0 && (
              <Select
                value={
                  detectedModels.includes(modelDraft) ? modelDraft : undefined
                }
                onValueChange={(value) => {
                  setModelDraft(value);
                  void setModelId(value);
                }}
              >
                <SelectTrigger aria-label={translateMessage("{0} detected models", provider.label)}>
                  <SelectValue
                    placeholder={t("Select a model from the server")}
                  />
                </SelectTrigger>
                <SelectContent>
                  {detectedModels.map((id) => (
                    <SelectItem key={id} value={id}>
                      {id}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
            {discoveryError && (
              <p role="status" className="text-xs text-muted-foreground">
                {discoveryError}
              </p>
            )}
          </div>
        )}

        {selected && (
          <div
            className="rounded-lg border border-border p-3 text-xs text-muted-foreground"
            aria-label={t("Model capabilities")}
          >
            <p>
              {selected.display_name} ·{" "}
              {translateMessage(selected.loaded_instances.length
                ? "Loaded"
                : "Downloaded · loads on first use")}
            </p>
            <p>
              {t("Chat")}
              {translateMessage(selected.capabilities?.vision ? " · Vision" : "")}
              {translateMessage(selected.capabilities?.trained_for_tool_use ? " · Tool Use" : "")}
              {translateMessage(selected.capabilities?.reasoning ? " · Think" : "")}
            </p>
            <p>
              {t("Model maximum:")}{" "}
              {selected.max_context_length?.toLocaleString() ?? "Unknown"}{" "}
              {t("tokens. Active context:")}{" "}
              {selected.loaded_instances[0]?.config.context_length?.toLocaleString() ??
                "LM Studio defaults"}
              .
            </p>
            <p>
              {t(
                "LM Studio manages GPU placement and loading. Model maximum is not a VRAM estimate.",
              )}
            </p>
          </div>
        )}
        {provider.id === "ollama" && selectedOllama && (
          <div className="text-xs text-muted-foreground" aria-label={t("Ollama model status")}>
            {selectedOllama.loaded === null
              ? t("Load status unavailable")
              : selectedOllama.loaded ? t("Loaded") : t("Downloaded · loads on first use")}
            {selectedOllama.context_length
              ? ` · ${t("Active context:")} ${selectedOllama.context_length?.toLocaleString()} ${t("tokens")}` : ""}
          </div>
        )}

        {((provider.id === "lmstudio" && selected) || (provider.id === "ollama" && selectedOllama)) && (
          <div className="flex flex-col gap-2">
            <Button variant="outline" size="sm" disabled={unloadingLocal || discovering || (provider.id === "lmstudio" ? !selected?.loaded_instances.length : selectedOllama?.loaded !== true)} onClick={() => void unloadSelected()}>
              {t(unloadingLocal ? "Unloading…" : "Unload")}
            </Button>
            {lifecycleError && <p role="alert" className="text-xs text-destructive">{lifecycleError}</p>}
          </div>
        )}
        {selectedReasoning && (
          <FieldRow label={translateMessage("Think")}>{(inputId) => <>
            <Select
              value={reasoning}
              onValueChange={(v) => void setLmstudioReasoning(v)}
            >
              <SelectTrigger id={inputId} aria-label={t("LM Studio reasoning")}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {[
                  "default",
                  ...selectedReasoning.allowed_options,
                ].map((v) => (
                  <SelectItem key={v} value={v}>
                    {v}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </>}</FieldRow>
        )}
        <FieldRow label={translateMessage("Model ID")}>{(inputId) => <>
          <Input id={inputId}
            value={modelDraft}
            onChange={(e) => setModelDraft(e.target.value)}
            onBlur={() => {
              const v = modelDraft.trim();
              if (v !== modelId) void setModelId(v);
            }}
            placeholder={meta.modelPlaceholder}
            spellCheck={false}
            className="h-8 font-mono text-[11.5px]"
          />
        </>}</FieldRow>

        {setContextLimit ? (
          <FieldRow label={translateMessage("Context")}>{(inputId) => <>
            <div className="flex flex-1 items-center gap-1.5">
              <Input id={inputId}
                value={contextDraft}
                onChange={(e) => setContextDraft(e.target.value)}
                onBlur={() => {
                  const v = parseInt(contextDraft);
                  if (Number.isFinite(v) && v >= 1000) void setContextLimit(v);
                  else setContextDraft(String(contextLimit ?? ""));
                }}
                placeholder="128000"
                spellCheck={false}
                className="h-8 w-28 font-mono text-[11.5px]"
              />
              <span className="text-[10.5px] text-muted-foreground">
                {t("tokens")}{" "}
              </span>
            </div>
          </>}</FieldRow>
        ) : null}

        {supportsKey ? (
          <FieldRow label={translateMessage("API key")} staticLabel={!!compatKey}>{(inputId) => <>
            {compatKey ? (
              <div className="flex flex-1 items-center gap-1.5">
                <code className="flex-1 truncate rounded bg-muted/40 px-2 py-1 font-mono text-[11px] text-muted-foreground">
                  {translateMessage("{0}{1}{2}", compatKey.slice(0, 4), "•".repeat(8), compatKey.slice(-4))}
                </code>
                <Button
                  size="icon"
                  variant="ghost"
                  onClick={() => void onClearKey()}
                  title={t("Remove key")}
                  className="size-7 text-muted-foreground hover:text-destructive"
                >
                  <HugeiconsIcon
                    icon={Cancel01Icon}
                    size={12}
                    strokeWidth={1.75}
                  />
                </Button>
              </div>
            ) : (
              <div className="flex flex-1 gap-1.5">
                <Input id={inputId}
                  type="password"
                  value={keyDraft}
                  onChange={(e) => setKeyDraft(e.target.value)}
                  placeholder={t(
                    "Optional — leave empty for unauthenticated endpoints",
                  )}
                  spellCheck={false}
                  className="h-8 flex-1 font-mono text-[11.5px]"
                />
                <Button
                  size="sm"
                  onClick={async () => {
                    const v = keyDraft.trim();
                    if (!v) return;
                    await onSaveKey(v);
                    setKeyDraft("");
                  }}
                  disabled={!keyDraft.trim()}
                  className="h-8 px-3 text-[11px]"
                >
                  {t("Save")}{" "}
                </Button>
              </div>
            )}
          </>}</FieldRow>
        ) : null}

        <StatusLine status={testStatus} />

        {!modelId.trim() && meta.modelHint ? (
          <p className="text-[10.5px] leading-relaxed text-muted-foreground">
            {meta.modelHint}
          </p>
        ) : null}
      </div>
    </div>
  );
}

export function CustomEndpointCard({
  endpoint,
  endpointKey,
  onSaveKey,
  onClearKey,
  onUpdate,
  onRemove,
}: {
  endpoint: CustomEndpoint;
  endpointKey: string | null;
  onSaveKey: (v: string) => Promise<void>;
  onClearKey: () => Promise<void>;
  onUpdate: (patch: Partial<CustomEndpoint>) => Promise<void>;
  onRemove: () => void;
}) {
  const detailsId = useId();
  const [expanded, setExpanded] = useState(!endpoint.baseURL.trim());
  const [nameDraft, setNameDraft] = useState(endpoint.name);
  const [urlDraft, setUrlDraft] = useState(endpoint.baseURL);
  const [modelDraft, setModelDraft] = useState(endpoint.modelId);
  const [contextDraft, setContextDraft] = useState(
    String(endpoint.contextLimit ?? ""),
  );
  const [keyDraft, setKeyDraft] = useState("");
  const [testError, setTestError] = useState("");
  const [discoveredModels, setDiscoveredModels] = useState<string[]>([]);
  const [testStatus, setTestStatus] = useState<
    "idle" | "testing" | "ok" | "fail"
  >("idle");

  useEffect(() => setNameDraft(endpoint.name), [endpoint.name]);
  useEffect(() => setUrlDraft(endpoint.baseURL), [endpoint.baseURL]);
  useEffect(() => setModelDraft(endpoint.modelId), [endpoint.modelId]);
  useEffect(
    () => setContextDraft(String(endpoint.contextLimit ?? "")),
    [endpoint.contextLimit],
  );

  const configured = !!endpoint.baseURL.trim() && !!endpoint.modelId.trim();

  const test = async () => {
    setTestStatus("testing");
    setTestError("");
    setDiscoveredModels([]);
    try {
      const oc = await import("@/modules/ai/lib/openclaw");
      if (endpoint.name === oc.OPENCLAW_ENDPOINT_NAME) {
        const normalized = oc.normalizeOpenClawBaseURL(urlDraft);
        const found = await oc.discoverOpenClawModels(normalized, endpointKey);
        const ids = found.map((m) => m.id);
        setDiscoveredModels(ids);
        if (found.length === 0) throw new Error("Gateway lists no agents or models");
        const chosen = ids.includes(endpoint.modelId) ? endpoint.modelId : ids[0];
        await onUpdate({ baseURL: normalized, modelId: chosen });
        setUrlDraft(normalized);
        setModelDraft(chosen);
        setTestStatus("ok");
        return;
      }
      const status = await invoke<number>("lm_ping", { baseUrl: urlDraft });
      if (status >= 200 && status < 300) {
        setTestStatus("ok");
      } else {
        setTestStatus("fail");
        setTestError(`Server responded with status ${status}`);
      }
    } catch (e: unknown) {
      setTestStatus("fail");
      setTestError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div className="flex flex-col rounded-lg border border-border/60 bg-card/60">
      <div className="flex items-center gap-2 px-3 py-2">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        aria-controls={detailsId}
        className="flex min-w-0 flex-1 items-center gap-2 text-left"
      >
        <HugeiconsIcon
          icon={ChevronDown}
          size={12}
          strokeWidth={2}
          className={cn(
            "shrink-0 text-muted-foreground/60 transition-transform",
            !expanded && "-rotate-90",
          )}
        />
        <ProviderIcon provider="openai-compatible" size={15} />
        <span className="text-[12.5px] font-medium truncate">
          {endpoint.name || "OpenAI Compatible"}
        </span>
        {endpoint.modelId.trim() && (
          <span className="text-[10.5px] text-muted-foreground truncate font-mono">
            {endpoint.modelId}
          </span>
        )}
        {configured ? (
          <Badge
            variant="outline"
            className="ml-1 h-4 gap-1 border-border/60 bg-muted/40 px-1.5 text-[10px] font-normal text-muted-foreground"
          >
            <HugeiconsIcon
              icon={CheckmarkCircle02Icon}
              size={9}
              strokeWidth={2}
            />
            {t("Configured")}{" "}
          </Badge>
        ) : null}
      </button>
        <Button
          type="button"
          size="icon"
          variant="ghost"
          onClick={onRemove}
          aria-label={t("Remove endpoint")}
          title={t("Remove endpoint")}
          className="ml-auto size-7 text-muted-foreground hover:text-destructive"
        >
          <HugeiconsIcon icon={Cancel01Icon} size={12} strokeWidth={1.75} />
        </Button>
      </div>

      {expanded && (
        <div id={detailsId} className="flex flex-col gap-2.5 border-t border-border/40 px-3 py-2.5">
          <FieldRow label={translateMessage("Name")}>{(inputId) => <>
            <Input id={inputId}
              value={nameDraft}
              onChange={(e) => setNameDraft(e.target.value)}
              onBlur={() => {
                const v = nameDraft.trim();
                if (v !== endpoint.name) void onUpdate({ name: v });
              }}
              placeholder={t("My endpoint")}
              spellCheck={false}
              className="h-8 flex-1 text-[11.5px]"
            />
          </>}</FieldRow>

          <FieldRow label={translateMessage("Base URL")}>{(inputId) => <>
            <div className="flex flex-1 gap-1.5">
              <Input id={inputId}
                value={urlDraft}
                onChange={(e) => setUrlDraft(e.target.value)}
                onBlur={() => {
                  const v = urlDraft.trim();
                  if (v !== endpoint.baseURL) void onUpdate({ baseURL: v });
                }}
                placeholder={t("https://api.example.com/v1")}
                spellCheck={false}
                className="h-8 flex-1 font-mono text-[11.5px]"
              />
              <Button
                size="sm"
                variant="outline"
                onClick={() => void test()}
                disabled={!urlDraft.trim()}
                className="h-8 px-3 text-[11px]"
              >
                {t("Test")}{" "}
              </Button>
            </div>
          </>}</FieldRow>

          <FieldRow label={translateMessage("Model ID")}>{(inputId) => <>
            <div className="flex flex-1 flex-col gap-1.5">
            <Input id={inputId}
              value={modelDraft}
              onChange={(e) => setModelDraft(e.target.value)}
              onBlur={() => {
                const v = modelDraft.trim();
                if (v !== endpoint.modelId) void onUpdate({ modelId: v });
              }}
              placeholder={t("gpt-4o, qwen3-max, glm-4.6, …")}
              spellCheck={false}
              className="h-8 font-mono text-[11.5px]"
            />
            {discoveredModels.length > 0 && (
              <Select
                value={
                  discoveredModels.includes(modelDraft)
                    ? modelDraft
                    : undefined
                }
                onValueChange={(value) => {
                  setModelDraft(value);
                  void onUpdate({ modelId: value });
                }}
              >
                <SelectTrigger
                  aria-label={t("Discovered models")}
                  className="h-8 text-[11.5px]"
                >
                  <SelectValue
                    placeholder={t("Select a discovered agent or model")}
                  />
                </SelectTrigger>
                <SelectContent>
                  {discoveredModels.map((id) => (
                    <SelectItem key={id} value={id}>
                      {id}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </div>
          </>}</FieldRow>

          <FieldRow label={translateMessage("Context")}>{(inputId) => <>
            <div className="flex flex-1 items-center gap-1.5">
              <Input id={inputId}
                value={contextDraft}
                onChange={(e) => setContextDraft(e.target.value)}
                onBlur={() => {
                  const v = parseInt(contextDraft);
                  if (Number.isFinite(v) && v >= 1000)
                    void onUpdate({ contextLimit: v });
                  else setContextDraft(String(endpoint.contextLimit ?? ""));
                }}
                placeholder="128000"
                spellCheck={false}
                className="h-8 w-28 font-mono text-[11.5px]"
              />
              <span className="text-[10.5px] text-muted-foreground">
                {t("tokens")}{" "}
              </span>
            </div>
          </>}</FieldRow>

          <FieldRow label={translateMessage("API key")} staticLabel={!!endpointKey}>{(inputId) => <>
            {endpointKey ? (
              <div className="flex flex-1 items-center gap-1.5">
                <code className="flex-1 truncate rounded bg-muted/40 px-2 py-1 font-mono text-[11px] text-muted-foreground">
                  {translateMessage("{0}{1}{2}", endpointKey.slice(0, 4), "•".repeat(8), endpointKey.slice(-4))}
                </code>
                <Button
                  size="icon"
                  variant="ghost"
                  onClick={() => void onClearKey()}
                  title={t("Remove key")}
                  className="size-7 text-muted-foreground hover:text-destructive"
                >
                  <HugeiconsIcon
                    icon={Cancel01Icon}
                    size={12}
                    strokeWidth={1.75}
                  />
                </Button>
              </div>
            ) : (
              <div className="flex flex-1 gap-1.5">
                <Input id={inputId}
                  type="password"
                  value={keyDraft}
                  onChange={(e) => setKeyDraft(e.target.value)}
                  placeholder={t(
                    "Optional — leave empty for unauthenticated endpoints",
                  )}
                  spellCheck={false}
                  className="h-8 flex-1 font-mono text-[11.5px]"
                />
                <Button
                  size="sm"
                  onClick={async () => {
                    const v = keyDraft.trim();
                    if (!v) return;
                    await onSaveKey(v);
                    setKeyDraft("");
                  }}
                  disabled={!keyDraft.trim()}
                  className="h-8 px-3 text-[11px]"
                >
                  {t("Save")}{" "}
                </Button>
              </div>
            )}
          </>}</FieldRow>

          <StatusLine status={testStatus} error={testError} />
        </div>
      )}
    </div>
  );
}

function FieldRow({
  label,
  children,
  staticLabel = false,
}: {
  label: string;
  children: (inputId: string) => React.ReactNode;
  staticLabel?: boolean;
}) {
  const inputId = useId();
  const LabelTag = staticLabel ? "span" : "label";
  return (
    <div className="flex items-center gap-3">
      <LabelTag htmlFor={staticLabel ? undefined : inputId} className="w-16 shrink-0 text-[11px] tracking-tight text-muted-foreground">
        {label}
      </LabelTag>
      <div className="flex flex-1 items-center">{children(inputId)}</div>
    </div>
  );
}

function StatusLine({
  status,
  error,
}: {
  status: "idle" | "testing" | "ok" | "fail";
  error?: string;
}) {
  if (status === "idle") return null;
  if (status === "testing") {
    return (
      <span className="text-[10.5px] text-muted-foreground">
        {t("Testing…")}
      </span>
    );
  }
  if (status === "ok") {
    return (
      <span className="flex items-center gap-1 text-[10.5px] text-muted-foreground">
        <HugeiconsIcon icon={CheckmarkCircle02Icon} size={11} strokeWidth={2} />
        {t("Reachable — server responded.")}{" "}
      </span>
    );
  }
  return (
    <span role="status" className="text-[10.5px] text-destructive/80">
      {error || t("Could not reach the server.")}{" "}
    </span>
  );
}

function VoiceBlock({ setup, keys }: { setup: boolean; keys: KeysMap }) {
  const sttProvider = usePreferencesStore((s) => s.sttProvider);
  const groqSttModel = usePreferencesStore((s) => s.groqSttModel);
  const whispercppBaseURL = usePreferencesStore((s) => s.whispercppBaseURL);
  const [urlDraft, setUrlDraft] = useState(whispercppBaseURL);
  const [groqModelDraft, setGroqModelDraft] = useState(groqSttModel);
  const [health, setHealth] = useState<{ url: string; ready: boolean }>();
  const [healthError, setHealthError] = useState("");
  const [healthTick, setHealthTick] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setHealthError("");
    void whisperCppReady(whispercppBaseURL, controller.signal).then(ready => {
      if (!controller.signal.aborted) setHealth({ url: whispercppBaseURL, ready });
    }).catch(error => {
      if (!controller.signal.aborted) {
        setHealth({ url: whispercppBaseURL, ready: false });
        setHealthError(String(error));
      }
    });
    return () => controller.abort();
  }, [whispercppBaseURL, healthTick]);
  const availableProviders = getAvailableSttProviders(keys, health?.url === whispercppBaseURL && health.ready);

  useEffect(() => setUrlDraft(whispercppBaseURL), [whispercppBaseURL]);
  useEffect(() => setGroqModelDraft(groqSttModel), [groqSttModel]);

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border/60 bg-card/60 px-3 py-2.5">
      <div className="flex items-center gap-2">
        <HugeiconsIcon icon={Mic01Icon} size={15} strokeWidth={1.5} />
        <span className="text-[12.5px] font-medium">{t("Voice input")}</span>
      </div>

      {!setup && <>
      <FieldRow label={translateMessage("Provider")}>{(inputId) => <>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button id={inputId}
              variant="outline"
              className="h-8 flex-1 justify-between gap-2 px-2.5 text-[11.5px]"
            >
              <span>{availableProviders.includes(sttProvider) ? t(STT_PROVIDER_LABELS[sttProvider]) : t("Selected provider is unavailable")}</span>
              <HugeiconsIcon
                icon={ArrowDown01Icon}
                size={11}
                strokeWidth={2}
                className="opacity-70"
              />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="min-w-44 p-1">
            {availableProviders.map((p) => (
              <DropdownMenuItem
                key={p}
                onSelect={() => void setSttProvider(p)}
                className={cn(
                  "flex items-center gap-2 text-[12px]",
                  p === sttProvider && "bg-accent/50",
                )}
              >
                <span>{t(STT_PROVIDER_LABELS[p])}</span>
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      </>}</FieldRow>

      <p className="text-[10.5px] leading-relaxed text-muted-foreground">
        {sttProvider === "jarvis" &&
          "Uses shared Jarvis speech recognition. Enable the microphone in the footer; the transcript is inserted into the prompt."}
        {sttProvider === "openai" &&
          "Uses your official OpenAI API key and the Whisper model for transcription."}
        {sttProvider === "groq" &&
          "Uses your official Groq API key and Groq's Whisper endpoint for transcription."}
        {sttProvider === "whispercpp" &&
          "Connects to a local Whisper.cpp server for fully offline transcription."}
      </p>

      <Button variant="outline" onClick={() => void openSettingsWindow("integrations")}>{t("Configure speech providers in Integrations.")}</Button>
      </>}

      {setup && (
        <div className="flex flex-col gap-2.5">
          <FieldRow label={translateMessage("Model")}>{(inputId) => <>
            <Input id={inputId}
              value={groqModelDraft}
              onChange={(e) => setGroqModelDraft(e.target.value)}
              onBlur={() => {
                const v = groqModelDraft.trim();
                if (v !== groqSttModel) void setGroqSttModel(v);
              }}
              placeholder={t("whisper-large-v3-turbo")}
              spellCheck={false}
              className="h-8 font-mono text-[11.5px]"
            />
          </>}</FieldRow>
        </div>
      )}

      {setup && (
        <div className="flex flex-col gap-2.5">
          <FieldRow label={translateMessage("Base URL")}>{(inputId) => <>
            <Input id={inputId}
              aria-label={t("Whisper.cpp base URL")}
              value={urlDraft}
              onChange={(e) => setUrlDraft(e.target.value)}
              onBlur={() => {
                const v = urlDraft.trim();
                if (v !== whispercppBaseURL) void setWhispercppBaseURL(v);
              }}
              placeholder={WHISPERCPP_DEFAULT_BASE_URL}
              spellCheck={false}
              className="h-8 font-mono text-[11.5px]"
            />
          </>}</FieldRow>
          <Button variant="outline" onClick={() => setHealthTick(value => value + 1)}>{t("Check Whisper.cpp connection")}</Button>
          <p role="status" className="text-xs text-muted-foreground">{t(health?.url === whispercppBaseURL && health.ready ? "Whisper.cpp is ready" : "Whisper.cpp is unavailable or loading")}</p>
          {healthError && <p className="text-xs text-muted-foreground">{healthError}</p>}
        </div>
      )}
    </div>
  );
}

function Label({ children }: { children: React.ReactNode }) {
  return (
    <span className="text-[11px] font-medium tracking-tight text-muted-foreground">
      {children}
    </span>
  );
}

function GoogleFallbackSetting() {
  const [config, setConfig] = useState<{
    model: string;
    auto_fallback: boolean;
  } | null>(null);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    void invoke<{ model: string; auto_fallback: boolean }>("backend", {
      method: "snapshot",
      params: {},
    })
      .then((value) => {
        if (active) setConfig(value);
      })
      .catch((error) => {
        if (active) setError(String(error));
      });
    return () => {
      active = false;
    };
  }, []);
  const change = async (value: boolean) => {
    if (!config) return;
    setBusy(true);
    setError("");
    try {
      await invoke("backend", {
        method: "settings.models",
        params: { model: config.model, auto_fallback: value },
      });
      setConfig({ ...config, auto_fallback: value });
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="rounded-xl border border-border/60 bg-card/60 p-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="text-sm font-medium">{t("Google model fallback")}</h3>
          <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
            {t(
              "Try another supported Google model on quota or availability errors. Applies only to Google chat requests.",
            )}
          </p>
        </div>
        <Switch
          aria-label={t("Google model fallback")}
          checked={config?.auto_fallback ?? false}
          disabled={!config || busy}
          onCheckedChange={(value) => void change(value)}
        />
      </div>
      {error && (
        <p role="alert" className="mt-2 text-xs text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}


function LlamaSetting() {
  const [model, setModel] = useState("");
  const [projector, setProjector] = useState("");
  const [mode, setMode] = useState("vulkan");
  const [ready, setReady] = useState(false);
  const [running, setRunning] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    void invoke<{model: string; projector: string; mode: string; running: boolean; device: string}>("backend", {method: "llama.status", params: {}})
      .then(state => {
        if (!active) return;
        setModel(state.model); setProjector(state.projector); setMode(state.mode);
        setRunning(state.running); setReady(true);
        setMessage(state.running ? `llama.cpp: ${state.device === "none" ? "CPU" : state.device}` : t("Stopped"));
      })
      .catch(error => { if (active) setError(String(error)); });
    return () => { active = false; };
  }, []);
  const operate = async (start: boolean) => {
    setBusy(true); setError("");
    setMessage(t(start ? "Loading local model…" : "Stopping local model…"));
    try {
      const result = await invoke<{running: boolean; device: string; baseURL: string; context: number}>("backend", {
        method: start ? "llama.start" : "llama.stop", params: start ? {model: model.trim(), projector: projector.trim(), mode} : {},
      });
      setRunning(result.running);
      if (result.running) {
        const endpoints = usePreferencesStore.getState().customEndpoints;
        const endpoint = {id: "jarvis-llama", name: "llama.cpp", baseURL: result.baseURL, modelId: "jarvis-local", contextLimit: result.context};
        await setCustomEndpoints([...endpoints.filter(e => e.id !== endpoint.id), endpoint]);
        await setDefaultModel(compatModelIdForEndpoint(endpoint.id) as ModelId);
      }
      setMessage(result.running ? `llama.cpp: ${result.device === "none" ? "CPU" : result.device}` : t("Stopped"));
    } catch (error) { setError(String(error)); setMessage(""); }
    finally { setBusy(false); }
  };
  const valid = /\.gguf$/i.test(model.trim()) && (!projector.trim() || /\.gguf$/i.test(projector.trim()));
  return <details className="rounded-xl border border-border/60 bg-card/60 p-4" data-llama-settings>
    <summary className="cursor-pointer text-sm font-medium">{t("Local model · llama.cpp")}<span className="ml-2 text-xs text-muted-foreground">{t(running ? "Running" : "Stopped")}</span></summary>
    <div className="mt-4 flex flex-col gap-4" aria-busy={busy}>
      <p className="text-xs leading-relaxed text-muted-foreground">{t("Run a GGUF model on this PC. Starting selects it for the shared chat and tools. No cloud key is required.")}</p>
      <label className="flex flex-col gap-2 text-sm">{t("Chat model (.gguf)")}
        <Input aria-label={t("Chat GGUF path")} maxLength={2048} placeholder={translateMessage("D:\\\\Models\\\\model.gguf")} value={model} disabled={!ready || busy} onChange={e => setModel(e.target.value)} />
      </label>
      <label className="flex flex-col gap-2 text-sm">{t("Vision projector (optional)")}
        <Input aria-label={t("Vision projector GGUF path")} maxLength={2048} placeholder={translateMessage("D:\\\\Models\\\\mmproj.gguf")} value={projector} disabled={!ready || busy} onChange={e => setProjector(e.target.value)} />
        <span className="text-xs text-muted-foreground">{t("Use the projector that matches your chat model. Leave empty for text-only models.")}</span>
      </label>
      <label className="flex flex-col gap-2 text-sm">{t("Compute device")}
        <select aria-label={t("llama.cpp device")} value={mode} disabled={!ready || busy} onChange={e => setMode(e.target.value)} className="bg-background text-foreground rounded-md border p-2">
          <option value="vulkan">{t("Vulkan — GPU required (no CPU fallback)")}</option><option value="cpu">{translateMessage("CPU")}</option>
        </select>
      </label>
      <p className="text-xs leading-relaxed text-muted-foreground">{t("Context: 8192 tokens. Models stay on disk when stopped. A new chat request loads the selected local model again.")}</p>
      <div className="flex flex-wrap gap-2">
        <Button disabled={!ready || busy || !valid} onClick={() => void operate(true)}>{t(running ? "Reload & use model" : "Start & use model")}</Button>
        <Button disabled={!ready || busy || !running} variant="outline" onClick={() => void operate(false)}>{t("Unload model")}</Button>
      </div>
      {message && <p role="status" className="text-xs text-muted-foreground">{message}</p>}
      {error && <p role="alert" className="text-xs text-destructive break-words">{error}</p>}
    </div>
  </details>;
}
