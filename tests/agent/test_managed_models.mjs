import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { stripTypeScriptTypes } from "node:module";
import { getConfiguredChatModels, isModelSelectionConfigured, isValidStoredModelId } from "../../apps/desktop/workbench/src/modules/ai/config.ts";

const configUrl = new URL("../../apps/desktop/workbench/src/modules/ai/config.ts", import.meta.url).href;
async function loadManaged(rpc) {
  const key = `managedTest${crypto.randomUUID().replaceAll("-", "")}`;
  globalThis[key] = rpc;
  let source = await readFile(new URL("../../apps/desktop/workbench/src/modules/ai/lib/managedModels.ts", import.meta.url), "utf8");
  source = source.replace('import { invoke } from "@/host/core";', `const invoke = globalThis[${JSON.stringify(key)}];`)
    .replaceAll('from "../config"', `from ${JSON.stringify(configUrl)}`);
  return import(`data:text/javascript;base64,${Buffer.from(stripTypeScriptTypes(source)).toString("base64")}`);
}
const record = (repo, revision = "a".repeat(40), status = "unloaded") => ({
  model_id: `jarvis-managed:llm:${repo}@${revision}`, repo_id: repo, revision, kind: "llm", status,
  loaded: status === "loaded", context: 8192,
});

test("managed available-only catalogue retains independent revision identities and excludes unsupported metadata", () => {
  const one = record("owner/one");
  const two = record("owner/one", "b".repeat(40), "loaded");
  const state = { apiKeys: {}, managedModels: [one, two, { ...one, status: "unsupported" }, { ...one, model_id: "jarvis-managed:llm:other/repo@rev1" }, { ...one, status: undefined }] };
  assert.deepEqual(getConfiguredChatModels(state).map(model => model.id), [one.model_id, two.model_id]);
  assert.equal(isModelSelectionConfigured(one.model_id, state), true);
  assert.equal(isModelSelectionConfigured("jarvis-managed:llm:missing/repo@rev1", state), false);
  assert.equal(isValidStoredModelId(one.model_id), true);
  assert.equal(isValidStoredModelId("jarvis-managed:llm:owner/one@rev1"), false);
});

test("metadata discovery shares concurrent calls and never resolves or loads models", async () => {
  const calls = [];
  const model = record("owner/one");
  const module = await loadManaged(async (command, args) => { calls.push(args); return { models: [model, { ...model, status: "unsupported" }] }; });
  const [first, second] = await Promise.all([module.discoverManagedModels(), module.discoverManagedModels()]);
  assert.deepEqual(first, [model]); assert.deepEqual(second, [model]);
  assert.deepEqual(calls.map(call => call.method), ["managed_chat.list"]);
});

test("distinct inference requests resolve their exact stable identities and isolated URLs", async () => {
  const calls = [];
  const models = [record("owner/one"), record("owner/two")];
  const module = await loadManaged(async (command, args) => {
    calls.push(args);
    const index = models.findIndex(model => model.model_id === args.params.model_id);
    return { ...models[index], base_url: `http://127.0.0.1:${18100 + index}/v1` };
  });
  const targets = [];
  const fakeFetch = async (url, init) => { targets.push([String(url), JSON.parse(init.body).model]); return new Response("ok"); };
  const responses = await Promise.all(models.map(model => module.managedModelFetch(model.model_id, fakeFetch)("http://127.0.0.1/v1/chat/completions", { method: "POST", body: JSON.stringify({ model: model.model_id }) })));
  await Promise.all(responses.map(response => response.text()));
  assert.deepEqual(targets, models.map((model, index) => [`http://127.0.0.1:${18100 + index}/v1/chat/completions`, model.model_id]));
  assert.equal(new Set(calls.map(call => call.params.request_id)).size, 2);
});

test("abort during lazy resolution cancels only the matching request and never invokes inference", async () => {
  const controller = new AbortController();
  const calls = [];
  let finish;
  let started;
  const ready = new Promise(resolve => { started = resolve; });
  const model = record("owner/one");
  const module = await loadManaged(async (command, args) => {
    calls.push(args);
    if (args.method === "managed_chat.resolve") { started(); return new Promise(resolve => { finish = resolve; }); }
    return {};
  });
  let fetched = false;
  const request = module.managedModelFetch(model.model_id, async () => { fetched = true; return new Response("unexpected"); }, controller.signal)("http://127.0.0.1/v1/chat/completions");
  await ready;
  controller.abort();
  finish({ ...model, base_url: "http://127.0.0.1:18100/v1" });
  await assert.rejects(request, error => error.name === "AbortError");
  assert.equal(fetched, false);
  const resolved = calls.find(call => call.method === "managed_chat.resolve");
  const cancellations = calls.filter(call => call.method === "managed_chat.cancel");
  assert.ok(cancellations.length > 0);
  assert.ok(cancellations.every(call => call.params.request_id === resolved.params.request_id));
});

test("inference rejects a different resolved model rather than silently switching weights", async () => {
  const one = record("owner/one");
  const module = await loadManaged(async (command, args) => args.method === "managed_chat.resolve" ? { ...record("owner/two"), base_url: "http://127.0.0.1:18101/v1" } : {});
  let fetched = false;
  await assert.rejects(module.managedModelFetch(one.model_id, async () => { fetched = true; return new Response("unexpected"); })("http://127.0.0.1/v1/chat/completions"), /identity changed/);
  assert.equal(fetched, false);
});

test("managed-only installation exposes the composer only for its selected available bundle", async () => {
  const path = new URL("../../apps/desktop/workbench/src/modules/ai/hooks/useAiBootstrap.ts", import.meta.url);
  const source = (await readFile(path, "utf8")).replace(/^import[\s\S]*?from "[^"]+";\r?$/gm, "");
  async function composer(selectedModelId, models) {
    const key = `bootstrapTest${crypto.randomUUID().replaceAll("-", "")}`;
    globalThis[key] = { selectedModelId, models };
    const prelude = `import { isModelSelectionConfigured } from ${JSON.stringify(configUrl)};
      const fixture = globalThis[${JSON.stringify(key)}];
      const useEffect = () => {};
      const useState = () => [false, () => {}];
      const useChatStore = selector => selector({apiKeys:{}, selectedModelId:fixture.selectedModelId});
      const useManagedModelCatalog = () => fixture.models;
      const hasAnyKey = () => false;
      const preferences = {lmstudioBaseURL:"",lmstudioModelId:"",mlxBaseURL:"",mlxModelId:"",ollamaBaseURL:"",ollamaModelId:"",openaiCompatibleBaseURL:"",openaiCompatibleModelId:"",customEndpoints:[],hydrated:true};
      const usePreferencesStore = selector => selector(preferences);
    `;
    const module = await import(`data:text/javascript;base64,${Buffer.from(prelude + stripTypeScriptTypes(source)).toString("base64")}`);
    return module.useAiBootstrap().hasComposer;
  }
  const model = record("owner/only-managed");
  assert.equal(await composer(model.model_id, [model]), true);
  assert.equal(await composer(model.model_id, [{ ...model, status: "loaded", loaded: true }]), true);
  assert.equal(await composer(model.model_id, []), false);
  assert.equal(await composer(model.model_id, [{ ...model, status: "unsupported" }]), false);
  assert.equal(await composer(record("owner/missing").model_id, [model]), false);
});

test("readiness managed lifecycle invalidation rejects stale metadata from expired discovery", async () => {
  const pending = [];
  const module = await loadManaged(() => new Promise((resolve, reject) => pending.push({resolve, reject})));
  const model = {...record('owner/stale'), context:65536};
  const old = module.discoverManagedModels();
  module.invalidateManagedModelDiscovery();
  const current = module.discoverManagedModels();
  assert.equal(pending.length, 2);
  pending[1].resolve({models:[]}); await current;
  pending[0].resolve({models:[model]}); await old;
  assert.equal(module.managedModelContext(model.model_id), 8192);
  const loaded = module.discoverManagedModels();
  pending[2].resolve({models:[model]}); await loaded;
  assert.equal(module.managedModelContext(model.model_id), 65536);
  const failed = module.discoverManagedModels();
  pending[3].reject(new Error('synthetic server unavailable'));
  await assert.rejects(failed, /unavailable/);
  assert.equal(module.managedModelContext(model.model_id), 8192);
});
