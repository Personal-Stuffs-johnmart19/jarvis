import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createPreferenceWriteQueue } from "../../apps/desktop/workbench/src/modules/settings/preferenceWriteQueue.ts";

const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};

test("rapid managed-to-LM selection waits for delayed writes and keeps the latest default", async () => {
  const enqueue = createPreferenceWriteQueue();
  const firstSave = deferred();
  const secondSave = deferred();
  const started = [];
  let stored = "lmstudio-local";
  let secondComplete = false;
  const first = enqueue(async () => { started.push("managed"); await firstSave.promise; stored = "managed"; });
  const second = enqueue(async () => { started.push("lmstudio-local"); await secondSave.promise; stored = "lmstudio-local"; }).then(() => { secondComplete = true; });
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(started, ["managed"]);
  assert.equal(secondComplete, false);
  firstSave.resolve();
  await first;
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(started, ["managed", "lmstudio-local"]);
  assert.equal(stored, "managed");
  assert.equal(secondComplete, false, "selection must not resolve before persistence");
  secondSave.resolve();
  await second;
  assert.equal(stored, "lmstudio-local");
});

test("a rejected default write is observable and does not poison the following selection", async () => {
  const enqueue = createPreferenceWriteQueue();
  const failure = deferred();
  let stored = "previous";
  const first = enqueue(() => failure.promise);
  const rejected = assert.rejects(first, /disk write failed/);
  const next = enqueue(async () => { stored = "lmstudio-local"; });
  failure.reject(new Error("disk write failed"));
  await rejected;
  await next;
  assert.equal(stored, "lmstudio-local");
});

test("preference mirroring cannot feed back into canonical persistence", async () => {
  const source = await readFile(new URL("../../apps/desktop/workbench/src/modules/ai/store/chatStore.ts", import.meta.url), "utf8");
  const bootstrap = await readFile(new URL("../../apps/desktop/workbench/src/modules/ai/hooks/useAiBootstrap.ts", import.meta.url), "utf8");
  const settings = await readFile(new URL("../../apps/desktop/workbench/src/modules/settings/store.ts", import.meta.url), "utf8");
  assert.match(source, /mirrorSelectedModelId: \(id\) => set\(\{ selectedModelId: id \}\)/);
  assert.match(source, /setSelectedModelId: async \(id\) => \{[\s\S]*?await setDefaultModel\(id as ModelId\)/);
  assert.match(bootstrap, /mirrorSelectedModelId\(prefDefaultModel\)/);
  assert.doesNotMatch(bootstrap, /setSelectedModelId\(prefDefaultModel\)/);
  assert.match(settings, /queueDefaultModelWrite\(\(\) => writePref\(KEY_DEFAULT_MODEL, value\)\)/);
});
