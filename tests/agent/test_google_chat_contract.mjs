import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { fileURLToPath } from "node:url";
// Import the actual installed pnpm package with its dependency links intact.
const nodeModules = fileURLToPath(new URL("../../apps/desktop/node_modules/", import.meta.url));
const version = JSON.parse(readFileSync(`${nodeModules}/@ai-sdk/google/package.json`, "utf8")).version;
const packageDir = readdirSync(`${nodeModules}/.pnpm`).find(name => name.startsWith(`@ai-sdk+google@${version}_`));
assert.ok(packageDir, "installed Google SDK dependency graph");
const { createGoogle } = await import(pathToFileURL(`${nodeModules}/.pnpm/${packageDir}/node_modules/@ai-sdk/google/dist/index.js`).href);
const prompt = [{ role: "user", content: [{ type: "text", text: "offline fixture" }] }];
const sse = value => new Response(`data: ${JSON.stringify(value)}\n\n`, { headers: { "content-type": "text/event-stream" } });

test("actual Google stream parser preserves broker deadline error as readable provider error", async () => {
  let calls = 0;
  const model = createGoogle({ apiKey: "offline-fixture", fetch: async () => {
    calls++;
    return sse({ error: { code: 504, status: "DEADLINE_EXCEEDED", message: "Gemini did not respond before the deadline" } });
  } })("gemini-3.8-flash");
  const { stream } = await model.doStream({ prompt });
  const parts = await Array.fromAsync(stream);
  const errors = parts.filter(part => part.type === "error");
  assert.equal(calls, 1);
  assert.equal(errors.length, 1);
  assert.equal(errors[0].error.message, "Gemini did not respond before the deadline");
  assert.doesNotMatch(String(errors[0].error), /TypeValidationError|Type validation failed/);
});

test("actual Gemini 3.8 SDK serializes the interactive low thinking option", async () => {
  let payload;
  const model = createGoogle({ apiKey: "offline-fixture", fetch: async (_input, init) => {
    payload = JSON.parse(init.body);
    return sse({ candidates: [{ content: { role: "model", parts: [{ text: "fixture" }] }, finishReason: "STOP" }] });
  } })("gemini-3.8-flash");
  const { stream } = await model.doStream({ prompt, providerOptions: { google: { thinkingConfig: { thinkingLevel: "low" } } } });
  const parts = await Array.fromAsync(stream);
  assert.equal(parts.some(part => part.type === "error"), false);
  assert.equal(payload.generationConfig.thinkingConfig.thinkingLevel, "low");
  assert.equal(payload.contents[0].parts[0].text, "offline fixture");
});
