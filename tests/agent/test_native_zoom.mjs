import test from 'node:test';
import assert from 'node:assert/strict';
import { createZoomController } from '../../apps/desktop/src/nativeZoom.ts';

test('native zoom clamps invalid saved values and serializes rapid updates after errors', async () => {
  const calls = [];
  const zoom = createZoomController(async value => {
    calls.push(value);
    await new Promise(resolve => setTimeout(resolve, 1));
    if (value === 0.5) throw new Error('native failure');
  });
  const pending = [zoom(0), zoom(1.5), zoom(Infinity), zoom('2'), zoom(9)];
  const results = await Promise.allSettled(pending);
  assert.equal(results[0].status, 'rejected');
  assert.ok(results.slice(1).every(result => result.status === 'fulfilled'));
  assert.deepEqual(calls, [0.5, 1.5, 1, 1, 2]);
});

import { nativePlugin } from '../../apps/desktop/workbench/src/host/plugin.ts';
test('embedded plugins execute in parent realm while standalone calls retain native implementation', async () => {
  const previous = globalThis.window;
  const native = { isEnabled: async () => { throw new Error('iframe IPC unavailable'); } };
  const root = { __JARVIS_NATIVE__: { plugins: { autostart: { isEnabled: async () => true } } } };
  root.parent = root;
  try {
    globalThis.window = root;
    assert.equal(nativePlugin('autostart', native), native);
    globalThis.window = { parent: root };
    assert.equal(await nativePlugin('autostart', native).isEnabled(), true);
  } finally { globalThis.window = previous; }
});
