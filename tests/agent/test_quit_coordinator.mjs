import test from 'node:test';
import assert from 'node:assert/strict';
import { createQuitCoordinator } from '../../apps/desktop/src/quitCoordinator.ts';
function setup(overrides = {}) {
  const completions = [], failures = [], visits = [];
  const quit = createQuitCoordinator({ reveal: async () => visits.push('reveal'), snapshotDownloads: async () => [], confirmDownloads: async () => true, complete: async (id, approved, downloads) => completions.push([id, approved, downloads]), failed: error => failures.push(error), ...overrides });
  return { quit, completions, failures, visits };
}
test('quit fails closed until both guards have registered, and after guard failure', async () => {
  const { quit, completions, failures } = setup();
  await quit.request(1);
  quit.register('shell', async () => true);
  quit.register('workspace', async () => { throw new Error('IPC failed'); });
  await quit.request(2);
  assert.deepEqual(completions, [[1, false, []], [2, false, []]]);
  assert.equal(failures.length, 2);
});
test('cancel leaves app alive; a later request visits both guards and authorizes its own token', async () => {
  const { quit, completions, visits } = setup();
  quit.register('shell', async () => { visits.push('shell'); return true; });
  let approved = false;
  quit.register('workspace', async () => { visits.push('workspace'); return approved; });
  await quit.request(8); approved = true; await quit.request(9);
  assert.deepEqual(completions, [[8, false, []], [9, true, []]]);
  assert.deepEqual(visits, ['reveal','shell','workspace','reveal','shell','workspace']);
});
test('duplicate requests cannot bypass a pending prompt, and surface unload cancels authorization', async () => {
  const { quit, completions } = setup();
  const off = quit.register('shell', async () => true);
  let resolve;
  quit.register('workspace', () => new Promise(done => { resolve = done; }));
  const pending = quit.request(3);
  await new Promise(done => setImmediate(done));
  await quit.request(3); off(); resolve(true); await pending;
  assert.deepEqual(completions, [[3, false, []]]);
});
test('a task starting while workspace guard waits vetoes final authorization', async () => {
  const { quit, completions } = setup();
  let newlyBusy = false;
  quit.register('shell', async () => true, async () => !newlyBusy);
  quit.register('workspace', async () => { newlyBusy = true; return true; });
  await quit.request(4);
  assert.deepEqual(completions, [[4, false, []]]);
});
test('an unavailable final task query fails closed', async () => {
  const { quit, completions, failures } = setup();
  quit.register('shell', async () => true);
  quit.register('workspace', async () => true, async () => { throw new Error('terminal query failed'); });
  await quit.request(5);
  assert.deepEqual(completions, [[5, false, []]]);
  assert.equal(failures.length, 1);
});

test('download confirmation cancellation keeps the app alive; completed peers do not erase remaining activity', async () => {
  let approved = false, prompts = 0, active = [1, 2];
  const { quit, completions } = setup({ snapshotDownloads: async () => [...active], confirmDownloads: async () => { prompts++; active = [2]; return approved; } });
  quit.register('shell', async () => true);
  quit.register('workspace', async () => true);
  await quit.request(10);
  approved = true;
  await quit.request(11);
  active = [];
  await quit.request(12);
  assert.equal(prompts, 2);
  assert.deepEqual(completions, [[10, false, []], [11, true, [2]], [12, true, []]]);
});

test('a new download during another quit guard vetoes stale confirmation', async () => {
  let active = [1];
  const { quit, completions } = setup({ snapshotDownloads: async () => [...active] });
  quit.register('shell', async () => true);
  quit.register('workspace', async () => { active = [2]; return true; });
  await quit.request(13);
  assert.deepEqual(completions, [[13, false, []]]);
});

test('initial and final native download query failures both fail closed', async () => {
  for (const failAt of [1, 2]) {
    let queries = 0;
    const { quit, completions, failures } = setup({ snapshotDownloads: async () => { if (++queries === failAt) throw new Error('native state unavailable'); return []; } });
    quit.register('shell', async () => true);
    quit.register('workspace', async () => true);
    await quit.request(14);
    assert.deepEqual(completions, [[14, false, []]]);
    assert.equal(failures.length, 1);
  }
});

test('native completion rejecting a final registration race reports failure without retrying effects', async () => {
  let completed = 0;
  const { quit, failures } = setup({ complete: async () => { completed++; throw new Error('new download'); } });
  quit.register('shell', async () => true);
  quit.register('workspace', async () => true);
  await quit.request(15);
  assert.equal(completed, 1);
  assert.equal(failures.length, 1);
});

test('surface unload during final native download snapshot vetoes authorization', async () => {
  for (const role of ['shell', 'workspace']) {
    let queries = 0, resolveSnapshot;
    const { quit, completions } = setup({ snapshotDownloads: async () => {
      if (++queries === 1) return [];
      return new Promise(resolve => { resolveSnapshot = resolve; });
    } });
    const dispose = {
      shell: quit.register('shell', async () => true),
      workspace: quit.register('workspace', async () => true),
    };
    const pending = quit.request(16);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(queries, 2);
    dispose[role]();
    resolveSnapshot([]);
    await pending;
    assert.deepEqual(completions, [[16, false, []]]);
  }
});
