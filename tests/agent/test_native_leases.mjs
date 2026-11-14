import test from "node:test";
import assert from "node:assert/strict";
import { lease } from "../../apps/desktop/src/nativeLeases.ts";

test("reused iframe Window keeps independent document listener lifetimes", async () => {
  const owner = new EventTarget();
  owner.document = {};
  let finish;
  let oldReleased = 0, newReleased = 0;
  const old = lease(owner, new Promise(resolve => { finish = resolve; }));
  owner.dispatchEvent(new Event("pagehide"));
  owner.document = {};
  const releaseNew = await lease(owner, Promise.resolve(() => { newReleased++; }));
  finish(() => { oldReleased++; });
  await old;
  await Promise.resolve();
  assert.equal(oldReleased, 1);
  assert.equal(newReleased, 0);
  owner.dispatchEvent(new Event("pagehide"));
  await Promise.resolve();
  assert.equal(newReleased, 1);
  releaseNew();
  assert.equal(newReleased, 1);
});
