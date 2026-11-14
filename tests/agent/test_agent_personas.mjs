import test from "node:test";
import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import en from "../../apps/desktop/src/locales/en.json" with { type: "json" };
import ru from "../../apps/desktop/src/locales/ru.json" with { type: "json" };
const state = { entries: [] };
globalThis.__agentPersonaFixture = state;
const stub = `export class LazyStore { async entries() { return globalThis.__agentPersonaFixture.entries; } }`;
const hook = registerHooks({ resolve(specifier, context, nextResolve) {
  if (specifier === "@/host/store" && context.parentURL?.endsWith("/lib/agents.ts"))
    return { url: `data:text/javascript,${encodeURIComponent(stub)}`, shortCircuit: true };
  return nextResolve(specifier, context);
} });
const { BUILTIN_AGENTS, loadAgents, findAgent } = await import("../../apps/desktop/workbench/src/modules/ai/lib/agents.ts");
hook.deregister();

test("new profiles default to general Assistant; explicit Coder/custom selections survive", async () => {
  const assistant = BUILTIN_AGENTS[0];
  assert.equal(assistant.id, "builtin:assistant");
  assert.equal(assistant.icon, "spark");
  assert.equal((await loadAgents()).activeId, assistant.id);
  assert.equal(findAgent(BUILTIN_AGENTS, undefined), assistant);
  assert.equal(findAgent(BUILTIN_AGENTS, "removed"), assistant);
  const coder = BUILTIN_AGENTS.find(agent => agent.id === "builtin:coder");
  assert.ok(coder);
  state.entries = [["activeAgentId", coder.id]];
  assert.equal((await loadAgents()).activeId, coder.id);
  assert.equal(findAgent(BUILTIN_AGENTS, coder.id), coder);
  const custom = { ...coder, id: "custom:fixture", name: "My agent", builtIn: false };
  state.entries = [["activeAgentId", custom.id], ["customAgents", [custom]]];
  const loaded = await loadAgents();
  assert.equal(loaded.activeId, custom.id);
  assert.equal(findAgent([...BUILTIN_AGENTS, ...loaded.custom], loaded.activeId), custom);
  for (const message of [assistant.name, assistant.description]) {
    assert.equal(en[message], message);
    assert.equal(typeof ru[message], "string");
  }
  assert.equal(BUILTIN_AGENTS.length, 6);
});
