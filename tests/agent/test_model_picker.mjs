import test from "node:test";
import assert from "node:assert/strict";
import {
  buildLmstudioModelInfos,
  buildOllamaModelInfos,
  ollamaOptionId,
  localAutocompleteModelId,
  compatModelIdForEndpoint,
  filterChatLocalCatalog,
  filterChatModelIds,
  getConfiguredChatModels,
  getAvailableSttProviders,
  getAutocompleteEligibleModels,
  isEmbeddingModelId,
  isLmstudioOptionId,
  isModelSelectionConfigured,
  isProviderConfigured,
  isValidStoredModelId,
  lmstudioKeyFromOptionId,
  lmstudioOptionId,
  MODELS,
  PROVIDERS,
} from "../../apps/desktop/workbench/src/modules/ai/config.ts";

const LIVE_FIXTURE = {
      models: [
        {
          type: "llm",
          key: "gpt-oss-20b",
          display_name: "GPT-OSS 20B",
          loaded_instances: [],
          max_context_length: 131072,
          capabilities: { vision: false, trained_for_tool_use: true },
        },
        {
          type: "llm",
          key: "prism-ml/bonsai-27b",
          display_name: "Bonsai 27B",
          loaded_instances: [],
          max_context_length: 262144,
          capabilities: {
            vision: true,
            trained_for_tool_use: true,
            reasoning: { allowed_options: ["off", "on"], default: "on" },
          },
        },
        {
          type: "llm",
          key: "qwen/qwen3.5-9b",
          display_name: "Qwen3.5 9B",
          loaded_instances: [],
          max_context_length: 262144,
          capabilities: {
            vision: true,
            trained_for_tool_use: true,
            reasoning: { allowed_options: ["off", "on"], default: "on" },
          },
        },
        {
          type: "llm",
          key: "google/gemma-4-e4b",
          display_name: "Gemma 4 E4B",
          loaded_instances: [],
          max_context_length: 131072,
          capabilities: {
            vision: true,
            trained_for_tool_use: true,
            reasoning: { allowed_options: ["off", "on"], default: "on" },
          },
        },
        {
          type: "embedding",
          key: "text-embedding-nomic-embed-text-v1.5",
          display_name: "Nomic Embed Text v1.5",
          loaded_instances: [],
          max_context_length: 2048,
        },
      ],
    };

test("LM Studio installed catalogue keeps unloaded LLMs selectable and excludes embedding models", () => {
  const rawModels = LIVE_FIXTURE.models;
  const filtered = filterChatLocalCatalog(rawModels);
  assert.deepEqual(
    filtered.map((m) => m.key),
    [
      "gpt-oss-20b",
      "prism-ml/bonsai-27b",
      "qwen/qwen3.5-9b",
      "google/gemma-4-e4b",
    ],
  );
  assert.equal(
    filtered.some((m) => m.key.includes("nomic-embed")),
    false,
  );
  assert.equal(
    isEmbeddingModelId("text-embedding-nomic-embed-text-v1.5"),
    true,
  );

  const projected = buildLmstudioModelInfos(rawModels, [], "");
  assert.equal(projected.length, 4);
  assert.deepEqual(
    projected.map((m) => m.label),
    ["GPT-OSS 20B", "Bonsai 27B", "Qwen3.5 9B", "Gemma 4 E4B"],
  );
  for (const item of projected) {
    assert.equal(item.provider, "lmstudio");
    assert.equal(isLmstudioOptionId(item.id), true);
    assert.equal(item.hint, "LM Studio");
    assert.match(item.description, /Downloaded · loads on first use/);
  }
  assert.deepEqual(projected[0].tags, ["tools"]);
  assert.deepEqual(projected[1].tags, ["vision", "tools", "reasoning"]);
});

test("chat model picker shows only configured usable models while Settings retains full catalogue", () => {
  // Settings catalogue remains intact
  assert.ok(PROVIDERS.length >= 13);
  assert.ok(MODELS.length >= 35);

  // Empty state when nothing is configured
  const emptyState = {
    apiKeys: {},
    lmstudioBaseURL: "http://localhost:1234/v1",
    lmstudioModelId: "",
    lmstudioCatalog: [],
    lmstudioDetectedIds: [],
    mlxBaseURL: "http://127.0.0.1:8080/v1",
    mlxModelId: "",
    ollamaBaseURL: "http://localhost:11434/v1",
    ollamaModelId: "",
    openrouterModelId: "",
    customEndpoints: [],
  };
  assert.deepEqual(getConfiguredChatModels(emptyState), []);
  assert.equal(
    isModelSelectionConfigured("gemini-3.5-flash-lite", emptyState),
    false,
  );

  // When LM Studio has unloaded installed models, only LM Studio chat models appear
  const lmOnlyState = {
    ...emptyState,
    lmstudioCatalog: LIVE_FIXTURE.models,
    lmstudioModelId: "google/gemma-4-e4b",
  };
  const lmConfigured = getConfiguredChatModels(lmOnlyState);
  assert.equal(lmConfigured.length, 4);
  assert.ok(lmConfigured.every((m) => m.provider === "lmstudio"));
  assert.equal(
    lmConfigured.some((m) => m.id === "openai-compatible-custom"),
    false,
  );
  assert.equal(
    isModelSelectionConfigured("lmstudio-local", lmOnlyState),
    true,
  );
  assert.equal(
    isModelSelectionConfigured(
      lmstudioOptionId("prism-ml/bonsai-27b"),
      lmOnlyState,
    ),
    true,
  );
  assert.equal(
    lmstudioKeyFromOptionId(lmstudioOptionId("prism-ml/bonsai-27b")),
    "prism-ml/bonsai-27b",
  );
});

test("configured models react to added/removed providers and endpoints without silent unavailable fallback", () => {
  const endpoint = {
    id: "ep1",
    name: "Local vLLM",
    baseURL: "http://127.0.0.1:8000/v1",
    modelId: "Qwen/Qwen2.5-Coder-7B",
    contextLimit: 32768,
  };
  const stateWithOpenAiAndCompat = {
    apiKeys: { openai: "sk-test-key", openrouter: "sk-or-test" },
    lmstudioBaseURL: "http://localhost:1234/v1",
    lmstudioModelId: "",
    lmstudioCatalog: [],
    lmstudioDetectedIds: filterChatModelIds([
      "google/gemma-4-e4b",
      "text-embedding-nomic-embed-text-v1.5",
    ]),
    mlxBaseURL: "http://127.0.0.1:8080/v1",
    mlxModelId: "",
    ollamaBaseURL: "http://localhost:11434/v1",
    ollamaModelId: "qwen2.5-coder:7b",
    openrouterModelId: "", // OpenRouter key set, but model ID missing -> not configured
    customEndpoints: [endpoint],
  };

  assert.equal(isProviderConfigured("openai", stateWithOpenAiAndCompat), true);
  assert.equal(
    isProviderConfigured("openrouter", stateWithOpenAiAndCompat),
    false,
  );
  assert.equal(isProviderConfigured("ollama", stateWithOpenAiAndCompat), true);
  assert.equal(
    isProviderConfigured("lmstudio", stateWithOpenAiAndCompat),
    true,
  );
  assert.equal(isProviderConfigured("google", stateWithOpenAiAndCompat), false);

  const models = getConfiguredChatModels(stateWithOpenAiAndCompat);
  const providers = new Set(models.map((m) => m.provider));
  assert.deepEqual(
    providers,
    new Set(["openai", "lmstudio", "ollama", "openai-compatible"]),
  );
  assert.equal(
    models.some((m) => m.id === "openai-compatible-custom"),
    false,
  );
  assert.equal(
    models.some((m) => m.id === compatModelIdForEndpoint("ep1")),
    true,
  );
  assert.equal(
    isValidStoredModelId(compatModelIdForEndpoint("ep1")),
    true,
  );

  // Removing OpenAI key immediately drops OpenAI models and marks selection unconfigured
  const afterRemoveOpenAi = {
    ...stateWithOpenAiAndCompat,
    apiKeys: {},
  };
  assert.equal(isModelSelectionConfigured("gpt-5.6", afterRemoveOpenAi), false);
  assert.equal(
    getConfiguredChatModels(afterRemoveOpenAi).some(
      (m) => m.provider === "openai",
    ),
    false,
  );
});

test("typed native catalogue boundary accepts llm entries only", () => {
  const entry = (type, key) => ({ type, key, display_name: key, loaded_instances: [] });
  const kept = filterChatLocalCatalog([
    entry("llm", "chat-a"),
    entry("embedding", "emb-a"),
    entry("decision", "decide-a"),
    entry(undefined, "untyped-a"),
    entry("llm", "   "),
    entry("llm", "text-embedding-foo"),
    entry("llm", "chat-b"),
  ]);
  assert.deepEqual(
    kept.map((m) => m.key),
    ["chat-a", "chat-b"],
  );
  assert.deepEqual(filterChatLocalCatalog([]), []);
});


test("autocomplete eligibility hides unconfigured providers and follows credential removal", () => {
  assert.deepEqual(getAutocompleteEligibleModels(new Set()), []);
  const configured = new Set(["google"]);
  const models = getAutocompleteEligibleModels(configured);
  assert.ok(models.length > 0);
  assert.ok(models.every((model) => model.provider === "google"));
  configured.delete("google");
  assert.deepEqual(getAutocompleteEligibleModels(configured), []);
});


test("Ollama installed catalogue includes unloaded choices and excludes missing/stale selections", () => {
  const rows = [
    {id: "chat:latest", loaded: true, context_length: 8192},
    {id: "unloaded:small", loaded: false, context_length: null},
    {id: "nomic-embed-text", loaded: false, context_length: null},
  ];
  const state = {apiKeys: {}, ollamaBaseURL: "http://localhost:11434/v1", ollamaModelId: "deleted:old", ollamaCatalog: rows};
  assert.deepEqual(getConfiguredChatModels(state).map(m => m.id), [ollamaOptionId("chat:latest"), ollamaOptionId("unloaded:small")]);
  assert.equal(isModelSelectionConfigured(ollamaOptionId("deleted:old"), state), false);
  assert.equal(isModelSelectionConfigured("ollama-local", state), false);
  assert.equal(isModelSelectionConfigured(ollamaOptionId("unloaded:small"), state), true);
  assert.match(buildOllamaModelInfos(rows)[1].description, /loads on first use/);
  assert.deepEqual(getConfiguredChatModels({...state, ollamaCatalog: []}), []);
});

test("local autocomplete preserves legacy generic selections and independent model keys", () => {
  for (const provider of ["lmstudio", "ollama"]) {
    assert.equal(localAutocompleteModelId(provider, "", "shared"), "shared");
    assert.equal(localAutocompleteModelId(provider, `${provider}-local`, "shared"), "shared");
    assert.equal(localAutocompleteModelId(provider, `${provider}:independent`, "shared"), "independent");
    assert.equal(localAutocompleteModelId(provider, "independent", "shared"), "independent");
  }
});


test("speech input choices require keys or a ready local server", () => {
  assert.deepEqual(getAvailableSttProviders({}, false), ["jarvis"]);
  assert.deepEqual(getAvailableSttProviders({openai: " ", groq: null}, false), ["jarvis"]);
  assert.deepEqual(getAvailableSttProviders({openai: "key"}, true), ["jarvis", "openai", "whispercpp"]);
  assert.deepEqual(getAvailableSttProviders({groq: "key"}, false), ["jarvis", "groq"]);
});
